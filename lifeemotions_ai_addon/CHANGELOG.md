# Changelog

## [1.3.1] - 2026-04-21

### Fixed
- **Stop sending events to the cloud.** 1.3.0 kept calling
  `_process_events`, but the v2 `/ha/data` endpoint only stores state
  records (events have been silently dropped server-side since
  migration 012). On a fresh addon start that meant the sync loop
  would rapid-fire through the entire HA event history trying to
  "catch up" a cursor that never advances persistently — burning
  rate-limit budget and producing 429s with nothing stored.
- **Filter state records by the enabled-entity allowlist before
  sending.** Prior versions relied on the server to drop records for
  entities where `sync_enabled = false`. That still wasted bandwidth
  and counted against the site's rate limit. Each sync cycle now
  fetches the enabled-entity set from `GET /ha/v2/entities/cursors`
  and drops disabled-entity records locally in the addon.
- If `fetch_enabled_entities` returns an empty set, the addon logs
  once and skips the cycle (nothing to sync — user hasn't enabled
  anything in the console yet).

### Requires
- Cloud with `/ha/v2/entities/cursors` live (shipped in PR #111).

## [1.3.0] - 2026-04-20

### Added
- Entity manifest sync. Once per config-refresh cycle the addon reads the HA
  entity registry (via WebSocket) and POSTs the entity list — with labels — to
  the cloud. The cloud uses this to drive its opt-in sync model: labeled
  entities auto-enable; unlabeled entities stay disabled until the user
  toggles them in the console UI.

### Changed
- Data sync now targets the v2 API (`POST /ha/v2/data`). The server drops
  records for disabled entities, so only sync-enabled entities land in the
  time-series store.
- 5-minute bucketing now applies to **all** state types. Previously only
  numeric states were aggregated (avg/bucket) and non-numeric states passed
  through unchanged. Now non-numeric states (on/off, text, `unavailable`,
  `unknown`) are collapsed to the last value in each 5-min bucket. This
  matches the cloud-side safety net that drops records arriving <5 min after
  the last record for the same entity.

### Requires
- Cloud API with the v2 endpoints deployed (console PR #111+).

## [1.0.0] - 2026-02-26

### Added
- Initial release
- Home Assistant event and state synchronization to cloud API
- XGBoost model management (download, load, predict)
- Configurable sync intervals and batch sizes
- Bearer token authentication
