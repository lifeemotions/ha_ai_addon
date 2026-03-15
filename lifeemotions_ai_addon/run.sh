#!/usr/bin/with-contenv bashio
# ==============================================================================
# Home Assistant Add-on: Life Emotions AI
# Runs the add-on script
# ==============================================================================

# Read configuration from add-on options
export CLOUD_AUTH_TOKEN=$(bashio::config 'cloud_auth_token')

bashio::log.info "Starting Life Emotions AI..."

# Run the main Python script
exec python3 /app/main.py
