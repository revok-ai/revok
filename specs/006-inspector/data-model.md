# Data Model — Inspector (006)

**Branch**: feat/inspector
**Date**: 2026-06-23

---

## New Protocols (revok/interfaces.py)

### GraphReader

Read-only graph introspection protocol. Separate from `GraphBackend` (write/propagate).

```
GraphReader
├── has_node(entity_id: str) -> bool
├── successors(entity_id: str) -> list[str]
├── predecessors(entity_id: str) -> list[str]
├── edge_weight(source_id: str, target_id: str) -> float
└── node_score(entity_id: str) -> float
```

**Who implements it**: `CausalGraph` (NetworkX) — the only concrete `GraphReader` in this feature
**Who depends on it**: `RevokInspector` only — signal processor does NOT use `GraphReader`

---

### SignalHistoryStore

Write and read access to the signal event log.

```
SignalHistoryStore
├── record(event: SignalRecord) -> Awaitable[None]
├── get_for_entity(entity_key: str) -> Awaitable[list[SignalRecord]]
└── close() -> Awaitable[None]
```

**Who implements it**: `SqliteSignalHistoryStore`
**Who depends on it**: `SignalProcessor` (write), `RevokInspector` (read)

---

### Inspector

Read-only explainability query service.

```
Inspector
├── inspect_entity(entity_key: str) -> Awaitable[InspectionReport | None]
├── get_downstream(entity_key: str, *, max_hops, min_pressure, attenuation) -> Awaitable[list[DownstreamEntity]]
├── get_paths(entity_key: str, *, max_hops, min_pressure, attenuation) -> Awaitable[list[PropagationPath]]
└── get_signals(entity_key: str) -> Awaitable[list[SignalRecord] | None]
```

`get_signals` returns `None` when signal history is disabled (history store is not configured), vs. an empty list when enabled but no records exist.

**Who implements it**: `RevokInspector`
**Who depends on it**: aiohttp route handlers in `revok/proxy.py`

---

## New Dataclasses (revok/models.py)

### CausalNeighbor (frozen)

One directed edge in the inspection response.

| Field | Type | Description |
|-------|------|-------------|
| `entity_key` | `str` | Neighbor entity identifier |
| `direction` | `Literal["upstream", "downstream"]` | Edge direction relative to inspected entity |
| `weight` | `float` | Edge propagation weight `(0, 1]` |
| `score` | `float \| None` | Neighbor's current score from StateStore; None if not in store |

---

### InspectionReport (frozen)

Point-in-time view of one entity.

| Field | Type | Description |
|-------|------|-------------|
| `entity_key` | `str` | Normalized entity identifier |
| `score` | `float` | Current confidence score |
| `valid_time` | `float` | Unix epoch — when the event occurred |
| `transaction_time` | `float` | Unix epoch — when Revok recorded it |
| `signal_count` | `int` | Total signals processed for this entity |
| `contradiction_count` | `int` | Total contradiction events detected |
| `upstream` | `list[CausalNeighbor]` | Direct upstream sources (predecessors in causal graph) |
| `downstream` | `list[CausalNeighbor]` | Direct downstream targets (successors in causal graph) |
| `inspected_at` | `float` | Unix epoch timestamp when this inspection snapshot was generated |

---

### DownstreamEntity (frozen)

One entity in the blast-radius result set.

| Field | Type | Description |
|-------|------|-------------|
| `entity_key` | `str` | Normalized entity identifier |
| `pressure` | `float` | Maximum propagated pressure reaching this entity |
| `hops` | `int` | Minimum number of hops from root |

---

### PropagationPath (frozen)

An ordered entity chain from a root to a target.

| Field | Type | Description |
|-------|------|-------------|
| `hops` | `list[str]` | Ordered entity keys from root (index 0) to target (last) |
| `pressures` | `list[float]` | Per-hop accumulated pressure (same length as hops) |
| `is_dominant` | `bool` | True if this path delivered the highest pressure to the target |

---

### SignalRecord (frozen)

Persisted log entry for one signal processing event.

| Field | Type | Description |
|-------|------|-------------|
| `id` | `int \| None` | SQLite rowid; None before persistence |
| `entity_key` | `str` | Entity that received the update |
| `source_id` | `str` | Signal source identifier (from `Signal.source_id`) |
| `processed_at` | `float` | Unix epoch when SignalProcessor wrote the update |
| `score_before` | `float \| None` | Score before update (None if entity was new) |
| `score_after` | `float` | Score after update |
| `is_propagated` | `bool` | False = direct signal; True = causal propagation |
| `upstream_source` | `str \| None` | Root entity key if `is_propagated`; None otherwise |

---

## New Config (revok/config.py)

### InspectorConfig (frozen)

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | `bool` | `True` | Gate all inspector routes |
| `signal_history_enabled` | `bool` | `True` | Persist signal events; enable `/signals` endpoint |
| `signal_history_max_rows` | `int` | `10_000` | Max rows per entity in `signal_events` table |
| `max_paths` | `int` | `100` | Max propagation paths returned by `get_paths()`; discovery stops when reached |

---

## New SQLite Table

### signal_events

```sql
CREATE TABLE IF NOT EXISTS signal_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_key       TEXT    NOT NULL,
    source_id        TEXT    NOT NULL,
    processed_at     REAL    NOT NULL,
    score_before     REAL,
    score_after      REAL    NOT NULL,
    is_propagated    INTEGER NOT NULL DEFAULT 0,   -- 0=false, 1=true
    upstream_source  TEXT                          -- NULL for direct signals
);

CREATE INDEX IF NOT EXISTS idx_signal_events_entity_key
    ON signal_events (entity_key, processed_at DESC);
```

Stored in the same SQLite file as `entity_records` (same path from `StateStoreConfig.sqlite_path`).

---

## Entity Relationships

```
Signal  ──── (processed by) ──── SignalProcessor
                 │
                 ├── writes ──── EntityRecord ──── (stored in) ──── SqliteStateStore
                 │
                 └── records ── SignalRecord ──── (stored in) ──── SqliteSignalHistoryStore

CausalGraph ──── implements ──── GraphBackend (write/propagate)
             ──── implements ──── GraphReader  (read-only introspection)

RevokInspector ──── depends on ──── GraphReader (read)
                ──── depends on ──── StateStore  (read)
                ──── depends on ──── SignalHistoryStore (read)
```

---

## State Transitions — InspectionReport

The `InspectionReport` is **stateless** (computed on demand, not persisted). It reflects the point-in-time values from `StateStore.get()` and `GraphReader.*()` at query execution time.

## State Transitions — SignalRecord

```
Signal arrives at SignalProcessor
  ↓
_apply_root() / _apply_propagation() runs
  ↓
SignalHistoryStore.record() called with:
  - score_before = existing EntityRecord.score (or None)
  - score_after  = new_score after ScoringEngine
  - is_propagated = False for _apply_root, True for _apply_propagation
  ↓
SignalRecord persisted to signal_events table
```
