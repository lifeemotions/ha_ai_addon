# Life Emotions AI Add-on

This Home Assistant Add-on extracts historical device events and state changes from the local SQLite database and streams them to a remote Cloud API.

> **Requires Home Assistant 2023.4 or later.** This add-on only supports the modern database schema introduced in HA 2023.4, where event types are stored in a separate `event_types` table and `state_changed` events are no longer recorded in the `events` table. The add-on will not install on older HA versions.

## Features

- **Database Access**: Reads from the Home Assistant SQLite database (`/config/home-assistant_v2.db`)
- **Entity-Scoped Sync**: Sends the Home Assistant entity registry to the cloud, then syncs only server-enabled entities
- **Cursor System**: Fetches per-entity forward cursors from the Cloud API to avoid sending duplicate state data
- **Batch Processing**: Efficiently queries data in server-controlled batch sizes (default: 100 rows)
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

`sync_interval_minutes` and `batch_size` are no longer Home Assistant add-on options. The add-on calls `POST /ha/verify` every 5 minutes and uses the server-controlled sync interval and batch size returned by the console backend.

### Example Configuration

```yaml
cloud_auth_token: "your-secret-token-here"
```

## Sync Flow

The current add-on uses the v2 entity-scoped sync contract:

1. `POST /ha/verify` verifies the bearer token and returns server-controlled sync settings.
2. `GET /ha/config` fetches model, filter, and prediction configuration and caches it locally.
3. `POST /ha/v2/entities/manifest` sends Home Assistant entity IDs, friendly names, domains, and labels.
4. `GET /ha/v2/entities/cursors` returns only sync-enabled entities and their forward cursors.
5. The add-on reads states from SQLite per enabled entity and sends them with cursor updates to `POST /ha/v2/data`.

Home Assistant events are read by older compatibility paths, but the current v2 sync loop persists state records only. Enable entities through the console UI or by applying Home Assistant labels that the console treats as sync-enabled.

### QA Expectations

- Startup should verify the token, fetch config, and attempt a manifest sync.
- If `GET /ha/v2/entities/cursors` returns no entities, the add-on should log
  that nothing is enabled and skip the SQLite state read for that cycle.
- State sync should run per enabled entity and send `forward_cursor_ts` updates
  in the same `/ha/v2/data` request as the accepted batch.
- Disabled entities should not be sent by the current add-on loop; if they are
  sent by a test harness, the cloud should report them as dropped.
- Event records are compatibility examples only for the current v2 release; the
  active sync loop sends state records.

## Data Schema

The active v2 sync loop sends **states** (entity state changes) to the Cloud API.
The record envelope is still event/state-shaped for compatibility with older
paths and tests.

> **Note:** Since HA 2023.4+, `state_changed` events are no longer recorded in the events table. State changes are only stored in the states table. The events table contains system events like `homeassistant_start`, `automation_triggered`, `call_service`, etc.

### State record (entity state changes)

```json
{
  "records": [
    {
      "id": 1,
      "type": "state",
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

### Event record (system/automation events)

```json
{
  "records": [
    {
      "id": 1,
      "type": "event",
      "timestamp": "2025-12-19T10:30:00.000000Z",
      "raw_timestamp": 1734605400.0,
      "entity_id": "",
      "event_type": "automation_triggered",
      "state": null,
      "attributes": {
        "entity_id": "automation.morning"
      },
      "origin": "local"
    }
  ],
  "source": "home_assistant",
  "sent_at": "2025-12-19T10:30:05.000000Z"
}
```

## Cursor Persistence

The add-on fetches enabled entities and their per-entity forward cursors from the Cloud API on each sync cycle. The API returns:

```json
{
  "entities": [
    {
      "entity_id": "sensor.kitchen_temperature",
      "forward_cursor_ts": "2024-01-31T12:00:00.000Z"
    }
  ]
}
```

For each enabled entity, states with timestamps after `forward_cursor_ts` are fetched and sent. After a successful batch, the add-on sends updated cursor values in the same `/ha/v2/data` request.

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
export API_ENDPOINT="https://api.life-emotions.com/ha"

# Run the script
python main.py
```

### Building the Docker Image

```bash
docker build -t lifeemotions-ai-addon .
```

## License

MIT License
