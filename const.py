"""Configuration constants for Life Emotions HA Plugin."""

import os

# Database configuration
DATABASE_PATH = "/config/home-assistant_v2.db"

# Checkpoint file location (persistent storage)
CHECKPOINT_FILE = "/data/checkpoint.json"

# API configuration
DEFAULT_API_ENDPOINT = "https://api.example-cloud.com/v1/ingest"
API_ENDPOINT = os.environ.get("API_ENDPOINT", DEFAULT_API_ENDPOINT)
CLOUD_AUTH_TOKEN = os.environ.get("CLOUD_AUTH_TOKEN", "")

# Sync configuration
DEFAULT_SYNC_INTERVAL_MINUTES = 5
SYNC_INTERVAL_MINUTES = int(os.environ.get("SYNC_INTERVAL_MINUTES", DEFAULT_SYNC_INTERVAL_MINUTES))

# Batch configuration
DEFAULT_BATCH_SIZE = 100
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", DEFAULT_BATCH_SIZE))

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
