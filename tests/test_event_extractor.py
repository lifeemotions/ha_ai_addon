"""Tests for EventExtractor class."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from const import CONFIG_REFRESH_INTERVAL_MINUTES
from main import DataProcessor, EventExtractor


class TestEventExtractorInit:
    """Tests for EventExtractor initialization."""

    @patch("main.ModelManager")
    @patch("main.DatabaseReader")
    @patch("main.CloudApiClient")
    def test_init_creates_components(self, mock_api, mock_db, mock_model):
        extractor = EventExtractor()
        assert extractor.running is True
        assert extractor.config is None
        assert extractor._last_config_refresh == 0.0
        assert extractor.sync_interval_minutes is None
        assert extractor.batch_size is None
        assert extractor.verified is False
        mock_db.assert_called_once()
        mock_api.assert_called_once()
        mock_model.assert_called_once()
        assert extractor.model_manager is not None

    @patch("main.ModelManager")
    @patch("main.DatabaseReader")
    @patch("main.CloudApiClient")
    def test_init_has_no_checkpoint_manager(self, mock_api, mock_db, mock_model):
        extractor = EventExtractor()
        assert not hasattr(extractor, "checkpoint_manager")


class TestSyncCycle:
    """Tests for EventExtractor.sync_cycle()."""

    @pytest.mark.asyncio
    async def test_sync_cycle_uses_separate_cursors(self):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.api_client = AsyncMock()
        extractor.api_client.fetch_checkpoint = AsyncMock(
            return_value={"event": 1705320000.0, "state": 1705319000.0}
        )
        extractor.running = True

        extractor._process_events = AsyncMock(return_value=1705320060.0)
        extractor._process_states = AsyncMock(return_value=1705320120.0)

        await extractor.sync_cycle()

        extractor.api_client.fetch_checkpoint.assert_called_once()
        extractor._process_events.assert_called_once_with(1705320000.0)
        extractor._process_states.assert_called_once_with(1705319000.0)

    @pytest.mark.asyncio
    async def test_sync_cycle_same_cursors(self):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.api_client = AsyncMock()
        extractor.api_client.fetch_checkpoint = AsyncMock(
            return_value={"event": 1705320060.0, "state": 1705320060.0}
        )
        extractor.running = True

        extractor._process_events = AsyncMock(return_value=1705320120.0)
        extractor._process_states = AsyncMock(return_value=1705320120.0)

        await extractor.sync_cycle()

        extractor._process_events.assert_called_once_with(1705320060.0)
        extractor._process_states.assert_called_once_with(1705320060.0)

    @pytest.mark.asyncio
    async def test_sync_cycle_skips_when_checkpoint_is_none(self):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.api_client = AsyncMock()
        extractor.api_client.fetch_checkpoint = AsyncMock(return_value=None)
        extractor.running = True

        extractor._process_events = AsyncMock()
        extractor._process_states = AsyncMock()

        await extractor.sync_cycle()

        extractor._process_events.assert_not_called()
        extractor._process_states.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_cycle_with_zero_timestamps(self):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.api_client = AsyncMock()
        extractor.api_client.fetch_checkpoint = AsyncMock(
            return_value={"event": 0.0, "state": 0.0}
        )
        extractor.running = True

        extractor._process_events = AsyncMock(return_value=1705320000.0)
        extractor._process_states = AsyncMock(return_value=1705320000.0)

        await extractor.sync_cycle()

        extractor._process_events.assert_called_once_with(0.0)
        extractor._process_states.assert_called_once_with(0.0)


class TestProcessEvents:
    """Tests for EventExtractor._process_events()."""

    @pytest.mark.asyncio
    async def test_no_events_returns_same_timestamp(self):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.db_reader.fetch_events.return_value = []
        extractor.api_client = AsyncMock()
        extractor.data_processor = DataProcessor()
        extractor.batch_size = 100

        result = await extractor._process_events(1705320000.0)
        assert result == 1705320000.0

    @pytest.mark.asyncio
    async def test_processes_single_batch(self, sample_event_records):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.db_reader.fetch_events.side_effect = [sample_event_records, []]
        extractor.api_client = AsyncMock()
        extractor.api_client.send_batch = AsyncMock(return_value=True)
        extractor.data_processor = DataProcessor()
        extractor.batch_size = 100

        result = await extractor._process_events(0.0)
        assert result == 1705320060.0  # max raw_timestamp from sample records

    @pytest.mark.asyncio
    async def test_stops_on_send_failure(self, sample_event_records):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.db_reader.fetch_events.return_value = sample_event_records
        extractor.api_client = AsyncMock()
        extractor.api_client.send_batch = AsyncMock(return_value=False)
        extractor.data_processor = DataProcessor()
        extractor.batch_size = 100

        result = await extractor._process_events(0.0)
        assert result == 0.0  # Should not advance on failure

    @pytest.mark.asyncio
    async def test_processes_multiple_batches(self):
        """When batch_size events are returned, should fetch another batch."""
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.data_processor = DataProcessor()
        extractor.batch_size = 100

        # First batch: exactly batch_size records with raw_timestamps
        batch1 = [
            {"id": i, "type": "event", "raw_timestamp": 1705320000.0 + i, "entity_id": "automation.test"}
            for i in range(1, extractor.batch_size + 1)
        ]
        # Second batch: fewer than batch_size (end of data)
        batch2 = [
            {"id": extractor.batch_size + 1, "type": "event", "raw_timestamp": 1705320000.0 + extractor.batch_size + 1, "entity_id": "automation.test"}
        ]

        extractor.db_reader.fetch_events.side_effect = [batch1, batch2]
        extractor.api_client = AsyncMock()
        extractor.api_client.send_batch = AsyncMock(return_value=True)

        result = await extractor._process_events(0.0)
        assert result == 1705320000.0 + extractor.batch_size + 1
        assert extractor.db_reader.fetch_events.call_count == 2


class TestProcessStates:
    """Tests for EventExtractor._process_states()."""

    @pytest.mark.asyncio
    async def test_no_states_returns_same_timestamp(self):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.db_reader.fetch_states.return_value = []
        extractor.api_client = AsyncMock()
        extractor.data_processor = DataProcessor()
        extractor.batch_size = 100

        result = await extractor._process_states(1705320000.0)
        assert result == 1705320000.0

    @pytest.mark.asyncio
    async def test_processes_single_batch(self, sample_state_records):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.db_reader.fetch_states.side_effect = [sample_state_records, []]
        extractor.api_client = AsyncMock()
        extractor.api_client.send_batch = AsyncMock(return_value=True)
        extractor.data_processor = DataProcessor()
        extractor.batch_size = 100

        result = await extractor._process_states(0.0)
        assert result == 1705320000.0  # raw_timestamp from sample record

    @pytest.mark.asyncio
    async def test_stops_on_send_failure(self, sample_state_records):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.db_reader.fetch_states.return_value = sample_state_records
        extractor.api_client = AsyncMock()
        extractor.api_client.send_batch = AsyncMock(return_value=False)
        extractor.data_processor = DataProcessor()
        extractor.batch_size = 100

        result = await extractor._process_states(0.0)
        assert result == 0.0


class TestRun:
    """Tests for EventExtractor.run()."""

    @pytest.mark.asyncio
    async def test_run_exits_when_no_token(self):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.api_client = AsyncMock()
        extractor.model_manager = MagicMock()
        extractor.running = True
        extractor.config = None
        extractor._last_config_refresh = 0.0
        extractor.verified = False
        extractor.sync_interval_minutes = None
        extractor.batch_size = None

        with patch("main.CLOUD_AUTH_TOKEN", ""):
            await extractor.run()

        # Should exit early without calling close
        extractor.api_client.close.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_exits_when_whitespace_token(self):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.api_client = AsyncMock()
        extractor.model_manager = MagicMock()
        extractor.running = True
        extractor.config = None
        extractor._last_config_refresh = 0.0
        extractor.verified = False
        extractor.sync_interval_minutes = None
        extractor.batch_size = None

        with patch("main.CLOUD_AUTH_TOKEN", "   "):
            await extractor.run()

        extractor.api_client.close.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_exits_when_db_missing(self):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.api_client = AsyncMock()
        extractor.model_manager = MagicMock()
        extractor.running = True
        extractor.config = None
        extractor._last_config_refresh = 0.0
        extractor.verified = False
        extractor.sync_interval_minutes = None
        extractor.batch_size = None

        with patch("main.Path") as mock_path, \
             patch("main.DATABASE_PATH", "/nonexistent/db"), \
             patch("main.CLOUD_AUTH_TOKEN", "token123456"):
            mock_path.return_value.exists.return_value = False
            await extractor.run()

        # Should exit early (return before try/finally, so close is not called)
        extractor.api_client.close.assert_not_called()


class TestHeartbeatLoop:
    """Tests for EventExtractor._heartbeat_loop()."""

    @pytest.mark.asyncio
    async def test_heartbeat_sets_verified_on_success(self):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.api_client = AsyncMock()
        extractor.api_client.verify_token = AsyncMock(
            return_value={"sync_interval_minutes": 10, "batch_size": 200}
        )
        extractor.running = True
        extractor.verified = False
        extractor.sync_interval_minutes = None
        extractor.batch_size = None

        call_count = 0

        async def stop_after_one(seconds):
            nonlocal call_count
            call_count += 1
            extractor.running = False

        with patch("main.asyncio.sleep", side_effect=stop_after_one):
            await extractor._heartbeat_loop()

        assert extractor.verified is True
        assert extractor.sync_interval_minutes == 10
        assert extractor.batch_size == 200

    @pytest.mark.asyncio
    async def test_heartbeat_sets_unverified_on_failure(self):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.api_client = AsyncMock()
        extractor.api_client.verify_token = AsyncMock(return_value=None)
        extractor.running = True
        extractor.verified = True
        extractor.sync_interval_minutes = 5
        extractor.batch_size = 100

        async def stop_after_one(seconds):
            extractor.running = False

        with patch("main.asyncio.sleep", side_effect=stop_after_one):
            await extractor._heartbeat_loop()

        assert extractor.verified is False

    @pytest.mark.asyncio
    async def test_heartbeat_catches_exceptions(self):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.api_client = AsyncMock()
        extractor.api_client.verify_token = AsyncMock(side_effect=RuntimeError("boom"))
        extractor.running = True
        extractor.verified = True
        extractor.sync_interval_minutes = 5
        extractor.batch_size = 100

        async def stop_after_one(seconds):
            extractor.running = False

        with patch("main.asyncio.sleep", side_effect=stop_after_one):
            await extractor._heartbeat_loop()

        assert extractor.verified is False


class TestSyncLoop:
    """Tests for EventExtractor._sync_loop()."""

    @pytest.mark.asyncio
    async def test_sync_loop_waits_for_verification(self):
        """Should wait when not verified."""
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.api_client = AsyncMock()
        extractor.model_manager = MagicMock()
        extractor.model_manager.should_run_prediction = MagicMock(return_value=False)
        extractor.running = True
        extractor.verified = False
        extractor.sync_interval_minutes = 5
        extractor.batch_size = 100
        extractor.config = None
        extractor._last_config_refresh = 0.0

        sleep_count = 0

        async def count_sleep(seconds):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 3:
                extractor.running = False

        extractor.sync_cycle = AsyncMock()
        extractor._refresh_config = AsyncMock()

        with patch("main.asyncio.sleep", side_effect=count_sleep):
            await extractor._sync_loop()

        # sync_cycle should not have been called since we were never verified
        extractor.sync_cycle.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_loop_runs_when_verified(self):
        """Should run sync_cycle when verified."""
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.api_client = AsyncMock()
        extractor.model_manager = MagicMock()
        extractor.model_manager.should_run_prediction = MagicMock(return_value=False)
        extractor.running = True
        extractor.verified = True
        extractor.sync_interval_minutes = 5
        extractor.batch_size = 100
        extractor.config = None
        extractor._last_config_refresh = -CONFIG_REFRESH_INTERVAL_MINUTES * 60

        async def fake_sync():
            extractor.running = False

        extractor.sync_cycle = fake_sync
        extractor._refresh_config = AsyncMock()

        with patch("main.asyncio.sleep", new_callable=AsyncMock):
            await extractor._sync_loop()

        # Config should be refreshed
        extractor._refresh_config.assert_called()

    @pytest.mark.asyncio
    async def test_sync_loop_handles_sync_cycle_exception(self):
        """sync_cycle exceptions should be caught and not crash the loop."""
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.api_client = AsyncMock()
        extractor.model_manager = MagicMock()
        extractor.model_manager.should_run_prediction = MagicMock(return_value=False)
        extractor.running = True
        extractor.verified = True
        extractor.sync_interval_minutes = 5
        extractor.batch_size = 100
        extractor.config = None
        extractor._last_config_refresh = 0.0

        call_count = 0

        async def failing_sync():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("sync failed")
            extractor.running = False

        extractor.sync_cycle = failing_sync
        extractor._refresh_config = AsyncMock()

        with patch("main.asyncio.sleep", new_callable=AsyncMock):
            await extractor._sync_loop()

        assert call_count == 2

    @pytest.mark.asyncio
    async def test_sync_loop_executes_prediction_when_scheduled(self):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.api_client = AsyncMock()
        extractor.model_manager = MagicMock()
        extractor.model_manager.should_run_prediction = MagicMock(return_value=True)
        extractor.model_manager.run_prediction = AsyncMock(return_value=True)
        extractor.running = True
        extractor.verified = True
        extractor.sync_interval_minutes = 5
        extractor.batch_size = 100
        extractor.config = {"prediction_schedule": "0 * * * *"}
        extractor._last_config_refresh = 0.0

        async def fake_sync():
            extractor.running = False

        extractor.sync_cycle = fake_sync
        extractor._refresh_config = AsyncMock()

        with patch("main.asyncio.sleep", new_callable=AsyncMock):
            await extractor._sync_loop()

        extractor.model_manager.should_run_prediction.assert_called_once_with("0 * * * *")
        extractor.model_manager.run_prediction.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_loop_skips_prediction_when_not_scheduled(self):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.api_client = AsyncMock()
        extractor.model_manager = MagicMock()
        extractor.model_manager.should_run_prediction = MagicMock(return_value=False)
        extractor.model_manager.run_prediction = AsyncMock()
        extractor.running = True
        extractor.verified = True
        extractor.sync_interval_minutes = 5
        extractor.batch_size = 100
        extractor.config = {"prediction_schedule": "0 * * * *"}
        extractor._last_config_refresh = 0.0

        async def fake_sync():
            extractor.running = False

        extractor.sync_cycle = fake_sync
        extractor._refresh_config = AsyncMock()

        with patch("main.asyncio.sleep", new_callable=AsyncMock):
            await extractor._sync_loop()

        extractor.model_manager.run_prediction.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_loop_handles_prediction_exception(self):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.api_client = AsyncMock()
        extractor.model_manager = MagicMock()
        extractor.model_manager.should_run_prediction = MagicMock(return_value=True)
        extractor.model_manager.run_prediction = AsyncMock(side_effect=RuntimeError("prediction crashed"))
        extractor.running = True
        extractor.verified = True
        extractor.sync_interval_minutes = 5
        extractor.batch_size = 100
        extractor.config = {"prediction_schedule": "0 * * * *"}
        extractor._last_config_refresh = 0.0

        call_count = 0

        async def fake_sync():
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                extractor.running = False

        extractor.sync_cycle = fake_sync
        extractor._refresh_config = AsyncMock()

        with patch("main.asyncio.sleep", new_callable=AsyncMock):
            await extractor._sync_loop()

        assert call_count == 2


class TestRefreshConfig:
    """Tests for EventExtractor._refresh_config()."""

    @pytest.mark.asyncio
    async def test_refresh_config_saves_to_disk_on_success(self, tmp_path):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.api_client = AsyncMock()
        extractor.model_manager = AsyncMock()
        extractor.model_manager.check_and_update = AsyncMock(return_value=True)
        extractor.data_processor = DataProcessor()
        extractor.config = None
        extractor._last_config_refresh = 0.0

        config_data = {"feature_flags": {"sync_states": True}}
        extractor.api_client.fetch_config = AsyncMock(return_value=config_data)

        config_file = tmp_path / "config.json"
        with patch("main.CONFIG_FILE_PATH", str(config_file)):
            await extractor._refresh_config()

        assert extractor.config == config_data
        assert config_file.exists()
        assert json.loads(config_file.read_text()) == config_data
        extractor.model_manager.check_and_update.assert_called_once_with(config_data)

    @pytest.mark.asyncio
    async def test_refresh_config_falls_back_to_local(self, tmp_path):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.api_client = AsyncMock()
        extractor.model_manager = AsyncMock()
        extractor.model_manager.check_and_update = AsyncMock(return_value=True)
        extractor.data_processor = DataProcessor()
        extractor.config = None
        extractor._last_config_refresh = 0.0

        extractor.api_client.fetch_config = AsyncMock(return_value=None)

        config_data = {"feature_flags": {"sync_states": False}}
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config_data))

        with patch("main.CONFIG_FILE_PATH", str(config_file)):
            await extractor._refresh_config()

        assert extractor.config == config_data
        extractor.model_manager.check_and_update.assert_called_once_with(config_data)

    @pytest.mark.asyncio
    async def test_refresh_config_none_when_no_fallback(self, tmp_path):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.api_client = AsyncMock()
        extractor.model_manager = AsyncMock()
        extractor.model_manager.check_and_update = AsyncMock()
        extractor.config = None
        extractor._last_config_refresh = 0.0

        extractor.api_client.fetch_config = AsyncMock(return_value=None)

        with patch("main.CONFIG_FILE_PATH", str(tmp_path / "nonexistent.json")):
            await extractor._refresh_config()

        assert extractor.config is None
        # Model check should NOT be called when config is None
        extractor.model_manager.check_and_update.assert_not_called()


class TestLoadLocalConfig:
    """Tests for EventExtractor._load_local_config()."""

    def test_load_valid_config(self, tmp_path):
        extractor = EventExtractor.__new__(EventExtractor)
        config_data = {"settings": {"batch_size": 200}}
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config_data))

        with patch("main.CONFIG_FILE_PATH", str(config_file)):
            result = extractor._load_local_config()

        assert result == config_data

    def test_load_missing_file_returns_none(self, tmp_path):
        extractor = EventExtractor.__new__(EventExtractor)

        with patch("main.CONFIG_FILE_PATH", str(tmp_path / "nonexistent.json")):
            result = extractor._load_local_config()

        assert result is None

    def test_load_invalid_json_returns_none(self, tmp_path):
        extractor = EventExtractor.__new__(EventExtractor)
        config_file = tmp_path / "config.json"
        config_file.write_text("not valid json {{")

        with patch("main.CONFIG_FILE_PATH", str(config_file)):
            result = extractor._load_local_config()

        assert result is None


class TestSaveLocalConfig:
    """Tests for EventExtractor._save_local_config()."""

    def test_save_config(self, tmp_path):
        extractor = EventExtractor.__new__(EventExtractor)
        config_data = {"entity_filters": {"include": ["light.*"]}}
        config_file = tmp_path / "config.json"

        with patch("main.CONFIG_FILE_PATH", str(config_file)):
            extractor._save_local_config(config_data)

        assert config_file.exists()
        assert json.loads(config_file.read_text()) == config_data

    def test_save_config_handles_write_error(self):
        extractor = EventExtractor.__new__(EventExtractor)
        config_data = {"key": "value"}

        with patch("main.CONFIG_FILE_PATH", "/nonexistent/dir/config.json"):
            # Should not raise, just log warning
            extractor._save_local_config(config_data)
