"""Tests for DataProcessor class."""

import pytest

from main import DataProcessor


class TestEntityFiltering:
    """Tests for Tier 1: entity filtering."""

    def test_no_filters_keeps_all(self):
        dp = DataProcessor()
        states = [
            {"entity_id": "sensor.temperature", "raw_timestamp": 1.0, "state": "22.5"},
            {"entity_id": "light.kitchen", "raw_timestamp": 2.0, "state": "on"},
        ]
        result = dp.process_states(states)
        assert len(result) == 2

    def test_hardcoded_system_monitor_excluded(self):
        dp = DataProcessor()
        states = [
            {"entity_id": "sensor.system_monitor_memory_use", "raw_timestamp": 1.0, "state": "512"},
            {"entity_id": "sensor.system_monitor_load_1m", "raw_timestamp": 2.0, "state": "0.5"},
            {"entity_id": "sensor.temperature", "raw_timestamp": 3.0, "state": "22.5"},
        ]
        result = dp.process_states(states)
        assert len(result) == 1
        assert result[0]["entity_id"] == "sensor.temperature"

    def test_hardcoded_knx_interface_excluded(self):
        dp = DataProcessor()
        states = [
            {"entity_id": "sensor.knx_interface_telegrams", "raw_timestamp": 1.0, "state": "1234"},
            {"entity_id": "sensor.temperature", "raw_timestamp": 2.0, "state": "22.5"},
        ]
        result = dp.process_states(states)
        assert len(result) == 1
        assert result[0]["entity_id"] == "sensor.temperature"

    def test_hardcoded_exclusions_apply_to_events(self):
        dp = DataProcessor()
        events = [
            {"entity_id": "sensor.system_monitor_cpu", "raw_timestamp": 1.0},
            {"entity_id": "automation.morning", "raw_timestamp": 2.0},
        ]
        result = dp.process_events(events)
        assert len(result) == 1
        assert result[0]["entity_id"] == "automation.morning"

    def test_include_domains_filter(self):
        dp = DataProcessor()
        dp.update_filters({"entity_filters": {
            "include_domains": ["sensor", "light"],
            "exclude_entities": [],
        }})
        states = [
            {"entity_id": "sensor.temperature", "raw_timestamp": 1.0, "state": "22.5"},
            {"entity_id": "light.kitchen", "raw_timestamp": 2.0, "state": "on"},
            {"entity_id": "switch.pump", "raw_timestamp": 3.0, "state": "off"},
        ]
        result = dp.process_states(states)
        entity_ids = [r["entity_id"] for r in result]
        assert "sensor.temperature" in entity_ids
        assert "light.kitchen" in entity_ids
        assert "switch.pump" not in entity_ids

    def test_exclude_entities_glob(self):
        dp = DataProcessor()
        dp.update_filters({"entity_filters": {
            "include_domains": [],
            "exclude_entities": ["sensor.qe_geral_*"],
        }})
        states = [
            {"entity_id": "sensor.qe_geral_channel_a_power", "raw_timestamp": 1.0, "state": "230"},
            {"entity_id": "sensor.qe_geral_channel_b_power", "raw_timestamp": 2.0, "state": "115"},
            {"entity_id": "sensor.temperature", "raw_timestamp": 3.0, "state": "22.5"},
        ]
        result = dp.process_states(states)
        assert len(result) == 1
        assert result[0]["entity_id"] == "sensor.temperature"

    def test_include_domains_and_exclude_entities_combined(self):
        dp = DataProcessor()
        dp.update_filters({"entity_filters": {
            "include_domains": ["sensor"],
            "exclude_entities": ["sensor.uptime"],
        }})
        states = [
            {"entity_id": "sensor.temperature", "raw_timestamp": 1.0, "state": "22.5"},
            {"entity_id": "sensor.uptime", "raw_timestamp": 2.0, "state": "12345"},
            {"entity_id": "light.kitchen", "raw_timestamp": 3.0, "state": "on"},
        ]
        result = dp.process_states(states)
        assert len(result) == 1
        assert result[0]["entity_id"] == "sensor.temperature"

    def test_empty_entity_id_passes_through(self):
        dp = DataProcessor()
        events = [
            {"entity_id": "", "raw_timestamp": 1.0},
            {"entity_id": "sensor.system_monitor_cpu", "raw_timestamp": 2.0},
        ]
        result = dp.process_events(events)
        assert len(result) == 1
        assert result[0]["entity_id"] == ""

    def test_all_filtered_returns_empty(self):
        dp = DataProcessor()
        states = [
            {"entity_id": "sensor.system_monitor_cpu", "raw_timestamp": 1.0, "state": "50"},
            {"entity_id": "sensor.system_monitor_memory_use", "raw_timestamp": 2.0, "state": "512"},
        ]
        result = dp.process_states(states)
        assert result == []

    def test_no_entity_filters_in_config(self):
        dp = DataProcessor()
        dp.update_filters({"feature_flags": {"sync_states": True}})
        states = [
            {"entity_id": "sensor.temperature", "raw_timestamp": 1.0, "state": "22.5"},
        ]
        result = dp.process_states(states)
        assert len(result) == 1


