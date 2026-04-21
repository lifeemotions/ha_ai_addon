"""Tests for CloudApiClient class."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from main import CloudApiClient


class TestCloudApiClientInit:
    """Tests for CloudApiClient initialization."""

    def test_default_init(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ingest",
            auth_token="test-token",
        )
        assert client.api_endpoint == "https://api.test.com/ingest"
        assert client.auth_token == "test-token"
        assert client._session is None

    def test_custom_timeout(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com",
            auth_token="token",
            timeout=60,
        )
        assert client.timeout.total == 60


class TestCloudApiClientSession:
    """Tests for CloudApiClient session management."""

    @pytest.mark.asyncio
    async def test_get_session_creates_session(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com",
            auth_token="token",
        )
        session = await client._get_session()
        assert isinstance(session, aiohttp.ClientSession)
        await client.close()

    @pytest.mark.asyncio
    async def test_get_session_reuses_session(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com",
            auth_token="token",
        )
        session1 = await client._get_session()
        session2 = await client._get_session()
        assert session1 is session2
        await client.close()

    @pytest.mark.asyncio
    async def test_close_cleans_up_session(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com",
            auth_token="token",
        )
        await client._get_session()
        assert client._session is not None
        await client.close()
        assert client._session is None

    @pytest.mark.asyncio
    async def test_close_noop_when_no_session(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com",
            auth_token="token",
        )
        await client.close()  # Should not raise


def _make_mock_response(resp):
    """Create a single mock response context manager from a response dict."""
    mock_resp = MagicMock()
    mock_resp.status = resp["status"]
    mock_resp.headers = resp.get("headers", {})
    if "text" in resp:
        mock_resp.text = AsyncMock(return_value=resp["text"])
    if "json" in resp:
        mock_resp.json = AsyncMock(return_value=resp["json"])

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _make_mock_session(responses, method="post"):
    """Helper to create a mock session with a sequence of responses.

    responses: a single dict or list of dicts with keys:
        - status: int
        - text: str (optional, for error responses)
        - json: dict (optional, for JSON responses)
    method: "post" or "get"
    """
    mock_session = MagicMock()

    if not isinstance(responses, list):
        responses = [responses]

    context_managers = [_make_mock_response(r) for r in responses]

    if len(context_managers) == 1:
        setattr(mock_session, method, MagicMock(return_value=context_managers[0]))
    else:
        setattr(mock_session, method, MagicMock(side_effect=context_managers))

    return mock_session


class TestFetchEnabledEntities:
    """Tests for CloudApiClient.fetch_enabled_entities()."""

    @pytest.mark.asyncio
    async def test_returns_entity_id_set_on_200(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ingest",
            auth_token="test-token",
        )
        mock_session = _make_mock_session(
            {
                "status": 200,
                "json": {
                    "entities": [
                        {"entity_ref": "uuid-1", "entity_id": "sensor.temp", "forward_cursor_ts": None},
                        {"entity_ref": "uuid-2", "entity_id": "light.kitchen", "forward_cursor_ts": "2026-04-01T00:00:00Z"},
                    ]
                },
            },
            method="get",
        )
        client._get_session = AsyncMock(return_value=mock_session)

        result = await client.fetch_enabled_entities()
        assert result == {"sensor.temp", "light.kitchen"}

        call_args = mock_session.get.call_args
        assert call_args[0][0] == "https://api.test.com/ingest/v2/entities/cursors"
        assert call_args[1]["headers"]["Authorization"] == "Bearer test-token"

    @pytest.mark.asyncio
    async def test_empty_list_returns_empty_set(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com",
            auth_token="test-token",
        )
        mock_session = _make_mock_session(
            {"status": 200, "json": {"entities": []}}, method="get"
        )
        client._get_session = AsyncMock(return_value=mock_session)

        result = await client.fetch_enabled_entities()
        assert result == set()

    @pytest.mark.asyncio
    async def test_no_token_returns_none(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com",
            auth_token="",
        )
        result = await client.fetch_enabled_entities()
        assert result is None

    @pytest.mark.asyncio
    async def test_4xx_returns_none_no_retry(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com",
            auth_token="test-token",
        )
        mock_session = _make_mock_session(
            {"status": 401, "text": "unauthorized"}, method="get"
        )
        client._get_session = AsyncMock(return_value=mock_session)

        result = await client.fetch_enabled_entities()
        assert result is None
        assert mock_session.get.call_count == 1


class TestSendManifest:
    """Tests for CloudApiClient.send_manifest()."""

    @pytest.mark.asyncio
    async def test_empty_entities_returns_true_no_request(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com",
            auth_token="token",
        )
        client._get_session = AsyncMock()
        result = await client.send_manifest([])
        assert result is True
        client._get_session.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_auth_token_returns_false(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com",
            auth_token="",
        )
        entities = [{"entity_id": "sensor.temp", "labels": []}]
        result = await client.send_manifest(entities)
        assert result is False

    @pytest.mark.asyncio
    async def test_successful_manifest_200(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ingest",
            auth_token="test-token",
        )
        server_response = {
            "entities": [
                {"entity_ref": "uuid-1", "entity_id": "sensor.temp", "sync_enabled": True, "labels": ["living_room"]},
                {"entity_ref": "uuid-2", "entity_id": "sensor.humidity", "sync_enabled": False, "labels": []},
            ]
        }
        mock_session = _make_mock_session({"status": 200, "json": server_response})
        client._get_session = AsyncMock(return_value=mock_session)

        entities = [
            {"entity_id": "sensor.temp", "friendly_name": "Temp", "domain": "sensor", "labels": ["living_room"]},
            {"entity_id": "sensor.humidity", "friendly_name": "Humidity", "domain": "sensor", "labels": []},
        ]
        result = await client.send_manifest(entities)
        assert result is True

        call_args = mock_session.post.call_args
        assert call_args[0][0] == "https://api.test.com/ingest/v2/entities/manifest"
        assert call_args[1]["json"] == {"entities": entities}
        assert call_args[1]["headers"]["Authorization"] == "Bearer test-token"

    @pytest.mark.asyncio
    async def test_manifest_4xx_fails_fast(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com",
            auth_token="token",
        )
        mock_session = _make_mock_session({"status": 400, "text": "bad request"})
        client._get_session = AsyncMock(return_value=mock_session)

        entities = [{"entity_id": "sensor.x", "labels": []}]
        result = await client.send_manifest(entities)
        assert result is False
        # Only one attempt on 4xx
        assert mock_session.post.call_count == 1


class TestSendBatch:
    """Tests for CloudApiClient.send_batch()."""

    @pytest.mark.asyncio
    async def test_empty_records_returns_true(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com",
            auth_token="token",
        )
        result = await client.send_batch([])
        assert result is True

    @pytest.mark.asyncio
    async def test_no_auth_token_returns_false(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com",
            auth_token="",
        )
        records = [{"id": 1, "type": "event"}]
        result = await client.send_batch(records)
        assert result is False

    @pytest.mark.asyncio
    async def test_successful_send_200(self, sample_event_records):
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ingest",
            auth_token="test-token-1234",
        )

        mock_session = _make_mock_session({"status": 200})
        client._get_session = AsyncMock(return_value=mock_session)

        result = await client.send_batch(sample_event_records)
        assert result is True

        # Verify correct URL, headers and payload structure
        call_args = mock_session.post.call_args
        assert call_args[0][0] == "https://api.test.com/ingest/v2/data"
        assert call_args[1]["headers"]["Authorization"] == "Bearer test-token-1234"
        assert call_args[1]["headers"]["Content-Type"] == "application/json"
        payload = call_args[1]["json"]
        assert "records" in payload
        assert payload["source"] == "home_assistant"
        assert "sent_at" in payload

    @pytest.mark.asyncio
    async def test_successful_send_201(self, sample_event_records):
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ingest",
            auth_token="test-token-1234",
        )

        mock_session = _make_mock_session({"status": 201})
        client._get_session = AsyncMock(return_value=mock_session)

        result = await client.send_batch(sample_event_records)
        assert result is True

    @pytest.mark.asyncio
    async def test_client_error_returns_false(self, sample_event_records):
        """4xx errors should return False immediately (no retry)."""
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ingest",
            auth_token="test-token-1234",
        )

        mock_session = _make_mock_session({"status": 400, "text": "Bad Request"})
        client._get_session = AsyncMock(return_value=mock_session)

        result = await client.send_batch(sample_event_records)
        assert result is False
        assert mock_session.post.call_count == 1

    @pytest.mark.asyncio
    async def test_server_error_retries(self, sample_event_records):
        """5xx errors should trigger retries."""
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ingest",
            auth_token="test-token-1234",
        )

        mock_session = _make_mock_session({"status": 500})
        client._get_session = AsyncMock(return_value=mock_session)

        with patch("main.asyncio.sleep", new_callable=AsyncMock):
            result = await client.send_batch(sample_event_records)

        assert result is False
        from const import MAX_RETRIES
        assert mock_session.post.call_count == MAX_RETRIES

    @pytest.mark.asyncio
    async def test_timeout_retries(self, sample_event_records):
        """Timeout errors should trigger retries."""
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ingest",
            auth_token="test-token-1234",
        )

        mock_session = MagicMock()
        mock_session.post = MagicMock(side_effect=asyncio.TimeoutError())
        client._get_session = AsyncMock(return_value=mock_session)

        with patch("main.asyncio.sleep", new_callable=AsyncMock):
            result = await client.send_batch(sample_event_records)

        assert result is False
        from const import MAX_RETRIES
        assert mock_session.post.call_count == MAX_RETRIES

    @pytest.mark.asyncio
    async def test_network_error_retries(self, sample_event_records):
        """Network errors should trigger retries."""
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ingest",
            auth_token="test-token-1234",
        )

        mock_session = MagicMock()
        mock_session.post = MagicMock(
            side_effect=aiohttp.ClientError("Connection refused")
        )
        client._get_session = AsyncMock(return_value=mock_session)

        with patch("main.asyncio.sleep", new_callable=AsyncMock):
            result = await client.send_batch(sample_event_records)

        assert result is False
        from const import MAX_RETRIES
        assert mock_session.post.call_count == MAX_RETRIES

    @pytest.mark.asyncio
    async def test_unexpected_error_returns_false_no_retry(self, sample_event_records):
        """Unexpected exceptions should return False without retry."""
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ingest",
            auth_token="test-token-1234",
        )

        mock_session = MagicMock()
        mock_session.post = MagicMock(side_effect=RuntimeError("Something unexpected"))
        client._get_session = AsyncMock(return_value=mock_session)

        result = await client.send_batch(sample_event_records)
        assert result is False
        assert mock_session.post.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_with_exponential_backoff(self, sample_event_records):
        """Verify exponential backoff delays between retries."""
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ingest",
            auth_token="test-token-1234",
        )

        mock_session = MagicMock()
        mock_session.post = MagicMock(side_effect=asyncio.TimeoutError())
        client._get_session = AsyncMock(return_value=mock_session)

        sleep_calls = []

        async def mock_sleep(seconds):
            sleep_calls.append(seconds)

        with patch("main.asyncio.sleep", side_effect=mock_sleep):
            await client.send_batch(sample_event_records)

        from const import RETRY_DELAY_SECONDS
        assert sleep_calls[0] == RETRY_DELAY_SECONDS * 1
        assert sleep_calls[1] == RETRY_DELAY_SECONDS * 2

    @pytest.mark.asyncio
    async def test_server_error_then_success(self, sample_event_records):
        """Should succeed after transient server error."""
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ingest",
            auth_token="test-token-1234",
        )

        mock_session = _make_mock_session([
            {"status": 503},
            {"status": 200},
        ])
        client._get_session = AsyncMock(return_value=mock_session)

        with patch("main.asyncio.sleep", new_callable=AsyncMock):
            result = await client.send_batch(sample_event_records)

        assert result is True
        assert mock_session.post.call_count == 2

    @pytest.mark.asyncio
    async def test_auth_401_returns_false(self, sample_event_records):
        """401 should return False immediately."""
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ingest",
            auth_token="bad-token",
        )

        mock_session = _make_mock_session({"status": 401, "text": "Unauthorized"})
        client._get_session = AsyncMock(return_value=mock_session)

        result = await client.send_batch(sample_event_records)
        assert result is False
        assert mock_session.post.call_count == 1


class TestFetchCheckpoint:
    """Tests for CloudApiClient.fetch_checkpoint()."""

    @pytest.mark.asyncio
    async def test_no_auth_token_returns_none(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ingest",
            auth_token="",
        )
        result = await client.fetch_checkpoint()
        assert result is None

    @pytest.mark.asyncio
    async def test_successful_fetch_with_separate_cursors(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ingest",
            auth_token="test-token",
        )

        mock_session = _make_mock_session(
            {"status": 200, "json": {
                "last_timestamp": 1705320000.0,
                "last_event_timestamp": 1705320000.0,
                "last_state_timestamp": 1705319000.0,
            }},
            method="get",
        )
        client._get_session = AsyncMock(return_value=mock_session)

        result = await client.fetch_checkpoint()
        assert result == {"event": 1705320000.0, "state": 1705319000.0}

        # Verify correct URL and auth header
        call_args = mock_session.get.call_args
        assert call_args[0][0] == "https://api.test.com/ingest/data"
        assert call_args[1]["headers"]["Authorization"] == "Bearer test-token"

    @pytest.mark.asyncio
    async def test_successful_fetch_zero_timestamp(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ingest",
            auth_token="test-token",
        )

        mock_session = _make_mock_session(
            {"status": 200, "json": {
                "last_timestamp": 0.0,
                "last_event_timestamp": 0.0,
                "last_state_timestamp": 0.0,
            }},
            method="get",
        )
        client._get_session = AsyncMock(return_value=mock_session)

        result = await client.fetch_checkpoint()
        assert result == {"event": 0.0, "state": 0.0}

    @pytest.mark.asyncio
    async def test_fallback_to_legacy_last_timestamp(self):
        """Old server returning only last_timestamp should work."""
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ingest",
            auth_token="test-token",
        )

        mock_session = _make_mock_session(
            {"status": 200, "json": {"last_timestamp": 1705320000}},
            method="get",
        )
        client._get_session = AsyncMock(return_value=mock_session)

        result = await client.fetch_checkpoint()
        assert result == {"event": 1705320000.0, "state": 1705320000.0}

    @pytest.mark.asyncio
    async def test_missing_last_timestamp_returns_none(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ingest",
            auth_token="test-token",
        )

        mock_session = _make_mock_session(
            {"status": 200, "json": {"other_field": 42}},
            method="get",
        )
        client._get_session = AsyncMock(return_value=mock_session)

        result = await client.fetch_checkpoint()
        assert result is None

    @pytest.mark.asyncio
    async def test_client_error_returns_none(self):
        """4xx errors should return None immediately."""
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ingest",
            auth_token="test-token",
        )

        mock_session = _make_mock_session(
            {"status": 401, "text": "Unauthorized"},
            method="get",
        )
        client._get_session = AsyncMock(return_value=mock_session)

        result = await client.fetch_checkpoint()
        assert result is None
        assert mock_session.get.call_count == 1

    @pytest.mark.asyncio
    async def test_server_error_retries(self):
        """5xx errors should trigger retries."""
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ingest",
            auth_token="test-token",
        )

        mock_session = _make_mock_session({"status": 500}, method="get")
        client._get_session = AsyncMock(return_value=mock_session)

        with patch("main.asyncio.sleep", new_callable=AsyncMock):
            result = await client.fetch_checkpoint()

        assert result is None
        from const import MAX_RETRIES
        assert mock_session.get.call_count == MAX_RETRIES

    @pytest.mark.asyncio
    async def test_timeout_retries(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ingest",
            auth_token="test-token",
        )

        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=asyncio.TimeoutError())
        client._get_session = AsyncMock(return_value=mock_session)

        with patch("main.asyncio.sleep", new_callable=AsyncMock):
            result = await client.fetch_checkpoint()

        assert result is None
        from const import MAX_RETRIES
        assert mock_session.get.call_count == MAX_RETRIES

    @pytest.mark.asyncio
    async def test_network_error_retries(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ingest",
            auth_token="test-token",
        )

        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=aiohttp.ClientError("Connection refused"))
        client._get_session = AsyncMock(return_value=mock_session)

        with patch("main.asyncio.sleep", new_callable=AsyncMock):
            result = await client.fetch_checkpoint()

        assert result is None
        from const import MAX_RETRIES
        assert mock_session.get.call_count == MAX_RETRIES

    @pytest.mark.asyncio
    async def test_unexpected_error_returns_none_no_retry(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ingest",
            auth_token="test-token",
        )

        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=RuntimeError("Something unexpected"))
        client._get_session = AsyncMock(return_value=mock_session)

        result = await client.fetch_checkpoint()
        assert result is None
        assert mock_session.get.call_count == 1

    @pytest.mark.asyncio
    async def test_server_error_then_success(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ingest",
            auth_token="test-token",
        )

        mock_session = _make_mock_session(
            [
                {"status": 503},
                {"status": 200, "json": {
                    "last_timestamp": 1705320000.0,
                    "last_event_timestamp": 1705320000.0,
                    "last_state_timestamp": 1705320000.0,
                }},
            ],
            method="get",
        )
        client._get_session = AsyncMock(return_value=mock_session)

        with patch("main.asyncio.sleep", new_callable=AsyncMock):
            result = await client.fetch_checkpoint()

        assert result == {"event": 1705320000.0, "state": 1705320000.0}
        assert mock_session.get.call_count == 2

    @pytest.mark.asyncio
    async def test_invalid_timestamp_value_returns_none(self):
        """Non-numeric last_timestamp should return None."""
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ingest",
            auth_token="test-token",
        )

        mock_session = _make_mock_session(
            {"status": 200, "json": {"last_timestamp": "not-a-number"}},
            method="get",
        )
        client._get_session = AsyncMock(return_value=mock_session)

        result = await client.fetch_checkpoint()
        assert result is None


class TestVerifyToken:
    """Tests for CloudApiClient.verify_token()."""

    @pytest.mark.asyncio
    async def test_no_auth_token_returns_none(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ha",
            auth_token="",
        )
        result = await client.verify_token()
        assert result is None

    @pytest.mark.asyncio
    async def test_successful_verify(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ha",
            auth_token="test-token",
        )

        mock_session = _make_mock_session(
            {"status": 200, "json": {"status": "ok", "sync_interval_minutes": 10, "batch_size": 200}},
            method="post",
        )
        client._get_session = AsyncMock(return_value=mock_session)

        result = await client.verify_token()
        assert result == {"sync_interval_minutes": 10, "batch_size": 200}

        # Verify correct URL and auth header
        call_args = mock_session.post.call_args
        assert call_args[0][0] == "https://api.test.com/ha/verify"
        assert call_args[1]["headers"]["Authorization"] == "Bearer test-token"

    @pytest.mark.asyncio
    async def test_401_returns_none_no_retry(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ha",
            auth_token="bad-token",
        )

        mock_session = _make_mock_session(
            {"status": 401, "text": "Unauthorized"},
            method="post",
        )
        client._get_session = AsyncMock(return_value=mock_session)

        result = await client.verify_token()
        assert result is None
        assert mock_session.post.call_count == 1

    @pytest.mark.asyncio
    async def test_403_returns_none_no_retry(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ha",
            auth_token="suspended-token",
        )

        mock_session = _make_mock_session(
            {"status": 403, "text": "Forbidden"},
            method="post",
        )
        client._get_session = AsyncMock(return_value=mock_session)

        result = await client.verify_token()
        assert result is None
        assert mock_session.post.call_count == 1

    @pytest.mark.asyncio
    async def test_404_returns_none_no_retry(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ha",
            auth_token="unknown-token",
        )

        mock_session = _make_mock_session(
            {"status": 404, "text": "Not Found"},
            method="post",
        )
        client._get_session = AsyncMock(return_value=mock_session)

        result = await client.verify_token()
        assert result is None
        assert mock_session.post.call_count == 1

    @pytest.mark.asyncio
    async def test_5xx_retries(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ha",
            auth_token="test-token",
        )

        mock_session = _make_mock_session({"status": 500}, method="post")
        client._get_session = AsyncMock(return_value=mock_session)

        with patch("main.asyncio.sleep", new_callable=AsyncMock):
            result = await client.verify_token()

        assert result is None
        from const import MAX_RETRIES
        assert mock_session.post.call_count == MAX_RETRIES

    @pytest.mark.asyncio
    async def test_network_error_retries(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ha",
            auth_token="test-token",
        )

        mock_session = MagicMock()
        mock_session.post = MagicMock(side_effect=aiohttp.ClientError("Connection refused"))
        client._get_session = AsyncMock(return_value=mock_session)

        with patch("main.asyncio.sleep", new_callable=AsyncMock):
            result = await client.verify_token()

        assert result is None
        from const import MAX_RETRIES
        assert mock_session.post.call_count == MAX_RETRIES

    @pytest.mark.asyncio
    async def test_recovery_after_server_error(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ha",
            auth_token="test-token",
        )

        mock_session = _make_mock_session(
            [
                {"status": 503},
                {"status": 200, "json": {"status": "ok", "sync_interval_minutes": 5, "batch_size": 100}},
            ],
            method="post",
        )
        client._get_session = AsyncMock(return_value=mock_session)

        with patch("main.asyncio.sleep", new_callable=AsyncMock):
            result = await client.verify_token()

        assert result == {"sync_interval_minutes": 5, "batch_size": 100}
        assert mock_session.post.call_count == 2


class TestFetchConfig:
    """Tests for CloudApiClient.fetch_config()."""

    @pytest.mark.asyncio
    async def test_no_auth_token_returns_none(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ha",
            auth_token="",
        )
        result = await client.fetch_config()
        assert result is None

    @pytest.mark.asyncio
    async def test_successful_fetch(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ha",
            auth_token="test-token",
        )

        config_data = {
            "entity_filters": {"include": ["light.*"]},
            "feature_flags": {"sync_states": True},
            "settings": {"batch_size": 200},
        }
        mock_session = _make_mock_session(
            {"status": 200, "json": config_data},
            method="get",
        )
        client._get_session = AsyncMock(return_value=mock_session)

        result = await client.fetch_config()
        assert result == config_data

        # Verify correct URL and auth header
        call_args = mock_session.get.call_args
        assert call_args[0][0] == "https://api.test.com/ha/config"
        assert call_args[1]["headers"]["Authorization"] == "Bearer test-token"

    @pytest.mark.asyncio
    async def test_client_error_returns_none(self):
        """4xx errors should return None immediately."""
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ha",
            auth_token="test-token",
        )

        mock_session = _make_mock_session(
            {"status": 401, "text": "Unauthorized"},
            method="get",
        )
        client._get_session = AsyncMock(return_value=mock_session)

        result = await client.fetch_config()
        assert result is None
        assert mock_session.get.call_count == 1

    @pytest.mark.asyncio
    async def test_server_error_retries(self):
        """5xx errors should trigger retries."""
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ha",
            auth_token="test-token",
        )

        mock_session = _make_mock_session({"status": 500}, method="get")
        client._get_session = AsyncMock(return_value=mock_session)

        with patch("main.asyncio.sleep", new_callable=AsyncMock):
            result = await client.fetch_config()

        assert result is None
        from const import MAX_RETRIES
        assert mock_session.get.call_count == MAX_RETRIES

    @pytest.mark.asyncio
    async def test_timeout_retries(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ha",
            auth_token="test-token",
        )

        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=asyncio.TimeoutError())
        client._get_session = AsyncMock(return_value=mock_session)

        with patch("main.asyncio.sleep", new_callable=AsyncMock):
            result = await client.fetch_config()

        assert result is None
        from const import MAX_RETRIES
        assert mock_session.get.call_count == MAX_RETRIES

    @pytest.mark.asyncio
    async def test_network_error_retries(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ha",
            auth_token="test-token",
        )

        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=aiohttp.ClientError("Connection refused"))
        client._get_session = AsyncMock(return_value=mock_session)

        with patch("main.asyncio.sleep", new_callable=AsyncMock):
            result = await client.fetch_config()

        assert result is None
        from const import MAX_RETRIES
        assert mock_session.get.call_count == MAX_RETRIES

    @pytest.mark.asyncio
    async def test_unexpected_error_returns_none_no_retry(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ha",
            auth_token="test-token",
        )

        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=RuntimeError("Something unexpected"))
        client._get_session = AsyncMock(return_value=mock_session)

        result = await client.fetch_config()
        assert result is None
        assert mock_session.get.call_count == 1

    @pytest.mark.asyncio
    async def test_server_error_then_success(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ha",
            auth_token="test-token",
        )

        config_data = {"feature_flags": {"sync_states": True}}
        mock_session = _make_mock_session(
            [
                {"status": 503},
                {"status": 200, "json": config_data},
            ],
            method="get",
        )
        client._get_session = AsyncMock(return_value=mock_session)

        with patch("main.asyncio.sleep", new_callable=AsyncMock):
            result = await client.fetch_config()

        assert result == config_data
        assert mock_session.get.call_count == 2


class TestRateLimitHandling:
    """Tests for 429 (Rate Limited) handling across all CloudApiClient methods."""

    @pytest.mark.asyncio
    async def test_fetch_checkpoint_retries_on_429(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ha",
            auth_token="test-token",
        )
        mock_session = _make_mock_session(
            [
                {"status": 429, "headers": {"Retry-After": "5"}},
                {"status": 200, "json": {
                    "last_timestamp": 1234.5,
                    "last_event_timestamp": 1234.5,
                    "last_state_timestamp": 1234.5,
                }},
            ],
            method="get",
        )
        client._get_session = AsyncMock(return_value=mock_session)

        with patch("main.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await client.fetch_checkpoint()

        assert result == {"event": 1234.5, "state": 1234.5}
        assert mock_session.get.call_count == 2
        mock_sleep.assert_any_call(5)

    @pytest.mark.asyncio
    async def test_send_batch_retries_on_429(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ha",
            auth_token="test-token",
        )
        mock_session = _make_mock_session(
            [
                {"status": 429, "headers": {"Retry-After": "10"}},
                {"status": 201},
            ],
            method="post",
        )
        client._get_session = AsyncMock(return_value=mock_session)

        with patch("main.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await client.send_batch([{"id": 1, "type": "event"}])

        assert result is True
        assert mock_session.post.call_count == 2
        mock_sleep.assert_any_call(10)

    @pytest.mark.asyncio
    async def test_verify_token_retries_on_429(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ha",
            auth_token="test-token",
        )
        mock_session = _make_mock_session(
            [
                {"status": 429, "headers": {"Retry-After": "3"}},
                {"status": 200, "json": {"sync_interval_minutes": 5, "batch_size": 100}},
            ],
            method="post",
        )
        client._get_session = AsyncMock(return_value=mock_session)

        with patch("main.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await client.verify_token()

        assert result == {"sync_interval_minutes": 5, "batch_size": 100}
        assert mock_session.post.call_count == 2
        mock_sleep.assert_any_call(3)

    @pytest.mark.asyncio
    async def test_fetch_config_retries_on_429(self):
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ha",
            auth_token="test-token",
        )
        config_data = {"feature_flags": {"sync_states": True}}
        mock_session = _make_mock_session(
            [
                {"status": 429, "headers": {"Retry-After": "7"}},
                {"status": 200, "json": config_data},
            ],
            method="get",
        )
        client._get_session = AsyncMock(return_value=mock_session)

        with patch("main.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await client.fetch_config()

        assert result == config_data
        assert mock_session.get.call_count == 2
        mock_sleep.assert_any_call(7)

    @pytest.mark.asyncio
    async def test_429_uses_default_retry_when_no_header(self):
        """When Retry-After header is missing, fall back to RETRY_DELAY_SECONDS."""
        client = CloudApiClient(
            api_endpoint="https://api.test.com/ha",
            auth_token="test-token",
        )
        mock_session = _make_mock_session(
            [
                {"status": 429, "headers": {}},
                {"status": 200, "json": {
                    "last_timestamp": 99.0,
                    "last_event_timestamp": 99.0,
                    "last_state_timestamp": 99.0,
                }},
            ],
            method="get",
        )
        client._get_session = AsyncMock(return_value=mock_session)

        with patch("main.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await client.fetch_checkpoint()

        assert result == {"event": 99.0, "state": 99.0}
        # Should use RETRY_DELAY_SECONDS (5) as default
        mock_sleep.assert_any_call(5)
