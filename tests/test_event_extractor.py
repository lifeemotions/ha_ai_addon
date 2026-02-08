"""Tests for EventExtractor class."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, mock_open, patch

import pytest

from main import EventExtractor


class TestEventExtractorInit:
    """Tests for EventExtractor initialization."""

    @patch("main.DatabaseReader")
    @patch("main.CloudApiClient")
    def test_init_creates_components(self, mock_api, mock_db):
        extractor = EventExtractor()
        assert extractor.running is True
        assert extractor.config is None
        assert extractor._last_config_refresh == 0.0
        mock_db.assert_called_once()
        mock_api.assert_called_once()

    @patch("main.DatabaseReader")
    @patch("main.CloudApiClient")
    def test_init_has_no_checkpoint_manager(self, mock_api, mock_db):
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
    async def test_sync_cycle_with_zero_timestamp(self):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.api_client = AsyncMock()
        extractor.api_client.fetch_checkpoint = AsyncMock(return_value=0.0)
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

        result = await extractor._process_events(1705320000.0)
        assert result == 1705320000.0

    @pytest.mark.asyncio
    async def test_processes_single_batch(self, sample_event_records):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.db_reader.fetch_events.side_effect = [sample_event_records, []]
        extractor.api_client = AsyncMock()
        extractor.api_client.send_batch = AsyncMock(return_value=True)

        result = await extractor._process_events(0.0)
        assert result == 1705320060.0  # max raw_timestamp from sample records

    @pytest.mark.asyncio
    async def test_stops_on_send_failure(self, sample_event_records):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.db_reader.fetch_events.return_value = sample_event_records
        extractor.api_client = AsyncMock()
        extractor.api_client.send_batch = AsyncMock(return_value=False)

        result = await extractor._process_events(0.0)
        assert result == 0.0  # Should not advance on failure

    @pytest.mark.asyncio
    async def test_processes_multiple_batches(self):
        """When batch_size events are returned, should fetch another batch."""
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()

        from const import BATCH_SIZE
        # First batch: exactly BATCH_SIZE records with raw_timestamps
        batch1 = [
            {"id": i, "type": "event", "raw_timestamp": 1705320000.0 + i}
            for i in range(1, BATCH_SIZE + 1)
        ]
        # Second batch: fewer than BATCH_SIZE (end of data)
        batch2 = [
            {"id": BATCH_SIZE + 1, "type": "event", "raw_timestamp": 1705320000.0 + BATCH_SIZE + 1}
        ]

        extractor.db_reader.fetch_events.side_effect = [batch1, batch2]
        extractor.api_client = AsyncMock()
        extractor.api_client.send_batch = AsyncMock(return_value=True)

        result = await extractor._process_events(0.0)
        assert result == 1705320000.0 + BATCH_SIZE + 1
        assert extractor.db_reader.fetch_events.call_count == 2


class TestProcessStates:
    """Tests for EventExtractor._process_states()."""

    @pytest.mark.asyncio
    async def test_no_states_returns_same_timestamp(self):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.db_reader.fetch_states.return_value = []
        extractor.api_client = AsyncMock()

        result = await extractor._process_states(1705320000.0)
        assert result == 1705320000.0

    @pytest.mark.asyncio
    async def test_processes_single_batch(self, sample_state_records):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.db_reader.fetch_states.side_effect = [sample_state_records, []]
        extractor.api_client = AsyncMock()
        extractor.api_client.send_batch = AsyncMock(return_value=True)

        result = await extractor._process_states(0.0)
        assert result == 1705320000.0  # raw_timestamp from sample record

    @pytest.mark.asyncio
    async def test_stops_on_send_failure(self, sample_state_records):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.db_reader.fetch_states.return_value = sample_state_records
        extractor.api_client = AsyncMock()
        extractor.api_client.send_batch = AsyncMock(return_value=False)

        result = await extractor._process_states(0.0)
        assert result == 0.0


class TestRun:
    """Tests for EventExtractor.run()."""

    @pytest.mark.asyncio
    async def test_run_exits_when_db_missing(self):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.api_client = AsyncMock()
        extractor.running = True
        extractor.config = None
        extractor._last_config_refresh = 0.0

        with patch("main.Path") as mock_path, \
             patch("main.DATABASE_PATH", "/nonexistent/db"), \
             patch("main.CLOUD_AUTH_TOKEN", "token123456"):
            mock_path.return_value.exists.return_value = False
            await extractor.run()

        # Should exit early (return before try/finally, so close is not called)
        extractor.api_client.close.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_stops_when_running_false(self):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.api_client = AsyncMock()
        extractor.running = True
        extractor.config = None
        extractor._last_config_refresh = 0.0

        call_count = 0

        async def fake_sync():
            nonlocal call_count
            call_count += 1
            extractor.running = False

        extractor.sync_cycle = fake_sync
        extractor._refresh_config = AsyncMock()

        with patch("main.Path") as mock_path, \
             patch("main.DATABASE_PATH", "/fake/db"), \
             patch("main.CLOUD_AUTH_TOKEN", "token123456"):
            mock_path.return_value.exists.return_value = True
            await extractor.run()

        assert call_count == 1
        extractor.api_client.close.assert_called_once()
        # Config should be refreshed on startup
        extractor._refresh_config.assert_called()

    @pytest.mark.asyncio
    async def test_run_handles_sync_cycle_exception(self):
        """sync_cycle exceptions should be caught and not crash the loop."""
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.db_reader = MagicMock()
        extractor.api_client = AsyncMock()
        extractor.running = True
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

        with patch("main.Path") as mock_path, \
             patch("main.DATABASE_PATH", "/fake/db"), \
             patch("main.CLOUD_AUTH_TOKEN", "token123456"), \
             patch("main.asyncio.sleep", new_callable=AsyncMock):
            mock_path.return_value.exists.return_value = True
            await extractor.run()

        assert call_count == 2  # Recovered from first failure, ran again


class TestRefreshConfig:
    """Tests for EventExtractor._refresh_config()."""

    @pytest.mark.asyncio
    async def test_refresh_config_saves_to_disk_on_success(self, tmp_path):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.api_client = AsyncMock()
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

    @pytest.mark.asyncio
    async def test_refresh_config_falls_back_to_local(self, tmp_path):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.api_client = AsyncMock()
        extractor.config = None
        extractor._last_config_refresh = 0.0

        extractor.api_client.fetch_config = AsyncMock(return_value=None)

        config_data = {"feature_flags": {"sync_states": False}}
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config_data))

        with patch("main.CONFIG_FILE_PATH", str(config_file)):
            await extractor._refresh_config()

        assert extractor.config == config_data

    @pytest.mark.asyncio
    async def test_refresh_config_none_when_no_fallback(self, tmp_path):
        extractor = EventExtractor.__new__(EventExtractor)
        extractor.api_client = AsyncMock()
        extractor.config = None
        extractor._last_config_refresh = 0.0

        extractor.api_client.fetch_config = AsyncMock(return_value=None)

        with patch("main.CONFIG_FILE_PATH", str(tmp_path / "nonexistent.json")):
            await extractor._refresh_config()

        assert extractor.config is None


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
