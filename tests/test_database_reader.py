"""Tests for DatabaseReader class."""

import sqlite3
from unittest.mock import patch

import pytest

from main import DatabaseReader


class TestDatabaseReaderInit:
    """Tests for DatabaseReader initialization."""

    def test_stores_db_path(self, ha_db):
        reader = DatabaseReader(db_path=ha_db)
        assert reader.db_path == ha_db

    def test_event_types_table_initially_none(self, ha_db):
        reader = DatabaseReader(db_path=ha_db)
        assert reader._has_event_types_table is None


class TestDatabaseReaderConnection:
    """Tests for DatabaseReader._get_connection()."""

    def test_get_connection_returns_connection(self, ha_db):
        reader = DatabaseReader(db_path=ha_db)
        conn = reader._get_connection()
        assert isinstance(conn, sqlite3.Connection)
        conn.close()

    def test_connection_uses_row_factory(self, ha_db):
        reader = DatabaseReader(db_path=ha_db)
        conn = reader._get_connection()
        assert conn.row_factory == sqlite3.Row
        conn.close()

    def test_connection_is_read_only(self, ha_db):
        reader = DatabaseReader(db_path=ha_db)
        conn = reader._get_connection()
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO event_types (event_type_id, event_type) VALUES (99, 'test')")
        conn.close()

    def test_connection_fails_for_nonexistent_db(self, tmp_path):
        reader = DatabaseReader(db_path=str(tmp_path / "nonexistent.db"))
        with pytest.raises(sqlite3.OperationalError):
            reader._get_connection()


class TestCheckEventTypesTable:
    """Tests for DatabaseReader._check_event_types_table()."""

    def test_detects_new_schema(self, ha_db):
        reader = DatabaseReader(db_path=ha_db)
        conn = reader._get_connection()
        assert reader._check_event_types_table(conn) is True
        conn.close()

    def test_detects_old_schema(self, ha_db_old_schema):
        reader = DatabaseReader(db_path=ha_db_old_schema)
        conn = reader._get_connection()
        assert reader._check_event_types_table(conn) is False
        conn.close()

    def test_caches_result(self, ha_db):
        reader = DatabaseReader(db_path=ha_db)
        conn = reader._get_connection()
        reader._check_event_types_table(conn)
        assert reader._has_event_types_table is True
        reader._check_event_types_table(conn)
        assert reader._has_event_types_table is True
        conn.close()


class TestFetchEvents:
    """Tests for DatabaseReader.fetch_events() with timestamp-based filtering."""

    def test_fetches_all_events_from_zero(self, ha_db):
        reader = DatabaseReader(db_path=ha_db)
        events = reader.fetch_events(after_timestamp=0.0)
        assert len(events) == 3

    def test_fetches_events_after_timestamp(self, ha_db):
        reader = DatabaseReader(db_path=ha_db)
        events = reader.fetch_events(after_timestamp=1705320000.0)
        assert len(events) == 2
        assert events[0]["raw_timestamp"] == 1705320060.0
        assert events[1]["raw_timestamp"] == 1705320120.0

    def test_fetches_events_between_timestamps(self, ha_db):
        reader = DatabaseReader(db_path=ha_db)
        events = reader.fetch_events(after_timestamp=1705320060.0)
        assert len(events) == 1
        assert events[0]["raw_timestamp"] == 1705320120.0

    def test_returns_empty_when_no_new_events(self, ha_db):
        reader = DatabaseReader(db_path=ha_db)
        events = reader.fetch_events(after_timestamp=9999999999.0)
        assert events == []

    def test_respects_batch_size(self, ha_db):
        reader = DatabaseReader(db_path=ha_db)
        events = reader.fetch_events(after_timestamp=0.0, batch_size=1)
        assert len(events) == 1
        assert events[0]["raw_timestamp"] == 1705320000.0

    def test_event_structure(self, ha_db):
        reader = DatabaseReader(db_path=ha_db)
        events = reader.fetch_events(after_timestamp=0.0, batch_size=1)
        event = events[0]
        assert event["type"] == "event"
        assert event["event_type"] == "state_changed"
        assert event["entity_id"] == "light.living_room"
        assert event["origin"] == "local"
        assert event["state"] is None
        assert "timestamp" in event
        assert "raw_timestamp" in event
        assert isinstance(event["raw_timestamp"], float)
        assert isinstance(event["attributes"], dict)

    def test_event_with_invalid_json_data(self, ha_db):
        """Event with invalid JSON event_data should still parse (empty attributes)."""
        reader = DatabaseReader(db_path=ha_db)
        events = reader.fetch_events(after_timestamp=1705320060.0, batch_size=1)
        assert len(events) == 1
        assert events[0]["attributes"] == {}

    def test_fetches_events_old_schema(self, ha_db_old_schema):
        reader = DatabaseReader(db_path=ha_db_old_schema)
        events = reader.fetch_events(after_timestamp=0.0)
        assert len(events) == 1
        assert events[0]["event_type"] == "state_changed"
        assert events[0]["raw_timestamp"] == 1705320000.0

    def test_returns_empty_on_db_error(self, tmp_path):
        reader = DatabaseReader(db_path=str(tmp_path / "nonexistent.db"))
        events = reader.fetch_events(after_timestamp=0.0)
        assert events == []

    def test_handles_database_locked_error(self, ha_db):
        reader = DatabaseReader(db_path=ha_db)
        with patch.object(reader, "_get_connection") as mock_conn:
            mock_conn.side_effect = sqlite3.OperationalError("database is locked")
            events = reader.fetch_events(after_timestamp=0.0)
        assert events == []

    def test_handles_database_busy_error(self, ha_db):
        reader = DatabaseReader(db_path=ha_db)
        with patch.object(reader, "_get_connection") as mock_conn:
            mock_conn.side_effect = sqlite3.OperationalError("database is busy")
            events = reader.fetch_events(after_timestamp=0.0)
        assert events == []

    def test_events_ordered_by_timestamp_ascending(self, ha_db):
        reader = DatabaseReader(db_path=ha_db)
        events = reader.fetch_events(after_timestamp=0.0)
        timestamps = [e["raw_timestamp"] for e in events]
        assert timestamps == sorted(timestamps)

    def test_timestamp_is_iso_format(self, ha_db):
        reader = DatabaseReader(db_path=ha_db)
        events = reader.fetch_events(after_timestamp=0.0, batch_size=1)
        ts = events[0]["timestamp"]
        assert "T" in ts
        assert "+00:00" in ts

    def test_raw_timestamp_matches_db_value(self, ha_db):
        reader = DatabaseReader(db_path=ha_db)
        events = reader.fetch_events(after_timestamp=0.0, batch_size=1)
        assert events[0]["raw_timestamp"] == 1705320000.0


