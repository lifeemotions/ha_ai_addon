"""Integration tests that run against a real Home Assistant Docker container.

Usage:
    .venv/bin/python -m pytest tests/test_integration_ha_docker.py -v

Requires Docker to be running.
"""

import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

# Add addon subdirectory to path so we can import our addon code
sys.path.insert(0, str(Path(__file__).parent.parent / "lifeemotions_ai_addon"))
from main import DatabaseReader

# ---------------------------------------------------------------------------
# pytest-docker configuration
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def docker_compose_file():
    """Point pytest-docker at our HA-specific compose file."""
    return str(Path(__file__).parent / "docker-compose.ha.yml")


def is_ha_responsive(url: str) -> bool:
    """Check whether HA has finished starting and is ready for onboarding."""
    try:
        resp = requests.get(f"{url}/api/onboarding", timeout=5)
        # HA returns 200 with a JSON list of onboarding steps when not yet onboarded
        return resp.status_code == 200
    except (requests.ConnectionError, requests.Timeout):
        return False


@pytest.fixture(scope="session")
def ha_base_url(docker_ip, docker_services):
    """Wait for HA to be responsive and return its base URL."""
    port = docker_services.port_for("homeassistant", 8123)
    url = f"http://{docker_ip}:{port}"
    docker_services.wait_until_responsive(
        timeout=120.0,
        pause=3.0,
        check=lambda: is_ha_responsive(url),
    )
    return url


@pytest.fixture(scope="session")
def ha_access_token(ha_base_url):
    """Complete onboarding programmatically and return an access token."""
    # Step 1 – create the owner account
    resp = requests.post(
        f"{ha_base_url}/api/onboarding/users",
        json={
            "client_id": f"{ha_base_url}/",
            "name": "Test Admin",
            "username": "admin",
            "password": "testpassword123",
            "language": "en",
        },
        timeout=30,
    )
    resp.raise_for_status()
    auth_code = resp.json()["auth_code"]

    # Step 2 – exchange auth code for tokens
    resp = requests.post(
        f"{ha_base_url}/auth/token",
        data={
            "grant_type": "authorization_code",
            "code": auth_code,
            "client_id": f"{ha_base_url}/",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


# ---------------------------------------------------------------------------
# Simulated HA activity
# ---------------------------------------------------------------------------

SIMULATED_ENTITIES = [
    {
        "entity_id": "sensor.test_temperature",
        "state": "22.5",
        "attributes": {
            "unit_of_measurement": "°C",
            "friendly_name": "Test Temperature",
            "device_class": "temperature",
        },
    },
    {
        "entity_id": "light.test_living_room",
        "state": "on",
        "attributes": {
            "brightness": 200,
            "friendly_name": "Test Living Room Light",
        },
    },
    {
        "entity_id": "switch.test_garage",
        "state": "off",
        "attributes": {
            "friendly_name": "Test Garage Switch",
        },
    },
]


@pytest.fixture(scope="session")
def simulate_ha_activity(ha_base_url, ha_access_token):
    """Create simulated entities via the HA REST API.

    POST /api/states/<entity_id> causes HA Recorder to write both a state row
    and a state_changed event into its SQLite database.
    """
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
    # The Recorder commits in batches, so we need to wait longer
    time.sleep(10)

    return SIMULATED_ENTITIES


@pytest.fixture(scope="session")
def ha_database_path(docker_compose_project_name, simulate_ha_activity, tmp_path_factory):
    """Copy the HA SQLite database out of the container to a local temp path."""
    container_name = f"{docker_compose_project_name}-homeassistant-1"
    dest_dir = tmp_path_factory.mktemp("ha_db")
    dest_path = dest_dir / "home-assistant_v2.db"

    # Wait for the Recorder to create the DB file (it may take a moment)
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
        # The main .db file must exist; -wal and -shm are optional
        if suffix == "":
            assert result.returncode == 0, f"docker cp failed: {result.stderr}"

    assert dest_path.exists(), "Database file was not copied"

    return str(dest_path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestHomeAssistantIsRunning:
    """Verify the HA container is up, onboarded, and the API is reachable."""

    def test_api_is_reachable(self, ha_base_url, ha_access_token):
        """GET /api/ should return 200 with a welcome message."""
        resp = requests.get(
            f"{ha_base_url}/api/",
            headers={"Authorization": f"Bearer {ha_access_token}"},
            timeout=10,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "message" in body
        assert body["message"] == "API running."


@pytest.mark.integration
class TestDatabaseReaderWithRealHA:
    """Test our addon's DatabaseReader against a real HA Recorder database."""

    def test_fetch_states_from_real_ha_db(self, ha_database_path):
        """DatabaseReader should parse states written by a real HA Recorder."""
        reader = DatabaseReader(db_path=ha_database_path)
        states = reader.fetch_states(after_timestamp=0, batch_size=500)

        assert len(states) > 0, "No states found in real HA database"

        # Check that our simulated entities are present
        entity_ids = {s["entity_id"] for s in states}
        for entity in SIMULATED_ENTITIES:
            assert entity["entity_id"] in entity_ids, (
                f"Simulated entity {entity['entity_id']} not found in states"
            )

        # Verify one entity in detail
        temp_states = [s for s in states if s["entity_id"] == "sensor.test_temperature"]
        assert len(temp_states) >= 1
        temp = temp_states[-1]  # most recent
        assert temp["state"] == "22.5"
        assert temp["type"] == "state"
        assert temp["event_type"] == "state_changed"
        assert temp["raw_timestamp"] > 0
        assert temp["attributes"]["friendly_name"] == "Test Temperature"
        assert temp["attributes"]["unit_of_measurement"] == "°C"

    def test_fetch_events_from_real_ha_db(self, ha_database_path):
        """DatabaseReader should parse events written by a real HA Recorder."""
        reader = DatabaseReader(db_path=ha_database_path)
        events = reader.fetch_events(after_timestamp=0, batch_size=500)

        assert len(events) > 0, "No events found in real HA database"

        # Verify event structure matches what our addon expects
        for event in events:
            assert event["type"] == "event"
            assert event["raw_timestamp"] > 0
            assert event["timestamp"]  # ISO format string
            assert event["origin"] == "local"
            assert event["event_type"]  # must have an event type

        # Collect all event types we found (for diagnostic visibility)
        event_types = {e["event_type"] for e in events}
        assert len(event_types) > 0, "Events should have at least one event type"

