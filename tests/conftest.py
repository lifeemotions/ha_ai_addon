"""Shared fixtures for Life Emotions AI tests."""

import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


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
    cursor.execute(
        "INSERT INTO event_types (event_type_id, event_type) VALUES (1, 'state_changed')"
    )
    cursor.execute(
        "INSERT INTO event_types (event_type_id, event_type) VALUES (2, 'automation_triggered')"
    )
    cursor.execute(
        'INSERT INTO event_data (data_id, shared_data) VALUES (1, \'{"entity_id": "light.living_room", "old_state": "off", "new_state": "on"}\')'
    )
    cursor.execute(
        'INSERT INTO event_data (data_id, shared_data) VALUES (2, \'{"entity_id": "automation.morning"}\')'
    )
    cursor.execute("INSERT INTO event_data (data_id, shared_data) VALUES (3, 'invalid json{{')")

    # Events: time_fired_ts is Unix timestamp
    # Event 1: 1705320000.0 (2024-01-15 12:00:00 UTC)
    # Event 2: 1705320060.0 (2024-01-15 12:01:00 UTC)
    # Event 3: 1705320120.0 (2024-01-15 12:02:00 UTC)
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
        "VALUES (3, 1, 1705320120.0, 0, 3)"
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
def ha_db_old_schema(tmp_path):
    """Create a temporary HA database with the older 2022.6+ schema (no event_types table)."""
    db_path = str(tmp_path / "home-assistant_v2.db")
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.executescript("""
        CREATE TABLE event_data (
            data_id INTEGER PRIMARY KEY,
            shared_data TEXT
        );

        CREATE TABLE events (
            event_id INTEGER PRIMARY KEY,
            event_type TEXT,
            time_fired_ts REAL,
            origin_idx INTEGER,
            data_id INTEGER,
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
            attributes_id INTEGER
        );
    """)

    cursor.execute(
        'INSERT INTO event_data (data_id, shared_data) VALUES (1, \'{"entity_id": "switch.garage"}\')'
    )
    cursor.execute(
        "INSERT INTO events (event_id, event_type, time_fired_ts, origin_idx, data_id) "
        "VALUES (1, 'state_changed', 1705320000.0, 0, 1)"
    )

    cursor.execute(
        "INSERT INTO states_meta (metadata_id, entity_id) VALUES (1, 'switch.garage')"
    )
    cursor.execute(
        'INSERT INTO state_attributes (attributes_id, shared_attrs) VALUES (1, \'{"friendly_name": "Garage"}\')'
    )
    cursor.execute(
        "INSERT INTO states (state_id, metadata_id, state, last_updated_ts, last_changed_ts, attributes_id) "
        "VALUES (1, 1, 'off', 1705320000.0, 1705320000.0, 1)"
    )

    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def sample_event_records():
    """Sample event records as returned by DatabaseReader."""
    return [
        {
            "id": 1,
            "type": "event",
            "timestamp": "2024-01-15T12:00:00+00:00",
            "raw_timestamp": 1705320000.0,
            "entity_id": "light.living_room",
            "event_type": "state_changed",
            "state": None,
            "attributes": {"entity_id": "light.living_room", "old_state": "off", "new_state": "on"},
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
