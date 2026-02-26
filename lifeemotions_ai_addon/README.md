# Life Emotions AI Add-on

Extracts historical device events from Home Assistant's local SQLite database and streams them to the Life Emotions Cloud API. Manages a local XGBoost AI model for predictions.

## Installation

1. Add this repository to your Home Assistant add-on store:
   **Settings > Add-ons > Add-on Store > ... (menu) > Repositories**
2. Enter the repository URL: `https://github.com/lifeemotions/ha_ai_addon`
3. Find "Life Emotions AI" in the store and click **Install**

## Configuration

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `cloud_auth_token` | string | `""` | Bearer token for Cloud API authentication |
| `sync_interval_minutes` | int | `5` | How often to sync events (1-1440 minutes) |
| `batch_size` | int | `100` | Records per API call (10-1000) |

## Requirements

- Home Assistant 2023.4 or newer (requires modern database schema)
- A valid Life Emotions cloud authentication token

## Support

For issues and feature requests, visit:
https://github.com/lifeemotions/ha_ai_addon/issues
