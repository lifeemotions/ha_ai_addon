# Life Emotions AI Add-on

This Home Assistant Add-on extracts historical device events and state changes from the local SQLite database and streams them to a remote Cloud API.

## Features

- **Database Access**: Reads from the Home Assistant SQLite database (`/config/home-assistant_v2.db`)
- **Checkpoint System**: Tracks the last processed event/state ID to avoid duplicate data on restart
- **Batch Processing**: Efficiently queries data in configurable batch sizes (default: 100 rows)
- **Resilient API Calls**: Automatic retry logic with exponential backoff for network failures
- **Bearer Token Authentication**: Secure authentication with the remote API

## Installation

1. Add this repository to your Home Assistant Add-on Store
2. Install the "Life Emotions AI" add-on
3. Configure the add-on with your Cloud API credentials

## Configuration

| Option                  | Type   | Default                                   | Description                             |
| ----------------------- | ------ | ----------------------------------------- | --------------------------------------- |
| `cloud_auth_token`      | string | `""`                                      | Bearer token for API authentication     |
| `sync_interval_minutes` | int    | `5`                                       | How often to sync data (1-1440 minutes) |
| `api_endpoint`          | url    | `https://api.example-cloud.com/v1/ingest` | Cloud API endpoint URL                  |
| `batch_size`            | int    | `100`                                     | Number of records per batch (10-1000)   |

### Example Configuration

```yaml
cloud_auth_token: "your-secret-token-here"
sync_interval_minutes: 5
api_endpoint: "https://api.your-cloud.com/v1/ingest"
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

## API Requirements

Your Cloud API endpoint should:

1. Accept POST requests with JSON body
2. Accept `Authorization: Bearer <token>` header
3. Return HTTP 200 or 201 on success
4. Handle the payload schema described above

## Checkpoint Persistence

The add-on maintains a checkpoint file at `/data/checkpoint.json` containing:

```json
{
  "last_event_id": 12345,
  "last_state_id": 67890
}
```

This ensures data is not duplicated if the add-on restarts.

## Logs

View logs in the Home Assistant Supervisor:

```
Settings → Add-ons → HA Event Extractor → Logs
```

## Troubleshooting

### "Database not found" error

- Ensure the add-on has `map: config:ro` in the configuration
- Verify Home Assistant Recorder is enabled and using SQLite

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
export API_ENDPOINT="http://localhost:8080/ingest"
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
