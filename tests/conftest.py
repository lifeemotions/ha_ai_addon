"""Shared fixtures for Life Emotions AI tests."""

import json
import sqlite3
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import requests
from aiohttp import web


@pytest.fixture
def tmp_dir(tmp_path):
    """Provide a temporary directory."""
    return tmp_path


@pytest.fixture
def ha_db(tmp_path):
    """Create a temporary Home Assistant-style SQLite database with test data."""
    db_path = str(tmp_path / "home-assistant_v2.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Create HA 2023.4+ schema tables
    cursor.executescript("""
        CREATE TABLE event_types (
            event_type_id INTEGER PRIMARY KEY,
            event_type TEXT NOT NULL
        );

        CREATE TABLE event_data (
            data_id INTEGER PRIMARY KEY,
            shared_data TEXT
        );

        CREATE TABLE events (
            event_id INTEGER PRIMARY KEY,
            event_type_id INTEGER,
            time_fired_ts REAL,
            origin_idx INTEGER,
            data_id INTEGER,
            FOREIGN KEY (event_type_id) REFERENCES event_types(event_type_id),
            FOREIGN KEY (data_id) REFERENCES event_data(data_id)
        );

        CREATE TABLE states_meta (
            metadata_id INTEGER PRIMARY KEY,
            entity_id TEXT NOT NULL
        );

        CREATE TABLE state_attributes (
            attributes_id INTEGER PRIMARY KEY,
            shared_attrs TEXT
        );

        CREATE TABLE states (
            state_id INTEGER PRIMARY KEY,
            metadata_id INTEGER,
            state TEXT,
            last_updated_ts REAL,
            last_changed_ts REAL,
            attributes_id INTEGER,
            FOREIGN KEY (metadata_id) REFERENCES states_meta(metadata_id),
            FOREIGN KEY (attributes_id) REFERENCES state_attributes(attributes_id)
        );
    """)

    # Insert test event data
    # Modern HA (2023.4+) no longer records state_changed in the events table.
    # State changes only go to the states table. The events table contains
    # system events like homeassistant_start, automation_triggered, call_service, etc.
    cursor.execute(
        "INSERT INTO event_types (event_type_id, event_type) VALUES (1, 'homeassistant_start')"
    )
    cursor.execute(
        "INSERT INTO event_types (event_type_id, event_type) VALUES (2, 'automation_triggered')"
    )
    cursor.execute(
        "INSERT INTO event_types (event_type_id, event_type) VALUES (3, 'call_service')"
    )
    cursor.execute(
        'INSERT INTO event_data (data_id, shared_data) VALUES (1, \'{}\')'
    )
    cursor.execute(
        'INSERT INTO event_data (data_id, shared_data) VALUES (2, \'{"entity_id": "automation.morning"}\')'
    )
    cursor.execute("INSERT INTO event_data (data_id, shared_data) VALUES (3, 'invalid json{{')")

    # Events: time_fired_ts is Unix timestamp
    # Event 1: 1705320000.0 (2024-01-15 12:00:00 UTC) - homeassistant_start
    # Event 2: 1705320060.0 (2024-01-15 12:01:00 UTC) - automation_triggered
    # Event 3: 1705320120.0 (2024-01-15 12:02:00 UTC) - call_service (invalid json data)
    cursor.execute(
        "INSERT INTO events (event_id, event_type_id, time_fired_ts, origin_idx, data_id) "
        "VALUES (1, 1, 1705320000.0, 0, 1)"
    )
    cursor.execute(
        "INSERT INTO events (event_id, event_type_id, time_fired_ts, origin_idx, data_id) "
        "VALUES (2, 2, 1705320060.0, 0, 2)"
    )
    cursor.execute(
        "INSERT INTO events (event_id, event_type_id, time_fired_ts, origin_idx, data_id) "
        "VALUES (3, 3, 1705320120.0, 0, 3)"
    )

    # Insert test state data
    cursor.execute(
        "INSERT INTO states_meta (metadata_id, entity_id) VALUES (1, 'light.living_room')"
    )
    cursor.execute(
        "INSERT INTO states_meta (metadata_id, entity_id) VALUES (2, 'sensor.temperature')"
    )
    cursor.execute(
        'INSERT INTO state_attributes (attributes_id, shared_attrs) VALUES (1, \'{"brightness": 255, "friendly_name": "Living Room Light"}\')'
    )
    cursor.execute(
        'INSERT INTO state_attributes (attributes_id, shared_attrs) VALUES (2, \'{"unit_of_measurement": "°C", "friendly_name": "Temperature"}\')'
    )
    cursor.execute("INSERT INTO state_attributes (attributes_id, shared_attrs) VALUES (3, 'bad json{{')")

    cursor.execute(
        "INSERT INTO states (state_id, metadata_id, state, last_updated_ts, last_changed_ts, attributes_id) "
        "VALUES (1, 1, 'on', 1705320000.0, 1705320000.0, 1)"
    )
    cursor.execute(
        "INSERT INTO states (state_id, metadata_id, state, last_updated_ts, last_changed_ts, attributes_id) "
        "VALUES (2, 2, '21.5', 1705320060.0, 1705320060.0, 2)"
    )
    cursor.execute(
        "INSERT INTO states (state_id, metadata_id, state, last_updated_ts, last_changed_ts, attributes_id) "
        "VALUES (3, 1, 'off', 1705320120.0, 1705320120.0, 3)"
    )

    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def sample_event_records():
    """Sample event records as returned by DatabaseReader.

    Modern HA (2023.4+) no longer records state_changed in the events table.
    Events contain system/automation events only.
    """
    return [
        {
            "id": 1,
            "type": "event",
            "timestamp": "2024-01-15T12:00:00+00:00",
            "raw_timestamp": 1705320000.0,
            "entity_id": "",
            "event_type": "homeassistant_start",
            "state": None,
            "attributes": {},
            "origin": "local",
        },
        {
            "id": 2,
            "type": "event",
            "timestamp": "2024-01-15T12:01:00+00:00",
            "raw_timestamp": 1705320060.0,
            "entity_id": "automation.morning",
            "event_type": "automation_triggered",
            "state": None,
            "attributes": {"entity_id": "automation.morning"},
            "origin": "local",
        },
    ]


@pytest.fixture
def sample_state_records():
    """Sample state records as returned by DatabaseReader."""
    return [
        {
            "id": 1,
            "type": "state",
            "timestamp": "2024-01-15T12:00:00+00:00",
            "raw_timestamp": 1705320000.0,
            "entity_id": "light.living_room",
            "event_type": "state_changed",
            "state": "on",
            "attributes": {"brightness": 255, "friendly_name": "Living Room Light"},
            "origin": "local",
        },
    ]


# ---------------------------------------------------------------------------
# Shared Docker fixtures (pytest-docker, used by HA Docker & E2E tests)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def docker_compose_file():
    """Point pytest-docker at the HA-specific compose file."""
    return str(Path(__file__).parent / "docker-compose.ha.yml")


def is_ha_responsive(url: str) -> bool:
    """Check whether HA has finished starting and is ready for onboarding."""
    try:
        resp = requests.get(f"{url}/api/onboarding", timeout=5)
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
# Mock Cloud API server (aiohttp.web)
# ---------------------------------------------------------------------------


class MockCloudApiState:
    """Tracks all requests received by the mock Cloud API server."""

    def __init__(self):
        self.received_batches: list[dict] = []
        self.checkpoint_calls: int = 0
        self.config_calls: int = 0
        self.checkpoint_timestamp: float = 0.0
        self.config_response: dict = {}

    def reset(self):
        self.received_batches.clear()
        self.checkpoint_calls = 0
        self.config_calls = 0
        self.checkpoint_timestamp = 0.0
        self.config_response = {}


def _create_mock_cloud_app(state: MockCloudApiState) -> web.Application:
    """Build an aiohttp.web application that mimics the Cloud API endpoints."""

    async def handle_get_data(request: web.Request) -> web.Response:
        """GET /ha/data — return checkpoint timestamp."""
        state.checkpoint_calls += 1
        return web.json_response({"last_timestamp": state.checkpoint_timestamp})

    async def handle_post_data(request: web.Request) -> web.Response:
        """POST /ha/data — accept a batch of records."""
        body = await request.json()
        state.received_batches.append(body)
        records = body.get("records", [])
        return web.json_response(
            {"records_received": len(records)},
            status=201,
        )

    async def handle_get_config(request: web.Request) -> web.Response:
        """GET /ha/config — return remote config."""
        state.config_calls += 1
        return web.json_response(state.config_response)

    app = web.Application()
    app.router.add_get("/ha/data", handle_get_data)
    app.router.add_post("/ha/data", handle_post_data)
    app.router.add_get("/ha/config", handle_get_config)
    return app


@pytest.fixture
async def mock_cloud_api():
    """Start a mock Cloud API server on a random port.

    Yields (base_url, state) where:
      - base_url is like "http://127.0.0.1:PORT/ha"
      - state is a MockCloudApiState for inspecting and configuring the server
    """
    state = MockCloudApiState()
    app = _create_mock_cloud_app(state)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)  # port 0 = OS picks a free port
    await site.start()

    # Extract the actual bound port
    sockets = site._server.sockets  # type: ignore[union-attr]
    port = sockets[0].getsockname()[1]
    base_url = f"http://127.0.0.1:{port}/ha"

    try:
        yield base_url, state
    finally:
        await runner.cleanup()
