"""End-to-end integration tests: HA Docker → addon sync → mock Cloud API.

Tests the full data pipeline:
  1. Real HA Core container writes events/states to SQLite
  2. DatabaseReader reads them from the copied DB
  3. CloudApiClient sends them to a mock Cloud API server
  4. EventExtractor.sync_cycle() orchestrates the full flow

Usage:
    .venv/bin/python -m pytest tests/test_integration_e2e.py -v

Requires Docker to be running.
"""

import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest
import requests

# Add addon subdirectory to path so we can import our addon code
sys.path.insert(0, str(Path(__file__).parent.parent / "lifeemotions_ai_addon"))
from main import CloudApiClient, DatabaseReader, EventExtractor

# ---------------------------------------------------------------------------
# Simulated HA activity
# ---------------------------------------------------------------------------

SIMULATED_ENTITIES = [
    {
        "entity_id": "sensor.e2e_temperature",
        "state": "23.1",
        "attributes": {
            "unit_of_measurement": "°C",
            "friendly_name": "E2E Temperature",
            "device_class": "temperature",
        },
    },
    {
        "entity_id": "light.e2e_living_room",
        "state": "on",
        "attributes": {
            "brightness": 180,
            "friendly_name": "E2E Living Room Light",
        },
    },
    {
        "entity_id": "switch.e2e_garage",
        "state": "off",
        "attributes": {
            "friendly_name": "E2E Garage Switch",
        },
    },
]


@pytest.fixture(scope="session")
def simulate_ha_activity(ha_base_url, ha_access_token):
    """Create simulated entities via the HA REST API."""
    headers = {"Authorization": f"Bearer {ha_access_token}"}

    for entity in SIMULATED_ENTITIES:
        resp = requests.post(
            f"{ha_base_url}/api/states/{entity['entity_id']}",
            headers=headers,
            json={
                "state": entity["state"],
                "attributes": entity["attributes"],
            },
            timeout=10,
        )
        assert resp.status_code in (200, 201), (
            f"Failed to create {entity['entity_id']}: {resp.status_code} {resp.text}"
        )

    # Give the Recorder time to flush to SQLite
    time.sleep(10)
    return SIMULATED_ENTITIES