class TestNumericAggregation:
    """Tests for Tier 2: numeric state aggregation."""

    def test_numeric_states_aggregated_same_bucket(self):
        dp = DataProcessor()
        # All within the same 5-min bucket (0-300)
        states = [
            {"entity_id": "sensor.temp", "raw_timestamp": 100.0, "state": "20.0",
             "event_type": "state_changed", "attributes": {"unit": "C"}, "origin": "local"},
            {"entity_id": "sensor.temp", "raw_timestamp": 200.0, "state": "22.0",
             "event_type": "state_changed", "attributes": {"unit": "C"}, "origin": "local"},
            {"entity_id": "sensor.temp", "raw_timestamp": 250.0, "state": "24.0",
             "event_type": "state_changed", "attributes": {"unit": "C"}, "origin": "local"},
        ]
        result = dp.process_states(states)
        assert len(result) == 1
        assert result[0]["entity_id"] == "sensor.temp"
        assert result[0]["state"] == "22.0"  # avg of 20, 22, 24
        assert result[0]["raw_timestamp"] == 250.0  # max

    def test_non_numeric_passes_through(self):
        dp = DataProcessor()
        states = [
            {"entity_id": "light.kitchen", "raw_timestamp": 100.0, "state": "on",
             "event_type": "state_changed", "attributes": {}, "origin": "local"},
            {"entity_id": "light.kitchen", "raw_timestamp": 200.0, "state": "off",
             "event_type": "state_changed", "attributes": {}, "origin": "local"},
        ]
        result = dp.process_states(states)
        assert len(result) == 2
        assert result[0]["state"] == "on"
        assert result[1]["state"] == "off"

    def test_different_entities_separate_buckets(self):
        dp = DataProcessor()
        states = [
            {"entity_id": "sensor.temp_a", "raw_timestamp": 100.0, "state": "20.0",
             "event_type": "state_changed", "attributes": {}, "origin": "local"},
            {"entity_id": "sensor.temp_b", "raw_timestamp": 100.0, "state": "25.0",
             "event_type": "state_changed", "attributes": {}, "origin": "local"},
            {"entity_id": "sensor.temp_a", "raw_timestamp": 200.0, "state": "22.0",
             "event_type": "state_changed", "attributes": {}, "origin": "local"},
            {"entity_id": "sensor.temp_b", "raw_timestamp": 200.0, "state": "27.0",
             "event_type": "state_changed", "attributes": {}, "origin": "local"},
        ]
        result = dp.process_states(states)
        assert len(result) == 2
        result_by_entity = {r["entity_id"]: r for r in result}
        assert result_by_entity["sensor.temp_a"]["state"] == "21.0"
        assert result_by_entity["sensor.temp_b"]["state"] == "26.0"

    def test_different_time_buckets(self):
        dp = DataProcessor()
        # Bucket 0 (0-300) and bucket 1 (300-600)
        states = [
            {"entity_id": "sensor.temp", "raw_timestamp": 100.0, "state": "20.0",
             "event_type": "state_changed", "attributes": {}, "origin": "local"},
            {"entity_id": "sensor.temp", "raw_timestamp": 200.0, "state": "22.0",
             "event_type": "state_changed", "attributes": {}, "origin": "local"},
            {"entity_id": "sensor.temp", "raw_timestamp": 400.0, "state": "24.0",
             "event_type": "state_changed", "attributes": {}, "origin": "local"},
            {"entity_id": "sensor.temp", "raw_timestamp": 500.0, "state": "26.0",
             "event_type": "state_changed", "attributes": {}, "origin": "local"},
        ]
        result = dp.process_states(states)
        assert len(result) == 2
        assert result[0]["state"] == "21.0"  # avg(20, 22), bucket 0
        assert result[0]["raw_timestamp"] == 200.0
        assert result[1]["state"] == "25.0"  # avg(24, 26), bucket 1
        assert result[1]["raw_timestamp"] == 500.0

    def test_mixed_numeric_and_non_numeric(self):
        dp = DataProcessor()
        states = [
            {"entity_id": "sensor.temp", "raw_timestamp": 100.0, "state": "20.0",
             "event_type": "state_changed", "attributes": {}, "origin": "local"},
            {"entity_id": "light.kitchen", "raw_timestamp": 150.0, "state": "on",
             "event_type": "state_changed", "attributes": {}, "origin": "local"},
            {"entity_id": "sensor.temp", "raw_timestamp": 200.0, "state": "22.0",
             "event_type": "state_changed", "attributes": {}, "origin": "local"},
        ]
        result = dp.process_states(states)
        assert len(result) == 2  # 1 aggregated + 1 non-numeric
        non_numeric = [r for r in result if r["entity_id"] == "light.kitchen"]
        numeric = [r for r in result if r["entity_id"] == "sensor.temp"]
        assert len(non_numeric) == 1
        assert non_numeric[0]["state"] == "on"
        assert len(numeric) == 1
        assert numeric[0]["state"] == "21.0"

    def test_aggregated_record_format(self):
        dp = DataProcessor()
        states = [
            {"entity_id": "sensor.power", "raw_timestamp": 100.0, "state": "230.5",
             "event_type": "state_changed", "attributes": {"unit_of_measurement": "W"}, "origin": "local"},
            {"entity_id": "sensor.power", "raw_timestamp": 200.0, "state": "231.5",
             "event_type": "state_changed", "attributes": {"unit_of_measurement": "W"}, "origin": "local"},
        ]
        result = dp.process_states(states)
        assert len(result) == 1
        record = result[0]
        assert record["type"] == "state"
        assert record["event_type"] == "state_changed"
        assert record["origin"] == "local"
        assert record["raw_timestamp"] == 200.0
        assert record["attributes"] == {"unit_of_measurement": "W"}
        assert "id" not in record

    def test_output_sorted_by_timestamp(self):
        dp = DataProcessor()
        states = [
            {"entity_id": "sensor.temp", "raw_timestamp": 100.0, "state": "20.0",
             "event_type": "state_changed", "attributes": {}, "origin": "local"},
            {"entity_id": "light.kitchen", "raw_timestamp": 50.0, "state": "on",
             "event_type": "state_changed", "attributes": {}, "origin": "local"},
        ]
        result = dp.process_states(states)
        timestamps = [r["raw_timestamp"] for r in result]
        assert timestamps == sorted(timestamps)

    def test_unavailable_unknown_not_aggregated(self):
        dp = DataProcessor()
        states = [
            {"entity_id": "sensor.temp", "raw_timestamp": 100.0, "state": "unavailable",
             "event_type": "state_changed", "attributes": {}, "origin": "local"},
            {"entity_id": "sensor.temp", "raw_timestamp": 200.0, "state": "unknown",
             "event_type": "state_changed", "attributes": {}, "origin": "local"},
            {"entity_id": "sensor.temp", "raw_timestamp": 250.0, "state": "22.5",
             "event_type": "state_changed", "attributes": {}, "origin": "local"},
        ]
        result = dp.process_states(states)
        assert len(result) == 3  # 2 non-numeric pass through + 1 numeric
        assert result[0]["state"] == "unavailable"
        assert result[1]["state"] == "unknown"
        assert result[2]["state"] == "22.5"

    def test_single_numeric_reading_still_aggregated(self):
        dp = DataProcessor()
        states = [
            {"entity_id": "sensor.temp", "raw_timestamp": 100.0, "state": "22.5",
             "event_type": "state_changed", "attributes": {"unit": "C"}, "origin": "local"},
        ]
        result = dp.process_states(states)
        assert len(result) == 1
        assert result[0]["state"] == "22.5"
        assert result[0]["raw_timestamp"] == 100.0

    def test_none_state_passes_through(self):
        dp = DataProcessor()
        states = [
            {"entity_id": "sensor.temp", "raw_timestamp": 100.0, "state": None,
             "event_type": "state_changed", "attributes": {}, "origin": "local"},
        ]
        result = dp.process_states(states)
        assert len(result) == 1
        assert result[0]["state"] is None


