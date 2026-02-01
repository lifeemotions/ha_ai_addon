#!/usr/bin/with-contenv bashio
# ==============================================================================
# Home Assistant Add-on: HA Event Extractor
# Runs the event extractor script
# ==============================================================================

# Read configuration from add-on options
export CLOUD_AUTH_TOKEN=$(bashio::config 'cloud_auth_token')
export SYNC_INTERVAL_MINUTES=$(bashio::config 'sync_interval_minutes')
export API_ENDPOINT=$(bashio::config 'api_endpoint')
export BATCH_SIZE=$(bashio::config 'batch_size')

bashio::log.info "Starting HA Event Extractor..."
bashio::log.info "Sync interval: ${SYNC_INTERVAL_MINUTES} minutes"
bashio::log.info "Batch size: ${BATCH_SIZE}"

# Run the main Python script
exec python3 /app/main.py