class TestFetchStates:
    """Tests for DatabaseReader.fetch_states() with timestamp-based filtering."""

    def test_fetches_all_states_from_zero(self, ha_db):
        reader = DatabaseReader(db_path=ha_db)
        states = reader.fetch_states(after_timestamp=0.0)
        assert len(states) == 3

    def test_fetches_states_after_timestamp(self, ha_db):
        reader = DatabaseReader(db_path=ha_db)
        states = reader.fetch_states(after_timestamp=1705320060.0)
        assert len(states) == 1
        assert states[0]["raw_timestamp"] == 1705320120.0

    def test_returns_empty_when_no_new_states(self, ha_db):
        reader = DatabaseReader(db_path=ha_db)
        states = reader.fetch_states(after_timestamp=9999999999.0)
        assert states == []

    def test_respects_batch_size(self, ha_db):
        reader = DatabaseReader(db_path=ha_db)
        states = reader.fetch_states(after_timestamp=0.0, batch_size=2)
        assert len(states) == 2

    def test_state_structure(self, ha_db):
        reader = DatabaseReader(db_path=ha_db)
        states = reader.fetch_states(after_timestamp=0.0, batch_size=1)
        state = states[0]
        assert state["type"] == "state"
        assert state["event_type"] == "state_changed"
        assert state["entity_id"] == "light.living_room"
        assert state["state"] == "on"
        assert state["origin"] == "local"
        assert "timestamp" in state
        assert "raw_timestamp" in state
        assert isinstance(state["raw_timestamp"], float)
        assert state["attributes"]["brightness"] == 255

    def test_state_with_invalid_json_attributes(self, ha_db):
        """State with invalid JSON attributes should still parse (empty attributes)."""
        reader = DatabaseReader(db_path=ha_db)
        states = reader.fetch_states(after_timestamp=1705320060.0, batch_size=1)
        assert len(states) == 1
        assert states[0]["attributes"] == {}

    def test_returns_empty_on_db_error(self, tmp_path):
        reader = DatabaseReader(db_path=str(tmp_path / "nonexistent.db"))
        states = reader.fetch_states(after_timestamp=0.0)
        assert states == []

    def test_handles_database_locked_error(self, ha_db):
        reader = DatabaseReader(db_path=ha_db)
        with patch.object(reader, "_get_connection") as mock_conn:
            mock_conn.side_effect = sqlite3.OperationalError("database is locked")
            states = reader.fetch_states(after_timestamp=0.0)
        assert states == []

    def test_states_ordered_by_timestamp_ascending(self, ha_db):
        reader = DatabaseReader(db_path=ha_db)
        states = reader.fetch_states(after_timestamp=0.0)
        timestamps = [s["raw_timestamp"] for s in states]
        assert timestamps == sorted(timestamps)

    def test_raw_timestamp_matches_db_value(self, ha_db):
        reader = DatabaseReader(db_path=ha_db)
        states = reader.fetch_states(after_timestamp=0.0, batch_size=1)
        assert states[0]["raw_timestamp"] == 1705320000.0


