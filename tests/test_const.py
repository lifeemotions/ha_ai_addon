"""Tests for const.py configuration module."""

import os
from unittest.mock import patch

import pytest


class TestConstantsDefaults:
    """Tests for default constant values."""

    def test_api_endpoint(self):
        from const import API_ENDPOINT
        assert API_ENDPOINT == "https://api.life-emotions.com/ha"

    def test_heartbeat_interval(self):
        from const import HEARTBEAT_INTERVAL_SECONDS
        assert HEARTBEAT_INTERVAL_SECONDS == 300

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

    def test_ha_config_dir(self):
        from const import HA_CONFIG_DIR
        assert HA_CONFIG_DIR == "/homeassistant"

    def test_database_path(self):
        from const import DATABASE_PATH, HA_CONFIG_DIR
        assert DATABASE_PATH == f"{HA_CONFIG_DIR}/home-assistant_v2.db"

    def test_config_file_path(self):
        from const import CONFIG_FILE_PATH, HA_CONFIG_DIR
        assert CONFIG_FILE_PATH == f"{HA_CONFIG_DIR}/lifeemotions_config.json"

    def test_config_refresh_interval(self):
        from const import CONFIG_REFRESH_INTERVAL_MINUTES
        assert CONFIG_REFRESH_INTERVAL_MINUTES == 60

    def test_model_dir(self):
        from const import MODEL_DIR, HA_CONFIG_DIR
        assert MODEL_DIR == f"{HA_CONFIG_DIR}/lifeemotions_model"

    def test_model_version_file(self):
        from const import MODEL_VERSION_FILE, MODEL_DIR
        assert MODEL_VERSION_FILE == f"{MODEL_DIR}/version.json"

    def test_model_entry_point(self):
        from const import MODEL_ENTRY_POINT
        assert MODEL_ENTRY_POINT == "predict.py"

    def test_model_requirements_file(self):
        from const import MODEL_REQUIREMENTS_FILE
        assert MODEL_REQUIREMENTS_FILE == "requirements.txt"

    def test_model_download_timeout(self):
        from const import MODEL_DOWNLOAD_TIMEOUT_SECONDS
        assert MODEL_DOWNLOAD_TIMEOUT_SECONDS == 300

    def test_prediction_timeout(self):
        from const import PREDICTION_TIMEOUT_SECONDS
        assert PREDICTION_TIMEOUT_SECONDS == 600

