# CLAUDE.md

## Project Overview

**Life Emotions AI Add-on** — A Home Assistant add-on that extracts historical device events and state changes from the local SQLite database and streams them to a remote Cloud API.

**Requires Home Assistant 2023.4+** (enforced via `config.yaml`). Only supports the modern database schema where event types are in a separate `event_types` table.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Home Assistant                           │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  SQLite DB: /config/home-assistant_v2.db            │   │
│  │  - events table (event_type_id → event_types)       │   │
│  │  - states table (metadata_id → states_meta)         │   │
│  │  - event_data, state_attributes (shared JSON)       │   │
│  └─────────────────────────────────────────────────────┘   │
│                           │                                 │
│                           ▼                                 │
│  ┌─────────────────────────────────────────────────────┐   │
│  │  Add-on Container                                   │   │
│  │  ┌───────────────┐  ┌───────────────────────────┐   │   │
│  │  │DatabaseReader │  │    CloudApiClient         │   │   │
│  │  │ fetch_events()│  │ fetch_checkpoint()        │   │   │
│  │  │ fetch_states()│  │ send_batch()              │   │   │
│  │  └───────────────┘  └───────────────────────────┘   │   │
│  │          │                      │                   │   │
│  │          └──────┬───────────────┘                   │   │
│  │                 ▼                                   │   │
│  │       ┌─────────────────┐                          │   │
│  │       │ EventExtractor  │                          │   │
│  │       │ sync_cycle()    │                          │   │
│  │       │ run()           │                          │   │
│  │       └─────────────────┘                          │   │
│  └─────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
                   ┌───────────────┐
                   │   Cloud API   │
                   │ POST /events  │
                   │ GET /checkpoint│
                   └───────────────┘
```

## Key Files

| File | Purpose |
|------|---------|
| `main.py` | Core logic: `DatabaseReader`, `CloudApiClient`, `EventExtractor` |
| `const.py` | Configuration constants (env vars, defaults) |
| `ha_api.py` | Home Assistant REST API client (unused currently) |
| `run.sh` | Entrypoint script |
| `config.yaml` | Add-on manifest (name, version, options, min HA version) |
| `Dockerfile` | Container build definition |

## Database Schema (HA 2023.4+)

**Events query** (joins `events` → `event_types` → `event_data`):
```sql
SELECT e.event_id, et.event_type, e.time_fired_ts, e.origin_idx, ed.shared_data
FROM events e
LEFT JOIN event_types et ON e.event_type_id = et.event_type_id
LEFT JOIN event_data ed ON e.data_id = ed.data_id
WHERE e.time_fired_ts > ?
```

**States query** (joins `states` → `states_meta` → `state_attributes`):
```sql
SELECT s.state_id, sm.entity_id, s.state, s.last_updated_ts, sa.shared_attrs
FROM states s
LEFT JOIN states_meta sm ON s.metadata_id = sm.metadata_id
LEFT JOIN state_attributes sa ON s.attributes_id = sa.attributes_id
WHERE s.last_updated_ts > ?
```

**Note**: `state_changed` events are NOT recorded in the `events` table in HA 2023.4+. State changes only appear in the `states` table.

## Python Environment

- Python version: 3.14.2
- Virtual environment: `.venv/` (activate with `source .venv/bin/activate`)
- Run Python: `.venv/bin/python`

## Running Tests

Use the test runner script for convenience:

```bash
./scripts/run_tests.sh          # Run all tests (106 total)
./scripts/run_tests.sh unit     # Run only unit tests (94 tests, fast)
./scripts/run_tests.sh ha       # Run only HA Docker integration tests (3 tests)
./scripts/run_tests.sh addon    # Run only add-on installation tests (9 tests, ~3 min)
./scripts/run_tests.sh fast     # Run unit + HA Docker tests (skip slow addon tests)
```

Or run directly with pytest:

### Unit Tests (94 tests, fast, no Docker)
```bash
.venv/bin/python -m pytest tests/ -v \
    --ignore=tests/test_integration_addon_install.py \
    --ignore=tests/test_integration_ha_docker.py
```

### HA Docker Integration Tests (3 tests)
```bash
.venv/bin/python -m pytest tests/test_integration_ha_docker.py -v
```

These tests:
- Start a real HA Core container via `pytest-docker`
- Complete onboarding programmatically (`POST /api/onboarding/users` → `POST /auth/token`)
- Simulate activity via REST API (`POST /api/states/<entity_id>`)
- Copy the SQLite DB out of the container (including WAL files)
- Run `DatabaseReader` against the real DB

### Add-on Installation Integration Tests (9 tests, ~3 min)
```bash
.venv/bin/python -m pytest tests/test_integration_addon_install.py -v
```

These tests:
- Start a full HA Supervisor environment (Docker-in-Docker)
- Verify the add-on appears in the local add-on store
- Install the add-on via Supervisor CLI
- Verify configuration options and HA version requirements

**Note**: Integration tests must be run separately because they use different docker-compose files. Running them together in a single pytest session will cause fixture conflicts.

### Test Structure

| Test File | Tests | Coverage |
|-----------|-------|----------|
| `test_database_reader.py` | 30 | `DatabaseReader` fetch/parse methods |
| `test_cloud_api_client.py` | 30 | `CloudApiClient` send/checkpoint with retry logic |
| `test_event_extractor.py` | 18 | `EventExtractor` sync cycle orchestration |
| `test_const.py` | 16 | Environment variable parsing |
| `test_integration_ha_docker.py` | 3 | End-to-end with real HA Core container |
| `test_integration_addon_install.py` | 9 | Add-on installation into HA Supervisor |

- Test framework: pytest with pytest-asyncio, pytest-docker
- Config: `pytest.ini` (asyncio_mode=auto)
- Integration tests marked with `@pytest.mark.integration`
- Slow tests marked with `@pytest.mark.slow`

## Bug Fixing Workflow

1. **Reproduce first**: Write a failing test that demonstrates the bug before touching any production code.
2. **Fix**: Only after the test confirms the bug, work on the fix.
3. **Verify**: Run the failing test again to confirm it passes with the fix.

## Add-on Configuration Options

Configured via HA UI, defined in `config.yaml`:
- `cloud_auth_token`: Bearer token for Cloud API authentication
- `sync_interval_minutes`: How often to sync (1-1440, default 5)
- `batch_size`: Records per API call (10-1000, default 100)