class TestParseEventRow:
    """Tests for DatabaseReader._parse_event_row()."""

    def test_parse_valid_event_row(self, ha_db):
        reader = DatabaseReader(db_path=ha_db)
        conn = reader._get_connection()
        cursor = conn.cursor()
        reader._check_event_types_table(conn)
        cursor.execute("""
            SELECT e.event_id, et.event_type, e.time_fired_ts, e.origin_idx,
                   ed.shared_data as event_data
            FROM events e
            LEFT JOIN event_types et ON e.event_type_id = et.event_type_id
            LEFT JOIN event_data ed ON e.data_id = ed.data_id
            WHERE e.event_id = 1
        """)
        row = cursor.fetchone()
        result = reader._parse_event_row(row)
        assert result is not None
        assert result["id"] == 1
        assert result["type"] == "event"
        assert result["event_type"] == "state_changed"
        assert result["raw_timestamp"] == 1705320000.0
        conn.close()

    def test_parse_event_with_null_data(self, ha_db):
        """Event with NULL event_data should return empty attributes."""
        reader = DatabaseReader(db_path=ha_db)
        conn = sqlite3.connect(ha_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO events (event_id, event_type_id, time_fired_ts, origin_idx, data_id) "
            "VALUES (99, 1, 1705320000.0, 0, NULL)"
        )
        conn.commit()
        conn_ro = reader._get_connection()
        cursor_ro = conn_ro.cursor()
        cursor_ro.execute("""
            SELECT e.event_id, et.event_type, e.time_fired_ts, e.origin_idx,
                   ed.shared_data as event_data
            FROM events e
            LEFT JOIN event_types et ON e.event_type_id = et.event_type_id
            LEFT JOIN event_data ed ON e.data_id = ed.data_id
            WHERE e.event_id = 99
        """)
        row = cursor_ro.fetchone()
        result = reader._parse_event_row(row)
        assert result is not None
        assert result["attributes"] == {}
        conn.close()
        conn_ro.close()


class TestParseStateRow:
    """Tests for DatabaseReader._parse_state_row()."""

    def test_parse_valid_state_row(self, ha_db):
        reader = DatabaseReader(db_path=ha_db)
        conn = reader._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT s.state_id, sm.entity_id, s.state, s.last_updated_ts,
                   s.last_changed_ts, sa.shared_attrs as attributes
            FROM states s
            LEFT JOIN states_meta sm ON s.metadata_id = sm.metadata_id
            LEFT JOIN state_attributes sa ON s.attributes_id = sa.attributes_id
            WHERE s.state_id = 1
        """)
        row = cursor.fetchone()
        result = reader._parse_state_row(row)
        assert result is not None
        assert result["id"] == 1
        assert result["type"] == "state"
        assert result["entity_id"] == "light.living_room"
        assert result["state"] == "on"
        assert result["raw_timestamp"] == 1705320000.0
        conn.close()

    def test_parse_state_with_null_entity_id(self, ha_db):
        """State with NULL entity_id should return empty string."""
        reader = DatabaseReader(db_path=ha_db)
        conn = sqlite3.connect(ha_db)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO states (state_id, metadata_id, state, last_updated_ts, last_changed_ts, attributes_id) "
            "VALUES (99, NULL, 'on', 1705320000.0, 1705320000.0, 1)"
        )
        conn.commit()
        conn_ro = reader._get_connection()
        cursor_ro = conn_ro.cursor()
        cursor_ro.execute("""
            SELECT s.state_id, sm.entity_id, s.state, s.last_updated_ts,
                   s.last_changed_ts, sa.shared_attrs as attributes
            FROM states s
            LEFT JOIN states_meta sm ON s.metadata_id = sm.metadata_id
            LEFT JOIN state_attributes sa ON s.attributes_id = sa.attributes_id
            WHERE s.state_id = 99
        """)
        row = cursor_ro.fetchone()
        result = reader._parse_state_row(row)
        assert result is not None
        assert result["entity_id"] == ""
        conn.close()
        conn_ro.close()
