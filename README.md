# Life Emotions AI Add-on

This Home Assistant Add-on extracts historical device events and state changes from the local SQLite database and streams them to a remote Cloud API.

## Features

- **Database Access**: Reads from the Home Assistant SQLite database (`/config/home-assistant_v2.db`)
- **Checkpoint System**: Fetches the last processed timestamp from the Cloud API to avoid sending duplicate data
- **Batch Processing**: Efficiently queries data in configurable batch sizes (default: 100 rows)
- **Resilient API Calls**: Automatic retry logic with exponential backoff for network failures
- **Bearer Token Authentication**: Secure authentication with the remote API

## Installation

1. Add this repository to your Home Assistant Add-on Store
2. Install the "Life Emotions AI" add-on
3. Configure the add-on with your Cloud API credentials

## Configuration

| Option                  | Type   | Default | Description                             |
| ----------------------- | ------ | ------- | --------------------------------------- |
| `cloud_auth_token`      | string | `""`    | Bearer token for API authentication     |
| `sync_interval_minutes` | int    | `5`     | How often to sync data (1-1440 minutes) |
| `batch_size`            | int    | `100`   | Number of records per batch (10-1000)   |

### Example Configuration

```yaml
cloud_auth_token: "your-secret-token-here"
sync_interval_minutes: 5
batch_size: 100
```

## Data Schema

The add-on sends data to the Cloud API in the following JSON format:

```json
{
  "records": [
    {
      "id": 12345,
      "type": "event|state",
      "timestamp": "2025-12-19T10:30:00.000000Z",
      "raw_timestamp": 1734605400.0,
      "entity_id": "light.living_room",
      "event_type": "state_changed",
      "state": "on",
      "attributes": {
        "brightness": 255,
        "friendly_name": "Living Room Light"
      },
      "origin": "local"
    }
  ],
  "source": "home_assistant",
  "sent_at": "2025-12-19T10:30:05.000000Z"
}
```

## Checkpoint Persistence

The add-on fetches the last processed timestamp from the Cloud API on each sync cycle. The API returns:

```json
{
  "last_timestamp": 1734605400.0
}
```

All events and states with timestamps after this value are fetched and sent. This ensures no duplicate data is sent across sync cycles.

## Logs

View logs in the Home Assistant Supervisor:

```
Settings → Add-ons → HA Event Extractor → Logs
```

## Troubleshooting

### "Recorder is using '{dialect}'" error

- The add-on automatically detects the Recorder's database engine at startup
- This add-on only supports SQLite (the default Home Assistant database)
- If you are using MariaDB or PostgreSQL, switch the Recorder back to SQLite

### "Database not found" error

- The Recorder may not be using SQLite, or the database has not been created yet
- Verify Home Assistant Recorder is enabled and using the default SQLite database

### "No authentication token configured" error

- Set the `cloud_auth_token` option in the add-on configuration

### Network timeout errors

- Check your network connectivity
- Verify the API endpoint is accessible
- The add-on will automatically retry failed requests

## Development

### Local Testing

```bash
# Set environment variables
export CLOUD_AUTH_TOKEN="test-token"
export SYNC_INTERVAL_MINUTES=1
export BATCH_SIZE=10

# Run the script
python main.py
```

### Building the Docker Image

```bash
docker build -t lifeemotions-ai-addon .
```

## License

MIT License
