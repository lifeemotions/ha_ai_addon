"""Configuration constants for Life Emotions AI."""

import logging
import os

logger = logging.getLogger("lifeemotions_ai_addon")

# Database configuration
DATABASE_PATH = "/config/home-assistant_v2.db"

# API configuration
API_ENDPOINT = "https://api.life-emotions.com/ha"
CLOUD_AUTH_TOKEN = os.environ.get("CLOUD_AUTH_TOKEN", "")

# Remote config
CONFIG_FILE_PATH = "/config/lifeemotions_config.json"
CONFIG_REFRESH_INTERVAL_MINUTES = 60


def _parse_int_env(name: str, default: int) -> int:
    """Parse an integer environment variable, falling back to default on error."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning(f"Invalid value for {name}={raw!r}, using default {default}")
        return default


# Sync configuration
DEFAULT_SYNC_INTERVAL_MINUTES = 5
SYNC_INTERVAL_MINUTES = _parse_int_env("SYNC_INTERVAL_MINUTES", DEFAULT_SYNC_INTERVAL_MINUTES)

# Batch configuration
DEFAULT_BATCH_SIZE = 100
BATCH_SIZE = _parse_int_env("BATCH_SIZE", DEFAULT_BATCH_SIZE)

# Request configuration
REQUEST_TIMEOUT_SECONDS = 30
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 5

# Origin identifier
ORIGIN = "local"

# Model management
MODEL_DIR = "/config/lifeemotions_model"
MODEL_VERSION_FILE = "/config/lifeemotions_model/version.json"
MODEL_ENTRY_POINT = "predict.py"
MODEL_REQUIREMENTS_FILE = "requirements.txt"
MODEL_DOWNLOAD_TIMEOUT_SECONDS = 300
PREDICTION_TIMEOUT_SECONDS = 600

# Table names
EVENTS_TABLE = "events"
STATES_TABLE = "states"
EVENT_DATA_TABLE = "event_data"
STATE_ATTRIBUTES_TABLE = "state_attributes"
