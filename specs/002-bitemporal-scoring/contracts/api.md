# API Contracts: Bitemporal Scoring (Feature 002)

## Changed Endpoint

### GET /v1/entities/{entity_key}

#### Before (v0.1.x response body)

```json
{
  "entity_key": "user:alice",
  "score": 0.82,
  "last_seen": 1717612800.0,
  "signal_count": 7,
  "pattern_name": "header"
}
```

#### After (v0.2.0 response body)

```json
{
  "entity_key": "user:alice",
  "score": 0.82,
  "valid_time": 1717612800.0,
  "transaction_time": 1717612801.3,
  "signal_count": 7,
  "pattern_name": "header"
}
```

**Change summary**:
- `last_seen` field **removed** from response (it was the stored field; now it's a property, excluded from `dataclasses.asdict()`)
- `valid_time` field **added** — event time (Unix epoch seconds)
- `transaction_time` field **added** — write time (Unix epoch seconds)
- All other fields unchanged

**Backward compatibility note**: Clients reading `last_seen` from this response must update to `valid_time`. The values will be equal for all records created by v0.2.0 live-signal writes (where no explicit `valid_time` override is provided).

#### Status codes (unchanged)

| Status | Condition |
|--------|-----------|
| 200    | Entity found; body as above |
| 404    | Entity not found; body: `{"error": "not_found"}` |

---

## Unchanged Endpoints

| Endpoint | Change |
|----------|--------|
| `POST /v1/upstream` (proxy write) | No change — `valid_time` transport deferred |
| `DELETE /v1/entities/{entity_key}` | No change |
| `GET /health` | No change |

---

## Implementation Notes

No changes to `revok/proxy.py` are required. The response uses `dataclasses.asdict(record)` which automatically includes all `__dataclass_fields__`. Since `last_seen` is now a `@property` (not a field), it is excluded; `valid_time` and `transaction_time` are fields, so they are included.
