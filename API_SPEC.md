# Cloud API Specification

Base URL: `https://api.life-emotions.com/ha`

All endpoints require `Authorization: Bearer <cloud_auth_token>`.

---

## POST /ha/verify

Verify the cloud auth token and retrieve sync configuration. Called by the addon heartbeat loop every 5 minutes.

### Request

```
POST /ha/verify
Authorization: Bearer <token>
```

No request body.

### Responses

| Status | Meaning | Body |
|--------|---------|------|
| 200 | Token valid, subscription active | `{ "status": "ok", "sync_interval_minutes": 5, "batch_size": 100 }` |
| 401 | Missing or invalid token | `{ "error": "Unauthorized" }` |
| 403 | Subscription suspended/expired/inactive | `{ "error": "Subscription suspended" }` |
| 404 | Token not found in database | `{ "error": "Subscription not found" }` |
| 5xx | Server error (addon will retry) | — |

---

## GET /ha/data

Fetch the sync checkpoint (last processed timestamp).

### Request

```
GET /ha/data
Authorization: Bearer <token>
```

### Responses

| Status | Meaning | Body |
|--------|---------|------|
| 200 | Success | `{ "last_timestamp": 1705320000.0 }` |
| 401 | Unauthorized | `{ "error": "Unauthorized" }` |

---

## POST /ha/data

Ingest a batch of event/state records and advance the checkpoint.

### Request

```
POST /ha/data
Authorization: Bearer <token>
Content-Type: application/json

{
  "records": [ ... ],
  "source": "home_assistant",
  "sent_at": "2024-01-15T12:00:00Z"
}
```

### Responses

| Status | Meaning | Body |
|--------|---------|------|
| 201 | Records accepted | `{ "records_received": 2 }` |
| 400 | No records | `{ "error": "No records" }` |
| 401 | Unauthorized | `{ "error": "Unauthorized" }` |

---

## GET /ha/config

Fetch remote configuration (entity filters, feature flags, model version, etc.).

### Request

```
GET /ha/config
Authorization: Bearer <token>
```

### Responses

| Status | Meaning | Body |
|--------|---------|------|
| 200 | Success | `{ "entity_filters": {...}, "feature_flags": {...}, ... }` |
| 401 | Unauthorized | `{ "error": "Unauthorized" }` |
