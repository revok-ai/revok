# Data Model — Signal-Driven Causal Propagation (005)

## Core Entities

### 1) SignalEvent
Represents one externally submitted signal consumed from `AsyncioQueueBus`.

Fields:
- `raw_content: str`
- `source_id: str`
- `timestamp: float`
- `http_method: str`
- `http_path: str`
- `original_body: bytes`
- `headers: dict[str, str]`
- `valid_time: float | None`

Usage:
- Input to async signal processor.
- Root entity resolution source.

### 2) EntityRecord
Persistent confidence state for each tracked entity.

Fields (existing):
- `entity_key: str`
- `score: float`
- `valid_time: float`
- `transaction_time: float`
- `signal_count: int`
- `pattern_name: str`
- `contradiction_count: int`
- `last_contradiction_time: float | None`
- `last_value_fingerprint: str | None`

State transitions:
- Root signal application: score decreases via pressure-aware scoring, `signal_count += 1`, temporal fields updated.
- Propagated signal application: same transition model per downstream entity with propagated pressure.

### 3) CausalRelationship
Directed weighted edge in causal graph.

Fields:
- `from: str`
- `to: str`
- `weight: float` (`0 < weight <= 1`)

Usage:
- Construct graph at startup.
- Used to propagate pressure from upstream to downstream entities.

### 4) PropagationPolicy
Controls traversal and pressure attenuation.

Fields:
- `min_pressure: float` (`0 <= value <= 1`)
- `max_hops: int` (`>= 0`)
- `attenuation: float` (`0 < value <= 1`)

Usage:
- Passed into `CausalGraph.propagate()` for deterministic bounded traversal.

### 5) PropagationResult
Output map from propagation.

Fields:
- `pressures: dict[str, float]` where key is downstream entity and value is max propagated pressure.

Semantics:
- Root entity excluded from downstream result map.
- For multi-path reachability, map value is max pressure among candidate paths.

## Validation Rules

- Relationship endpoints must be non-empty normalized entity keys.
- Relationship weight must be in `(0, 1]`.
- `attenuation` must be in `(0, 1]`.
- `min_pressure` must be in `[0, 1]`.
- `max_hops` must be non-negative integer.

## Invariants

- Propagation terminates due to bounded hops and cutoff threshold.
- Cycles do not cause infinite processing.
- Downstream entity pressure is never summed across paths.
- Existing write-enrichment model remains unchanged.
