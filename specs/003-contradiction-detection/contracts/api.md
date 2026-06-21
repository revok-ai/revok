# API Contracts — Contradiction Detection (003)
**Feature**: 003-contradiction-detection
**Date**: 2026-06-09

---

## GET /v1/entities/{entity_key}

**Change type**: Additive — two new fields in response body.

### Request
```
GET /v1/entities/{entity_key}
```
No new query parameters. No request body.

### Response — 200 OK (entity found)
```json
{
  "entity_key": "orion_cache",
  "score": 0.43,
  "signal_count": 6,
  "valid_time": 1749480000.0,
  "transaction_time": 1749480000.1,
  "pattern_name": "product",
  "contradiction_count": 2,
  "last_contradiction_time": 1749479800.0
}
```

**New fields**:

| Field | Type | Description |
|-------|------|-------------|
| `contradiction_count` | `integer ≥ 0` | Number of contradiction events detected over the entity's lifetime |
| `last_contradiction_time` | `number (epoch) \| null` | Unix epoch of the most recent contradiction, or `null` if never contradicted |

**Excluded fields** (internal, not serialized):
- `last_value_fingerprint` — stripped from response in `proxy._handle_get_entity`

### Response — 404 Not Found (entity absent)
```json
{"error": "not_found"}
```
Unchanged from current behavior.

---

## No New Endpoints

This feature adds no new HTTP routes. The existing routing table is unchanged:

| Method | Path | Handler |
|--------|------|---------|
| `GET`  | `/v1/entities/{entity_key}` | `_handle_get_entity` (modified) |
| `DELETE` | `/v1/entities/{entity_key}` | `_handle_delete_entity` (unchanged) |
| `GET`  | `/v1/entities` | `_handle_list_entities` (unchanged) |
| `POST` | `/v1/signals` | `_handle_post_signal` (unchanged) |
| `POST` | `/v1/memories`, etc. | proxy passthrough (unchanged) |

---

## YAML Configuration Contract

`revok.yaml` gains two new **optional** keys under `scoring:`:

```yaml
scoring:
  half_life_seconds: 3600
  signal_strength: 0.2
  score_cap: 1.0
  # New optional fields (defaults shown):
  contradiction_window_seconds: 300.0   # 5 minutes
  contradiction_penalty: 0.15           # added on top of signal_strength
```

- Omitting both keys is valid; defaults are applied.
- Both keys accept any positive float.
- Setting `contradiction_window_seconds: 0` effectively disables the window (no signal can
  have gap < 0), so contradictions are never triggered.
