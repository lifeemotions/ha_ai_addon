"""Configuration constants for Life Emotions AI."""

import logging
import os

logger = logging.getLogger("lifeemotions_ai_addon")

# Database configuration
DATABASE_PATH = "/config/home-assistant_v2.db"

# API configuration
API_ENDPOINT = os.environ.get("API_ENDPOINT", "https://api.life-emotions.com/ha")
CLOUD_AUTH_TOKEN = os.environ.get("CLOUD_AUTH_TOKEN", "")

# Remote config
CONFIG_FILE_PATH = "/config/lifeemotions_config.json"
CONFIG_REFRESH_INTERVAL_MINUTES = 60


# Heartbeat configuration
HEARTBEAT_INTERVAL_SECONDS = 300

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