@pytest.fixture(scope="session")
def ha_database_path(docker_compose_project_name, simulate_ha_activity, tmp_path_factory):
    """Copy the HA SQLite database out of the container to a local temp path."""
    container_name = f"{docker_compose_project_name}-homeassistant-1"
    dest_dir = tmp_path_factory.mktemp("ha_db_e2e")
    dest_path = dest_dir / "home-assistant_v2.db"

    # Wait for the Recorder to create the DB file
    db_found = False
    for _ in range(10):
        check = subprocess.run(
            ["docker", "exec", container_name, "test", "-f", "/config/home-assistant_v2.db"],
            capture_output=True,
        )
        if check.returncode == 0:
            db_found = True
            break
        time.sleep(2)
    assert db_found, "HA Recorder did not create the database file within timeout"

    # Copy the main DB file plus WAL/SHM files (SQLite WAL mode)
    for suffix in ("", "-wal", "-shm"):
        src = f"{container_name}:/config/home-assistant_v2.db{suffix}"
        dst = str(dest_dir / f"home-assistant_v2.db{suffix}")
        result = subprocess.run(
            ["docker", "cp", src, dst],
            capture_output=True,
            text=True,
        )
        if suffix == "":
            assert result.returncode == 0, f"docker cp failed: {result.stderr}"

    assert dest_path.exists(), "Database file was not copied"
    return str(dest_path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestEndToEndSyncCycle:
    """Full E2E: HA Docker → DatabaseReader → CloudApiClient → mock Cloud API."""

    @pytest.mark.asyncio
    async def test_full_sync_cycle(self, ha_database_path, mock_cloud_api):
        """EventExtractor.sync_cycle() reads from real HA DB and sends to mock API."""
        mock_url, state = mock_cloud_api
        state.reset()

        # Checkpoint at 0 means "sync everything"
        state.checkpoint_timestamp = 0.0

        # Build components pointing at real DB and mock API
        db_reader = DatabaseReader(db_path=ha_database_path)
        api_client = CloudApiClient(api_endpoint=mock_url, auth_token="e2e-test-token")

        # Construct EventExtractor with injected dependencies
        extractor = EventExtractor(db_reader=db_reader, api_client=api_client)
        extractor.model_manager.api_client = api_client

        # Run a single sync cycle
        await extractor.sync_cycle()

        # Clean up the aiohttp session
        await api_client.close()

        # --- Assertions ---

        # The mock API should have received the checkpoint check
        assert state.checkpoint_calls >= 1, "sync_cycle should fetch the checkpoint"

        # The mock API should have received at least one batch of records
        assert len(state.received_batches) > 0, (
            "sync_cycle should send at least one batch of records"
        )

        # Collect all records across all batches
        all_records = []
        for batch in state.received_batches:
            assert "records" in batch, "Each batch should have a 'records' key"
            assert "source" in batch, "Each batch should have a 'source' key"
            assert batch["source"] == "home_assistant"
            all_records.extend(batch["records"])

        assert len(all_records) > 0, "At least one record should have been synced"

        # Check that we have both event and state records
        record_types = {r["type"] for r in all_records}
        # States are guaranteed (we simulated entities); events depend on HA version
        assert "state" in record_types, "Should include state records from simulated entities"

        # Verify simulated entities appear in state records
        state_entity_ids = {r["entity_id"] for r in all_records if r["type"] == "state"}
        for entity in SIMULATED_ENTITIES:
            assert entity["entity_id"] in state_entity_ids, (
                f"Simulated entity {entity['entity_id']} should appear in synced state records"
            )

        # Verify record structure
        for record in all_records:
            assert "id" in record
            assert "type" in record
            assert record["type"] in ("event", "state")
            assert "timestamp" in record
            assert "raw_timestamp" in record
            assert isinstance(record["raw_timestamp"], (int, float))
            assert record["raw_timestamp"] > 0

    @pytest.mark.asyncio
    async def test_checkpoint_prevents_resync(self, ha_database_path, mock_cloud_api):
        """When checkpoint is far in the future, no records should be sent."""
        mock_url, state = mock_cloud_api
        state.reset()

        # Set checkpoint far in the future — no data should be newer than this
        state.checkpoint_timestamp = 9999999999.0

        db_reader = DatabaseReader(db_path=ha_database_path)
        api_client = CloudApiClient(api_endpoint=mock_url, auth_token="e2e-test-token")

        extractor = EventExtractor(db_reader=db_reader, api_client=api_client)
        extractor.model_manager.api_client = api_client

        await extractor.sync_cycle()
        await api_client.close()

        # Checkpoint was fetched
        assert state.checkpoint_calls >= 1

        # No batches should have been sent (all data is "before" the checkpoint)
        assert len(state.received_batches) == 0, (
            "No records should be sent when checkpoint is ahead of all data"
        )


@pytest.mark.integration
class TestCloudApiClientE2E:
    """Test CloudApiClient methods against the mock Cloud API server."""

    @pytest.mark.asyncio
    async def test_fetch_checkpoint(self, mock_cloud_api):
        """fetch_checkpoint() returns the timestamp from the mock server."""
        mock_url, state = mock_cloud_api
        state.reset()
        state.checkpoint_timestamp = 1706745600.123456

        client = CloudApiClient(api_endpoint=mock_url, auth_token="test-token")
        result = await client.fetch_checkpoint()
        await client.close()

        assert result == 1706745600.123456
        assert state.checkpoint_calls == 1

    @pytest.mark.asyncio
    async def test_fetch_config(self, mock_cloud_api):
        """fetch_config() returns the config from the mock server."""
        mock_url, state = mock_cloud_api
        state.reset()
        state.config_response = {
            "entity_filters": {"include_domains": ["light"]},
            "feature_flags": {"sync_states": True, "sync_events": True},
            "model_version": None,
            "prediction_schedule": "",
        }

        client = CloudApiClient(api_endpoint=mock_url, auth_token="test-token")
        config = await client.fetch_config()
        await client.close()

        assert config is not None
        assert config["entity_filters"] == {"include_domains": ["light"]}
        assert config["feature_flags"]["sync_states"] is True
        assert state.config_calls == 1

    @pytest.mark.asyncio
    async def test_send_batch(self, mock_cloud_api):
        """send_batch() posts records to the mock server."""
        mock_url, state = mock_cloud_api
        state.reset()

        records = [
            {
                "id": 1,
                "type": "state",
                "timestamp": "2024-01-15T12:00:00+00:00",
                "raw_timestamp": 1705320000.0,
                "entity_id": "light.test",
                "event_type": "state_changed",
                "state": "on",
                "attributes": {},
                "origin": "local",
            },
        ]

        client = CloudApiClient(api_endpoint=mock_url, auth_token="test-token")
        success = await client.send_batch(records)
        await client.close()

        assert success is True
        assert len(state.received_batches) == 1
        assert state.received_batches[0]["records"] == records
        assert state.received_batches[0]["source"] == "home_assistant"
        assert "sent_at" in state.received_batches[0]

    @pytest.mark.asyncio
    async def test_send_batch_auth_header(self, mock_cloud_api):
        """Verify that CloudApiClient sends the Authorization header."""
        mock_url, state = mock_cloud_api
        state.reset()

        client = CloudApiClient(api_endpoint=mock_url, auth_token="auth-test-token")
        await client.send_batch([{"id": 1, "type": "state", "timestamp": "2024-01-15T12:00:00+00:00",
                                   "raw_timestamp": 1705320000.0, "entity_id": "light.test",
                                   "event_type": "state_changed", "state": "on",
                                   "attributes": {}, "origin": "local"}])
        await client.close()

        assert any(h == "Bearer auth-test-token" for h in state.received_auth_headers)


@pytest.mark.integration
class TestErrorScenarios:
    """E2E tests for error scenarios."""

    @pytest.mark.asyncio
    @patch("main.RETRY_DELAY_SECONDS", 0)
    async def test_sync_cycle_handles_api_500(self, ha_db, mock_cloud_api):
        """sync_cycle() handles a 500 from the API gracefully (no crash)."""
        mock_url, state = mock_cloud_api
        state.reset()
        state.checkpoint_timestamp = 0.0
        state.force_error_status = 500

        db_reader = DatabaseReader(db_path=ha_db)
        api_client = CloudApiClient(api_endpoint=mock_url, auth_token="e2e-test-token")

        extractor = EventExtractor(db_reader=db_reader, api_client=api_client)
        extractor.model_manager.api_client = api_client

        # Should not raise — errors are handled gracefully
        await extractor.sync_cycle()
        await api_client.close()

        # Checkpoint was fetched successfully
        assert state.checkpoint_calls >= 1

        # No batches should have been accepted (all returned 500)
        assert len(state.received_batches) == 0
