# API Contract — Inspector (006)

**Branch**: feat/inspector
**Date**: 2026-06-23

---

## Base URL

All Inspector routes are mounted under `/v1/inspector/`.

---

## Endpoints

### GET /v1/inspector/entities/{entity_key}

Returns entity state and direct causal neighbors.

**Path Parameters**
| Name | Description |
|------|-------------|
| `entity_key` | URL-encoded normalized entity identifier |

**Response 200**
```json
{
  "entity_key": "alice",
  "score": 0.42,
  "valid_time": 1750000000.123,
  "transaction_time": 1750000001.456,
  "signal_count": 5,
  "contradiction_count": 1,  "inspected_at": 1750000002.789,  "upstream": [
    { "entity_key": "root_cause", "direction": "upstream", "weight": 1.0, "score": 0.88 }
  ],
  "downstream": [
    { "entity_key": "bob", "direction": "downstream", "weight": 0.8, "score": 0.31 }
  ]
}
```

**Response 404**
```json
{ "error": "not_found", "entity_key": "alice" }
```

**Response 503** (when `inspector.enabled = false`)
```json
{ "error": "inspector_disabled" }
```

---

### GET /v1/inspector/entities/{entity_key}/downstream

Returns all entities reachable from the given entity within propagation bounds. Uses `CausalGraphConfig` bounds (`max_hops`, `min_pressure`, `attenuation`) unless overridden by query parameters.

**Path Parameters**
| Name | Description |
|------|-------------|
| `entity_key` | URL-encoded normalized entity identifier |

**Query Parameters** (all optional — default to values from `causal_graph` config)
| Name | Type | Description |
|------|------|-------------|
| `max_hops` | int | Override propagation hop limit |
| `min_pressure` | float | Override minimum pressure threshold |
| `attenuation` | float | Override per-hop attenuation factor |

**Response 200**
```json
{
  "root": "alice",
  "downstream": [
    { "entity_key": "bob",   "pressure": 0.40, "hops": 1 },
    { "entity_key": "carol", "pressure": 0.20, "hops": 2 }
  ]
}
```

**Response 404** — entity_key not in state store  
**Response 503** — inspector disabled

---

### GET /v1/inspector/entities/{entity_key}/paths

Returns all propagation paths from upstream root entities to the given entity.

**Path Parameters**
| Name | Description |
|------|-------------|
| `entity_key` | URL-encoded normalized entity identifier |

**Query Parameters** (all optional — default to values from `causal_graph` config)
| Name | Type | Description |
|------|------|-------------|
| `max_hops` | int | Override max backward search depth |
| `min_pressure` | float | Override minimum pressure threshold |
| `attenuation` | float | Override per-hop attenuation |

**Response 200**
```json
{
  "target": "carol",
  "paths": [
    {
      "hops": ["alice", "bob", "carol"],
      "pressures": [0.80, 0.40, 0.20],
      "is_dominant": true
    },
    {
      "hops": ["alice", "dave", "carol"],
      "pressures": [0.80, 0.32, 0.16],
      "is_dominant": false
    }
  ]
}
```

**Response 404** — entity_key not in state store  
**Response 503** — inspector disabled

---

### GET /v1/inspector/entities/{entity_key}/signals

Returns the signal event log for the given entity.

**Path Parameters**
| Name | Description |
|------|-------------|
| `entity_key` | URL-encoded normalized entity identifier |

**Response 200**
```json
{
  "entity_key": "bob",
  "signals": [
    {
      "id": 42,
      "source_id": "agent-session-7",
      "processed_at": 1750000001.456,
      "score_before": 0.85,
      "score_after": 0.42,
      "is_propagated": true,
      "upstream_source": "alice"
    }
  ]
}
```

**Response 404** — entity_key not in state store  
**Response 503** — inspector disabled  
**Response 501** — signal history disabled (`inspector.signal_history_enabled = false`)

```json
{ "error": "signal_history_disabled" }
```

---

## Config Contract

### YAML schema (additions to `revok.example.yaml`)

```yaml
inspector:
  enabled: true
  signal_history_enabled: true
  signal_history_max_rows: 10000
  max_paths: 100
```

All fields are optional. Defaults shown above apply when the section is absent.

### InspectorConfig invariants

- `signal_history_max_rows` MUST be > 0
- `max_paths` MUST be > 0
- `signal_history_enabled: false` suppresses recording AND disables the `/signals` endpoint
- `enabled: false` returns 503 on all four Inspector endpoints

---

## GraphReader Protocol Contract

Implementations MUST satisfy:

| Method | Invariant |
|--------|-----------|
| `has_node(x)` | Returns `False` for any key not added via `add_entity` |
| `successors(x)` | Returns `[]` for unknown node (not raises) |
| `predecessors(x)` | Returns `[]` for unknown node (not raises) |
| `edge_weight(a, b)` | Raises `KeyError` if edge does not exist |
| `node_score(x)` | Returns the last score set by `add_entity(x, score)` |

These invariants form the basis of `tests/contract/test_graph_reader_contract.py`.
