# Contract — Signal Processing & Causal Graph Config (005)

## 1) External Signal API (existing, behavior clarified)

### Endpoint
- `POST /signals`

### Request Body
```json
{
  "entity_refs": ["entity-a"],
  "severity": "high",
  "source": "webhook",
  "payload": {}
}
```

### Response
- `202 Accepted`
```json
{
  "accepted": true
}
```

### Contract Semantics
- API remains asynchronous and non-blocking.
- Acceptance does not imply scoring has completed yet.
- Signal is processed by background consumer after enqueue.

## 2) Causal Graph Configuration Contract

### YAML shape
```yaml
causal_graph:
  relationships:
    - from: entity-a
      to: entity-b
      weight: 0.9
  propagation:
    min_pressure: 0.05
    max_hops: 3
    attenuation: 0.5
```

### Validation constraints
- `relationships` is a list of mappings.
- Each relationship requires:
  - `from` (non-empty string)
  - `to` (non-empty string)
  - `weight` (`0 < weight <= 1`)
- `propagation.min_pressure` in `[0, 1]`
- `propagation.max_hops` integer `>= 0`
- `propagation.attenuation` in `(0, 1]`

## 3) Propagation Contract

### Input
- Root entity key
- Initial pressure
- Propagation policy (`min_pressure`, `max_hops`, `attenuation`)

### Output
- Mapping `{entity_key: propagated_pressure}` for downstream entities only.

### Guarantees
- Traversal is cycle-safe.
- Traversal is bounded by `max_hops` and `min_pressure`.
- If an entity is reachable via multiple paths, retained pressure is the maximum path pressure.
- Pressures are never summed across paths.

## 4) Severity-to-pressure Contract

- `severity` is mapped via `scoring.severity_weights` keys: `low`, `medium`, `high`, `critical`.
- `default_severity` is used when `severity` is missing or unrecognized.
- Resolved pressure is applied to root scoring and used as initial propagation pressure.

### Validation constraints
- Each weight in `(0, score_cap]`.
- `default_severity` must be one configured key.