class TestConfigUpdate:
    """Tests for DataProcessor.update_filters()."""

    def test_update_sets_filters(self):
        dp = DataProcessor()
        dp.update_filters({
            "entity_filters": {
                "include_domains": ["sensor"],
                "exclude_entities": ["sensor.uptime"],
            }
        })
        assert dp._include_domains == {"sensor"}
        assert dp._exclude_entities == ["sensor.uptime"]

    def test_update_clears_filters_when_absent(self):
        dp = DataProcessor()
        dp.update_filters({
            "entity_filters": {
                "include_domains": ["sensor"],
                "exclude_entities": ["sensor.uptime"],
            }
        })
        dp.update_filters({"feature_flags": {}})
        assert dp._include_domains == set()
        assert dp._exclude_entities == []

    def test_update_with_empty_config(self):
        dp = DataProcessor()
        dp.update_filters({})
        assert dp._include_domains == set()
        assert dp._exclude_entities == []


class TestCursorAdvancement:
    """Tests for cursor behavior with filtering."""

    @pytest.mark.asyncio
    async def test_cursor_advances_when_all_filtered(self):
        """When all records are filtered, cursor should still advance."""
        from unittest.mock import AsyncMock, MagicMock
        from main import EventExtractor

        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.api_client = AsyncMock()
        extractor.data_processor = DataProcessor()
        extractor.batch_size = 100

        # All records are system_monitor (will be filtered)
        filtered_states = [
            {"entity_id": "sensor.system_monitor_cpu", "raw_timestamp": 100.0, "state": "50"},
            {"entity_id": "sensor.system_monitor_memory_use", "raw_timestamp": 200.0, "state": "512"},
        ]
        extractor.db_reader.fetch_states.side_effect = [filtered_states, []]

        result = await extractor._process_states(0.0)
        assert result == 200.0  # Should advance to batch_max_ts
        extractor.api_client.send_batch.assert_not_called()

    @pytest.mark.asyncio
    async def test_cursor_advances_through_multiple_filtered_batches(self):
        """Should paginate through multiple batches of filtered records."""
        from unittest.mock import AsyncMock, MagicMock
        from main import EventExtractor

        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.api_client = AsyncMock()
        extractor.data_processor = DataProcessor()
        extractor.batch_size = 2

        # Two full batches of filtered records, then empty
        batch1 = [
            {"entity_id": "sensor.system_monitor_cpu", "raw_timestamp": 100.0, "state": "50"},
            {"entity_id": "sensor.system_monitor_cpu", "raw_timestamp": 200.0, "state": "55"},
        ]
        batch2 = [
            {"entity_id": "sensor.system_monitor_cpu", "raw_timestamp": 300.0, "state": "60"},
        ]
        extractor.db_reader.fetch_states.side_effect = [batch1, batch2]

        result = await extractor._process_states(0.0)
        assert result == 300.0
        assert extractor.db_reader.fetch_states.call_count == 2
        extractor.api_client.send_batch.assert_not_called()
