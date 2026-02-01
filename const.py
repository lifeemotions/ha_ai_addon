"""Configuration constants for Life Emotions HA Plugin."""

import logging
import os

logger = logging.getLogger("lifeemotions_ha_plugin")

# Database configuration
DATABASE_PATH = "/config/home-assistant_v2.db"

# Checkpoint file location (persistent storage)
CHECKPOINT_FILE = "/data/checkpoint.json"

# API configuration
DEFAULT_API_ENDPOINT = "https://api.example-cloud.com/v1/ingest"
API_ENDPOINT = os.environ.get("API_ENDPOINT", DEFAULT_API_ENDPOINT)
CLOUD_AUTH_TOKEN = os.environ.get("CLOUD_AUTH_TOKEN", "")


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

# Table names
EVENTS_TABLE = "events"
STATES_TABLE = "states"
EVENT_DATA_TABLE = "event_data"
STATE_ATTRIBUTES_TABLE = "state_attributes"
