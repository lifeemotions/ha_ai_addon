"""Tests for const.py configuration module."""

import os
from unittest.mock import patch

import pytest


class TestParseIntEnv:
    """Tests for _parse_int_env helper function."""

    def _import_parse_int_env(self):
        """Import fresh to avoid module-level caching issues."""
        from const import _parse_int_env
        return _parse_int_env

    def test_returns_default_when_env_not_set(self):
        parse = self._import_parse_int_env()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TEST_INT_VAR", None)
            result = parse("TEST_INT_VAR", 42)
        assert result == 42

    def test_parses_valid_integer(self):
        parse = self._import_parse_int_env()
        with patch.dict(os.environ, {"TEST_INT_VAR": "10"}):
            result = parse("TEST_INT_VAR", 42)
        assert result == 10

    def test_parses_zero(self):
        parse = self._import_parse_int_env()
        with patch.dict(os.environ, {"TEST_INT_VAR": "0"}):
            result = parse("TEST_INT_VAR", 42)
        assert result == 0

    def test_parses_negative_integer(self):
        parse = self._import_parse_int_env()
        with patch.dict(os.environ, {"TEST_INT_VAR": "-5"}):
            result = parse("TEST_INT_VAR", 42)
        assert result == -5

    def test_returns_default_for_non_numeric_string(self):
        parse = self._import_parse_int_env()
        with patch.dict(os.environ, {"TEST_INT_VAR": "abc"}):
            result = parse("TEST_INT_VAR", 42)
        assert result == 42

    def test_returns_default_for_float_string(self):
        parse = self._import_parse_int_env()
        with patch.dict(os.environ, {"TEST_INT_VAR": "3.14"}):
            result = parse("TEST_INT_VAR", 42)
        assert result == 42

    def test_returns_default_for_empty_string(self):
        parse = self._import_parse_int_env()
        with patch.dict(os.environ, {"TEST_INT_VAR": ""}):
            result = parse("TEST_INT_VAR", 42)
        assert result == 42

    def test_returns_default_for_whitespace_string(self):
        parse = self._import_parse_int_env()
        with patch.dict(os.environ, {"TEST_INT_VAR": "  "}):
            result = parse("TEST_INT_VAR", 42)
        assert result == 42


class TestConstantsDefaults:
    """Tests for default constant values."""

    def test_default_api_endpoint(self):
        from const import DEFAULT_API_ENDPOINT
        assert DEFAULT_API_ENDPOINT == "https://api.example-cloud.com/v1/ingest"

    def test_default_sync_interval(self):
        from const import DEFAULT_SYNC_INTERVAL_MINUTES
        assert DEFAULT_SYNC_INTERVAL_MINUTES == 5

    def test_default_batch_size(self):
        from const import DEFAULT_BATCH_SIZE
        assert DEFAULT_BATCH_SIZE == 100

    def test_request_timeout(self):
        from const import REQUEST_TIMEOUT_SECONDS
        assert REQUEST_TIMEOUT_SECONDS == 30

    def test_max_retries(self):
        from const import MAX_RETRIES
        assert MAX_RETRIES == 3

    def test_retry_delay(self):
        from const import RETRY_DELAY_SECONDS
        assert RETRY_DELAY_SECONDS == 5

    def test_origin(self):
        from const import ORIGIN
        assert ORIGIN == "local"

    def test_database_path(self):
        from const import DATABASE_PATH
        assert DATABASE_PATH == "/config/home-assistant_v2.db"

