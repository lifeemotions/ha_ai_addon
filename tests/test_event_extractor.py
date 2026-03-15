"""Tests for EventExtractor class."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from main import EventExtractor


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
    async def test_sync_cycle_processes_events_and_states(self):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.api_client = AsyncMock()
        extractor.api_client.fetch_checkpoint = AsyncMock(return_value=1705320000.0)
        extractor.running = True
        extractor.batch_size = 100

        extractor._process_events = AsyncMock(return_value=1705320060.0)
        extractor._process_states = AsyncMock(return_value=1705320120.0)

        await extractor.sync_cycle()

        extractor.api_client.fetch_checkpoint.assert_called_once()
        extractor._process_events.assert_called_once_with(1705320000.0)
        extractor._process_states.assert_called_once_with(1705320000.0)

    @pytest.mark.asyncio
    async def test_sync_cycle_uses_checkpoint_timestamp(self):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.api_client = AsyncMock()
        extractor.api_client.fetch_checkpoint = AsyncMock(return_value=1705320060.0)
        extractor.running = True
        extractor.batch_size = 100

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
        extractor.batch_size = 100

        extractor._process_events = AsyncMock()
        extractor._process_states = AsyncMock()

        await extractor.sync_cycle()

        extractor._process_events.assert_not_called()
        extractor._process_states.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_cycle_with_zero_timestamp(self):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.api_client = AsyncMock()
        extractor.api_client.fetch_checkpoint = AsyncMock(return_value=0.0)
        extractor.running = True
        extractor.batch_size = 100

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
        extractor.batch_size = 100

        result = await extractor._process_events(0.0)
        assert result == 0.0  # Should not advance on failure

    @pytest.mark.asyncio
    async def test_processes_multiple_batches(self):
        """When batch_size events are returned, should fetch another batch."""
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.batch_size = 50

        # First batch: exactly batch_size records
        batch1 = [
            {"id": i, "type": "event", "raw_timestamp": 1705320000.0 + i}
            for i in range(1, 51)
        ]
        # Second batch: fewer than batch_size (end of data)
        batch2 = [
            {"id": 51, "type": "event", "raw_timestamp": 1705320000.0 + 51}
        ]

        extractor.db_reader.fetch_events.side_effect = [batch1, batch2]
        extractor.api_client = AsyncMock()
        extractor.api_client.send_batch = AsyncMock(return_value=True)

        result = await extractor._process_events(0.0)
        assert result == 1705320000.0 + 51
        assert extractor.db_reader.fetch_events.call_count == 2

    @pytest.mark.asyncio
    async def test_uses_self_batch_size(self, sample_event_records):
        """_process_events should pass self.batch_size to db_reader."""
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.db_reader.fetch_events.side_effect = [sample_event_records, []]
        extractor.api_client = AsyncMock()
        extractor.api_client.send_batch = AsyncMock(return_value=True)
        extractor.batch_size = 250

        await extractor._process_events(0.0)

        # First call should use batch_size=250
        extractor.db_reader.fetch_events.assert_any_call(0.0, 250)


class TestProcessStates:
    """Tests for EventExtractor._process_states()."""

    @pytest.mark.asyncio
    async def test_no_states_returns_same_timestamp(self):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.db_reader.fetch_states.return_value = []
        extractor.api_client = AsyncMock()
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
        extractor.batch_size = 100

        result = await extractor._process_states(0.0)
        assert result == 0.0


class TestRun:
    """Tests for EventExtractor.run()."""

    @pytest.mark.asyncio
    async def test_run_exits_when_no_token(self):
        """Empty token should exit immediately without starting loops."""
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.api_client = AsyncMock()
        extractor.model_manager = MagicMock()
        extractor.running = True
        extractor.config = None
        extractor._last_config_refresh = 0.0
        extractor.sync_interval_minutes = None
        extractor.batch_size = None
        extractor.verified = False

        with patch("main.CLOUD_AUTH_TOKEN", ""):
            await extractor.run()

        # Should exit early — no loops started, close not called
        extractor.api_client.close.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_exits_when_whitespace_token(self):
        """Whitespace-only token should exit immediately."""
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.api_client = AsyncMock()
        extractor.model_manager = MagicMock()
        extractor.running = True
        extractor.config = None
        extractor._last_config_refresh = 0.0
        extractor.sync_interval_minutes = None
        extractor.batch_size = None
        extractor.verified = False

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
        extractor.sync_interval_minutes = None
        extractor.batch_size = None
        extractor.verified = False

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
    async def test_heartbeat_success_sets_verified(self):
        """Successful verify_token sets verified=True and updates settings."""
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

        async def fake_sleep(seconds):
            nonlocal call_count
            call_count += 1
            extractor.running = False

        with patch("main.asyncio.sleep", side_effect=fake_sleep):
            await extractor._heartbeat_loop()

        assert extractor.verified is True
        assert extractor.sync_interval_minutes == 10
        assert extractor.batch_size == 200

    @pytest.mark.asyncio
    async def test_heartbeat_failure_sets_verified_false(self):
        """Failed verify_token sets verified=False."""
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.api_client = AsyncMock()
        extractor.api_client.verify_token = AsyncMock(return_value=None)
        extractor.running = True
        extractor.verified = True  # was previously verified
        extractor.sync_interval_minutes = 5
        extractor.batch_size = 100

        async def fake_sleep(seconds):
            extractor.running = False

        with patch("main.asyncio.sleep", side_effect=fake_sleep):
            await extractor._heartbeat_loop()

        assert extractor.verified is False

    @pytest.mark.asyncio
    async def test_heartbeat_exception_sets_verified_false(self):
        """Exception in verify_token sets verified=False."""
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.api_client = AsyncMock()
        extractor.api_client.verify_token = AsyncMock(side_effect=RuntimeError("boom"))
        extractor.running = True
        extractor.verified = True
        extractor.sync_interval_minutes = 5
        extractor.batch_size = 100

        async def fake_sleep(seconds):
            extractor.running = False

        with patch("main.asyncio.sleep", side_effect=fake_sleep):
            await extractor._heartbeat_loop()

        assert extractor.verified is False

    @pytest.mark.asyncio
    async def test_heartbeat_sleeps_interval_seconds(self):
        """Heartbeat should sleep HEARTBEAT_INTERVAL_SECONDS between calls."""
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.api_client = AsyncMock()
        extractor.api_client.verify_token = AsyncMock(
            return_value={"sync_interval_minutes": 5, "batch_size": 100}
        )
        extractor.running = True
        extractor.verified = False
        extractor.sync_interval_minutes = None
        extractor.batch_size = None

        sleep_values = []

        async def fake_sleep(seconds):
            sleep_values.append(seconds)
            extractor.running = False

        with patch("main.asyncio.sleep", side_effect=fake_sleep):
            await extractor._heartbeat_loop()

        from const import HEARTBEAT_INTERVAL_SECONDS
        assert sleep_values[0] == HEARTBEAT_INTERVAL_SECONDS

    @pytest.mark.asyncio
    async def test_heartbeat_settings_update_mid_run(self):
        """Settings should be updated on each successful heartbeat."""
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.api_client = AsyncMock()
        extractor.running = True
        extractor.verified = False
        extractor.sync_interval_minutes = None
        extractor.batch_size = None

        call_count = 0

        async def verify_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"sync_interval_minutes": 5, "batch_size": 100}
            return {"sync_interval_minutes": 15, "batch_size": 500}

        extractor.api_client.verify_token = AsyncMock(side_effect=verify_side_effect)

        sleep_count = 0

        async def fake_sleep(seconds):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 2:
                extractor.running = False

        with patch("main.asyncio.sleep", side_effect=fake_sleep):
            await extractor._heartbeat_loop()

        assert extractor.sync_interval_minutes == 15
        assert extractor.batch_size == 500


class TestSyncLoop:
    """Tests for EventExtractor._sync_loop()."""

    @pytest.mark.asyncio
    async def test_sync_loop_waits_until_verified(self):
        """Sync loop should poll every 30s until verified=True."""
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.api_client = AsyncMock()
        extractor.model_manager = MagicMock()
        extractor.model_manager.should_run_prediction = MagicMock(return_value=False)
        extractor.running = True
        extractor.config = None
        extractor._last_config_refresh = 0.0
        extractor.sync_interval_minutes = 5
        extractor.batch_size = 100
        extractor.verified = False

        extractor.sync_cycle = AsyncMock()
        extractor._refresh_config = AsyncMock()

        sleep_calls = []

        async def fake_sleep(seconds):
            sleep_calls.append(seconds)
            if len(sleep_calls) <= 2:
                # After 2 polls, set verified
                if len(sleep_calls) == 2:
                    extractor.verified = True
            else:
                # After the sync cycle sleep, stop
                extractor.running = False

        with patch("main.asyncio.sleep", side_effect=fake_sleep):
            await extractor._sync_loop()

        # Should have waited with 30s polls, then done a sync cycle
        assert sleep_calls[0] == 30
        assert sleep_calls[1] == 30
        extractor.sync_cycle.assert_called()

    @pytest.mark.asyncio
    async def test_sync_loop_skips_cycle_when_not_verified(self):
        """When verified becomes False mid-run, sync loop should skip."""
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.api_client = AsyncMock()
        extractor.model_manager = MagicMock()
        extractor.model_manager.should_run_prediction = MagicMock(return_value=False)
        extractor.running = True
        extractor.config = None
        extractor._last_config_refresh = 0.0
        extractor.sync_interval_minutes = 5
        extractor.batch_size = 100
        extractor.verified = True  # starts verified

        call_count = 0

        async def fake_sync():
            nonlocal call_count
            call_count += 1
            # After first sync, simulate heartbeat failure
            extractor.verified = False

        extractor.sync_cycle = fake_sync
        extractor._refresh_config = AsyncMock()

        sleep_count = 0

        async def fake_sleep(seconds):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 3:
                extractor.running = False

        with patch("main.asyncio.sleep", side_effect=fake_sleep):
            await extractor._sync_loop()

        # sync_cycle should have been called once, then skipped
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_sync_loop_handles_sync_exception(self):
        """sync_cycle exceptions should be caught and not crash the loop."""
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.api_client = AsyncMock()
        extractor.model_manager = MagicMock()
        extractor.model_manager.should_run_prediction = MagicMock(return_value=False)
        extractor.running = True
        extractor.config = None
        extractor._last_config_refresh = 0.0
        extractor.sync_interval_minutes = 5
        extractor.batch_size = 100
        extractor.verified = True

        call_count = 0

        async def failing_sync():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("sync failed")

        extractor.sync_cycle = failing_sync
        extractor._refresh_config = AsyncMock()

        sleep_count = 0

        async def fake_sleep(seconds):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 2:
                extractor.running = False

        with patch("main.asyncio.sleep", side_effect=fake_sleep):
            await extractor._sync_loop()

        assert call_count == 2  # Recovered from first failure

    @pytest.mark.asyncio
    async def test_sync_loop_executes_prediction_when_scheduled(self):
        """Prediction should run when schedule says it's time."""
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.api_client = AsyncMock()
        extractor.model_manager = MagicMock()
        extractor.model_manager.should_run_prediction = MagicMock(return_value=True)
        extractor.model_manager.run_prediction = AsyncMock(return_value=True)
        extractor.running = True
        extractor.config = {"prediction_schedule": "0 * * * *"}
        extractor._last_config_refresh = 0.0
        extractor.sync_interval_minutes = 5
        extractor.batch_size = 100
        extractor.verified = True

        extractor.sync_cycle = AsyncMock()
        extractor._refresh_config = AsyncMock()

        async def fake_sleep(seconds):
            extractor.running = False

        with patch("main.asyncio.sleep", side_effect=fake_sleep):
            await extractor._sync_loop()

        extractor.model_manager.should_run_prediction.assert_called_once_with("0 * * * *")
        extractor.model_manager.run_prediction.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_loop_uses_sync_interval_from_heartbeat(self):
        """Sync loop should sleep for sync_interval_minutes * 60."""
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.api_client = AsyncMock()
        extractor.model_manager = MagicMock()
        extractor.model_manager.should_run_prediction = MagicMock(return_value=False)
        extractor.running = True
        extractor.config = None
        extractor._last_config_refresh = 0.0
        extractor.sync_interval_minutes = 10
        extractor.batch_size = 100
        extractor.verified = True

        extractor.sync_cycle = AsyncMock()
        extractor._refresh_config = AsyncMock()

        sleep_values = []

        async def fake_sleep(seconds):
            sleep_values.append(seconds)
            extractor.running = False

        with patch("main.asyncio.sleep", side_effect=fake_sleep):
            await extractor._sync_loop()

        # Should sleep for 10 * 60 = 600 seconds
        assert sleep_values[-1] == 600


class TestRefreshConfig:
    """Tests for EventExtractor._refresh_config()."""

    @pytest.mark.asyncio
    async def test_refresh_config_saves_to_disk_on_success(self, tmp_path):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.api_client = AsyncMock()
        extractor.model_manager = AsyncMock()
        extractor.model_manager.check_and_update = AsyncMock(return_value=True)
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
