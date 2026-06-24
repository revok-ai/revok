# Implementation Plan — Inspector (006)

**Feature**: 006-inspector
**Branch**: feat/inspector
**Date**: 2026-06-23
**Status**: Planning complete
**Spec**: [spec.md](spec.md)

---

## Summary

The Inspector adds a read-only explainability API to Revok's causal pipeline. A new `GraphReader` Protocol (separate from the existing `GraphBackend`) provides structural graph introspection without conflating write and read responsibilities. A new `Inspector` Protocol (defined first, per Constitution § III) is implemented by `RevokInspector`, which composes `GraphReader` + `StateStore` + `SignalHistoryStore`. Four new aiohttp routes expose entity state, blast-radius, propagation paths, and signal history. A new `SqliteSignalHistoryStore` (implementing the `SignalHistoryStore` Protocol) persists signal events written by the `SignalProcessor`.

---

## Technical Context

- **Python**: 3.11+, `from __future__ import annotations` throughout
- **Async runtime**: `asyncio` + `aiohttp`
- **Graph**: `CausalGraph` backed by `networkx.DiGraph`; Inspector is `GraphReader`-agnostic by design
- **Persistence**: `SqliteStateStore` (WAL + LRU); new `SqliteSignalHistoryStore` (same DB file)
- **Tests**: `pytest` + `pytest-asyncio`; `mypy` + `ruff` quality gates
- **New dependencies**: None — all required packages already present in `pyproject.toml`
- **Constraints**:
  - Zero changes to write-enrichment path (`metadata_writer.py`)
  - Zero latency impact on `POST /signals` and `GET /v1/entities/{key}`
  - All new code: type hints on all functions, docstrings on all public methods
  - Every new module has a corresponding test module

---

## Constitution Check

| Gate | Status | Notes |
|------|--------|-------|
| Interface First: `Inspector` Protocol defined before implementation | PASS | T002 defines protocol; T006 implements |
| Interface First: `GraphReader` Protocol defined before implementation | PASS | T002 defines protocol; T004 implements |
| Interface First: `SignalHistoryStore` Protocol defined before implementation | PASS | T002 defines protocol; T011 implements |
| OSS boundary: no azure/boto3/redis/enterprise imports | PASS | core uses stdlib + existing deps only |
| OSS boundary: Inspector depends only on `GraphReader`, no concrete graph backend | PASS | Inspector depends only on `GraphReader`; no concrete backend code in this feature |
| Async first: StateStore access is async | PASS | Inspector methods are async |
| No hardcoded values | PASS | all bounds from `CausalGraphConfig` + `InspectorConfig` |
| Existing write-enrichment path unchanged | PASS | `metadata_writer.py` not touched |
| Existing tests must not regress | PASS | all new code additive |

---

## Project Structure

### Documentation (this feature)

```text
specs/006-inspector/
├── plan.md              ← this file
├── research.md          ← research decisions
├── data-model.md        ← entity and protocol definitions
├── contracts/
│   └── inspector-api.md ← HTTP + config + GraphReader contracts
├── quickstart.md        ← verification workflow
└── checklists/
    └── requirements.md
```

### Source Code Changes

```text
revok/
├── interfaces.py         ← +GraphReader, +Inspector, +SignalHistoryStore protocols
├── models.py             ← +CausalNeighbor, +InspectionReport, +DownstreamEntity,
│                            +PropagationPath, +SignalRecord
├── config.py             ← +InspectorConfig
├── inspector.py          ← NEW: RevokInspector implementation
├── signal_history.py     ← NEW: SqliteSignalHistoryStore implementation
├── signal_processor.py   ← inject SignalHistoryStore; record events
└── proxy.py              ← wire inspector routes + InspectorConfig

tests/
├── contract/
│   └── test_graph_reader_contract.py  ← NEW: shared GraphReader contract (NetworkX)
├── test_inspector.py     ← NEW
├── test_signal_history.py ← NEW
├── test_signal_processor.py ← extend: signal history recording
├── test_proxy.py         ← extend: inspector routes
└── test_causal_graph.py  ← extend: GraphReader protocol compliance
```

---

## Phase 0 — Research

Completed. See [research.md](research.md). All 8 clarifications resolved:

| # | Topic | Decision |
|---|-------|----------|
| 1 | GraphBackend extension vs. new GraphReader | **New `GraphReader` Protocol** |
| 2 | Signal history persistence | **New `SqliteSignalHistoryStore`** |
| 3 | Graph backend license | **NetworkX (BSD-3) — no new dependencies introduced** |
| 4 | GraphReader sync vs. async | **Sync** (in-memory, consistent with GraphBackend) |
| 5 | Path-finding algorithm | **Backward BFS via predecessors()** |
| 6 | API namespace | **/v1/inspector/entities/{key}/...** |
| 7 | Inspector config placement | **New `InspectorConfig`; reuse `CausalGraphConfig` bounds** |
| 8 | Contract test structure | **Shared mixin in `tests/contract/`** |

---

## Phase 1 — Design & Contracts

### T001 — Add new dataclasses to models.py
**Files**: `revok/models.py`

Add five frozen dataclasses (models must exist before protocols reference them):
- `CausalNeighbor(entity_key, direction, weight, score)` — `direction: Literal["upstream", "downstream"]`
- `InspectionReport(entity_key, score, valid_time, transaction_time, signal_count, contradiction_count, upstream: list[CausalNeighbor], downstream: list[CausalNeighbor], inspected_at: float)` — `inspected_at` is the Unix epoch timestamp when the inspection snapshot was generated
- `DownstreamEntity(entity_key, pressure, hops)`
- `PropagationPath(hops: list[str], pressures: list[float], is_dominant: bool)`
- `SignalRecord(id: int | None, entity_key, source_id, processed_at, score_before: float | None, score_after, is_propagated: bool, upstream_source: str | None)`

---

### T002 — Define new Protocols in interfaces.py
**Files**: `revok/interfaces.py`

Add three Protocols in order (Interface First — all before any implementation):

1. **`GraphReader`** (`@runtime_checkable`):
   - `has_node(entity_id: str) -> bool`
   - `successors(entity_id: str) -> list[str]` — returns `[]` for unknown node
   - `predecessors(entity_id: str) -> list[str]` — returns `[]` for unknown node
   - `edge_weight(source_id: str, target_id: str) -> float` — raises `KeyError` if edge absent
   - `node_score(entity_id: str) -> float` — returns last score set via `add_entity`

2. **`SignalHistoryStore`** (`@runtime_checkable`):
   - `async def record(event: SignalRecord) -> None`
   - `async def get_for_entity(entity_key: str) -> list[SignalRecord]`
   - `async def close() -> None`

3. **`Inspector`** (`@runtime_checkable`):
   - `async def inspect_entity(entity_key: str) -> InspectionReport | None`
   - `async def get_downstream(entity_key: str, *, max_hops: int, min_pressure: float, attenuation: float) -> list[DownstreamEntity]`
   - `async def get_paths(entity_key: str, *, max_hops: int, min_pressure: float, attenuation: float, max_paths: int) -> list[PropagationPath]`
   - `async def get_signals(entity_key: str) -> list[SignalRecord] | None`

---

### T003 — Add InspectorConfig to config.py
**Files**: `revok/config.py`, `config/revok.example.yaml`, `config/revok-zep.example.yaml`, `tests/test_config.py`

```python
@dataclass(frozen=True)
class InspectorConfig:
    enabled: bool = True
    signal_history_enabled: bool = True
    signal_history_max_rows: int = 10_000
    max_paths: int = 100
```

- Add `inspector: InspectorConfig` to main config dataclass
- Add YAML section to both example configs with defaults
- Validate `signal_history_max_rows > 0` and `max_paths > 0`
- Add to `load_config()`
- Add test cases for defaults and validation

---

### T004 — Extend CausalGraph to implement GraphReader
**Files**: `revok/causal_graph.py`, `tests/test_causal_graph.py`

Extend class declaration: `class CausalGraph(GraphBackend, GraphReader):`

Implement five methods delegating to `self._graph`:
```python
def has_node(self, entity_id: str) -> bool: ...
def successors(self, entity_id: str) -> list[str]: ...      # [] for unknown
def predecessors(self, entity_id: str) -> list[str]: ...    # [] for unknown
def edge_weight(self, source_id: str, target_id: str) -> float: ...  # KeyError if absent
def node_score(self, entity_id: str) -> float: ...
```

Extend `tests/test_causal_graph.py` with GraphReader-specific tests (also covered by T015 contract).

---

### T005 — Agent context update
**Files**: `.github/copilot-instructions.md`

Update plan reference between `<!-- SPECKIT START -->` and `<!-- SPECKIT END -->` markers to point to `specs/006-inspector/plan.md`.

---

## Phase 2 — Entity Inspection API

### T006 — RevokInspector: inspect_entity
**Files**: `revok/inspector.py` (NEW), `tests/test_inspector.py` (NEW)

```python
class RevokInspector:
    def __init__(
        self,
        store: StateStore,
        graph: GraphReader,
        history: SignalHistoryStore | None,
    ) -> None: ...

    async def inspect_entity(self, entity_key: str) -> InspectionReport | None:
        """Return InspectionReport or None if entity not in store.

        Gathers EntityRecord from store, then direct neighbors from graph.
        If graph is unavailable (None) or entity has no graph node, upstream/downstream = [].
        Score for each neighbor fetched from store (None if not in store).
        """
```

**Implementation notes**:
- `store.get(entity_key)` → None means 404
- `graph.successors(entity_key)` → build `CausalNeighbor` list for downstream
- `graph.predecessors(entity_key)` → build `CausalNeighbor` list for upstream
- `edge_weight` via `graph.edge_weight(neighbor, entity_key)` / `graph.edge_weight(entity_key, neighbor)`
- Neighbor score: `await store.get(neighbor_key)` → `.score` or `None`
- Must not raise if graph has no node for entity_key (return empty lists)

**Test cases**:
- Entity in store + graph with neighbors → full report
- Entity in store, no graph node → empty upstream/downstream
- Entity not in store → None
- Neighbor in graph but not store → `score: null` in report

---

### T007 — Wire GET /v1/inspector/entities/{entity_key}
**Files**: `revok/proxy.py`, `tests/test_proxy.py`

- Instantiate `RevokInspector` in `build_app()`, inject into route handlers
- Add route: `GET /v1/inspector/entities/{entity_key}`
- Handler:
  - If `inspector.enabled = false` → 503 `{"error": "inspector_disabled"}`
  - Call `inspector.inspect_entity(entity_key)` → None → 404
  - Serialize `InspectionReport` to JSON dict
- Test: 200 with valid entity, 404 with missing key, 503 when disabled

---

## Phase 3 — Propagation Path Queries

### T008 — RevokInspector: get_downstream
**Files**: `revok/inspector.py`, `tests/test_inspector.py`

```python
async def get_downstream(
    self,
    entity_key: str,
    *,
    max_hops: int,
    min_pressure: float,
    attenuation: float,
) -> list[DownstreamEntity]:
    """BFS forward from entity_key using GraphReader.successors().

    Uses same max-pressure, attenuation, and min_pressure semantics as SignalProcessor.
    Returns DownstreamEntity per reachable node. Excludes entity_key itself.
    Returns [] if entity has no graph node.
    """
```

**Algorithm**:
```
frontier = {entity_key: (pressure=1.0, hops=0)}
visited = {entity_key}
result = {}

for each hop up to max_hops:
    next_frontier = {}
    for source, (src_pressure, src_hops) in frontier:
        for target in graph.successors(source):
            if target in visited: continue
            w = graph.edge_weight(source, target)
            p = src_pressure * w * attenuation
            if p < min_pressure: continue
            if target not in result or p > result[target].pressure:
                result[target] = DownstreamEntity(target, pressure=p, hops=src_hops+1)
            next_frontier[target] = max(next_frontier.get(target, 0), p)
    if not next_frontier: break
    frontier = next_frontier
    visited.update(frontier)

return sorted(result.values(), key=lambda e: -e.pressure)
```

**Test cases**:
- Linear chain A→B→C: downstream of A returns B, C with correct pressures
- Diamond graph: D appears once with max pressure
- Cycle graph: no infinite loop, each node at most once
- `max_hops=1`: only direct neighbors returned
- Pressure below threshold: entity excluded

---

### T009 — RevokInspector: get_paths
**Files**: `revok/inspector.py`, `tests/test_inspector.py`

```python
async def get_paths(
    self,
    entity_key: str,
    *,
    max_hops: int,
    min_pressure: float,
    attenuation: float,
    max_paths: int,
) -> list[PropagationPath]:
    """Backward BFS from target; compute pressures forward for each discovered path.

    Root = node with no predecessors in the backward-searched subgraph.
    Returns all paths from any root to entity_key, capped at max_paths.
    Each path is a list of entity keys from root (index 0) to target (last).
    is_dominant = True for the path with highest terminal pressure.

    When max_paths is reached, discovery stops immediately and the returned
    list contains only the paths found up to that point. is_dominant is still
    computed on the returned subset.
    """
```

**Algorithm** (backward BFS):
```
# Phase 1: discover ancestor paths via backward BFS, capped at max_paths
paths = []
Find all simple paths in reverse direction from entity_key to root nodes
  - root = node where graph.predecessors(node) == []
  - cap depth at max_hops
  - no node revisited in a single path (cycle safety)
  - if len(paths) >= max_paths: stop immediately

# Phase 2: compute pressures forward along each discovered path
For path [r, n1, n2, ..., target]:
  p = 1.0 (starting pressure at root)
  pressures = [1.0]
  for hop in path[1:]:
    w = graph.edge_weight(prev, hop)
    p = p * w * attenuation
    pressures.append(p)

# Phase 3: mark dominant (on returned subset only)
if paths:
    max_terminal = max(path.pressures[-1] for path in paths)
    for path in paths:
        path.is_dominant = (path.pressures[-1] == max_terminal)
```

**max_paths behavior**:
- Discovery stops as soon as `max_paths` paths are collected; results are not exhaustive for highly connected graphs.
- Callers detect possible truncation by checking `len(response["paths"]) == max_paths`.
- `max_paths` is sourced from `InspectorConfig.max_paths` (default 100) and passed through by route handlers.
- `max_paths` is validated `> 0` in `InspectorConfig`.

**Test cases**:
- Single path A→B→C, query C: one path returned, is_dominant=True
- Diamond A→B→D, A→C→D, query D: two paths, higher-pressure one is_dominant=True
- No upstream sources for entity: empty list
- Cycle graph: cycle does not appear in any path
- `max_paths=1` on a diamond graph: exactly 1 path returned (not 2)
- `max_paths=2` on a graph with 5 paths: exactly 2 paths returned

---

### T010 — Wire GET /v1/inspector/entities/{entity_key}/downstream and /paths
**Files**: `revok/proxy.py`, `tests/test_proxy.py`

- Route: `GET /v1/inspector/entities/{entity_key}/downstream`
  - Parse optional query params (`max_hops`, `min_pressure`, `attenuation`)
  - Fall back to `config.causal_graph.*` for missing params
  - Call `inspector.get_downstream(...)`
  - Serialize to `{"root": entity_key, "downstream": [...]}`
- Route: `GET /v1/inspector/entities/{entity_key}/paths`
  - Same param handling for `max_hops`, `min_pressure`, `attenuation`
  - `max_paths` is always sourced from `config.inspector.max_paths` (not user-overridable via query param)
  - Call `inspector.get_paths(..., max_paths=config.inspector.max_paths)`
  - Serialize to `{"target": entity_key, "paths": [...]}`
- Both return 404 for unknown entity, 503 when disabled

---

## Phase 4 — Signal History Persistence

### T011 — SqliteSignalHistoryStore
**Files**: `revok/signal_history.py` (NEW), `tests/test_signal_history.py` (NEW)

```python
class SqliteSignalHistoryStore:
    """Implements SignalHistoryStore using a signal_events table in the SQLite DB."""

    DDL = """
    CREATE TABLE IF NOT EXISTS signal_events (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        entity_key      TEXT    NOT NULL,
        source_id       TEXT    NOT NULL,
        processed_at    REAL    NOT NULL,
        score_before    REAL,
        score_after     REAL    NOT NULL,
        is_propagated   INTEGER NOT NULL DEFAULT 0,
        upstream_source TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_signal_events_entity_key
        ON signal_events (entity_key, processed_at DESC);
    """

    async def open(self) -> None: ...        # create table + index
    async def record(self, event: SignalRecord) -> None: ...   # INSERT
    async def get_for_entity(self, entity_key: str) -> list[SignalRecord]: ...
    async def trim_for_entity(self, entity_key: str, max_rows: int) -> None: ...
    async def close(self) -> None: ...
```

`record()` calls `trim_for_entity()` after insert to enforce `signal_history_max_rows`.

**Test cases**:
- record + get_for_entity round-trip
- trim enforces max_rows (oldest rows deleted)
- is_propagated persisted as 0/1
- close() is idempotent

---

### T012 — Inject SignalHistoryStore into SignalProcessor
**Files**: `revok/signal_processor.py`, `tests/test_signal_processor.py`

Add `history: SignalHistoryStore | None` parameter to `SignalProcessor.__init__()`.

**Latency constraint**: Signal history persistence MUST NOT materially increase latency of `POST /signals` processing. The existing write-path behavior (root entity update → propagation) must be preserved. The implementation avoids unnecessary extra store reads — `_apply_root()` returns `(score_before, score_after)` so the caller has both values without a second query.

Future consideration: the `SignalHistoryStore.record()` call is `await`-ed inline for correctness. If profiling shows material latency impact, this integration point is designed to support async/event-driven persistence (e.g., publishing to a queue) as a drop-in replacement behind the `SignalHistoryStore` Protocol.

In `_apply_root()`:
```python
if self._history is not None:
    await self._history.record(SignalRecord(
        id=None,
        entity_key=entity_key,
        source_id=signal.source_id,
        processed_at=processing_time,
        score_before=existing.score if existing else None,
        score_after=new_score,
        is_propagated=False,
        upstream_source=None,
    ))
```

In `_apply_propagation()` — after each successful `_apply_root()` call:
```python
if self._history is not None:
    await self._history.record(SignalRecord(
        id=None,
        entity_key=entity_key,
        source_id=signal.source_id,
        processed_at=processing_time,
        score_before=score_before,  # returned from _apply_root()
        score_after=score_after,    # returned from _apply_root()
        is_propagated=True,
        upstream_source=root,  # the root entity that triggered this cascade
    ))
```

**Change**: `_apply_root()` must return `(score_before, score_after)` so `_apply_propagation()` can pass these to history without a second store read.

**Test cases**:
- Direct signal records event with `is_propagated=False`
- Propagated entity records event with `is_propagated=True` and correct `upstream_source`
- History=None: processor works normally, no recording attempted
- Benchmark test verifying that `POST /signals` processing time with history enabled does not exceed 2× baseline (no history) for a 10-node graph

---

### T013 — Wire GET /v1/inspector/entities/{entity_key}/signals
**Files**: `revok/proxy.py`, `tests/test_proxy.py`

- Route: `GET /v1/inspector/entities/{entity_key}/signals`
- Handler:
  - inspector disabled → 503
  - `inspector.get_signals(entity_key)` → None means history disabled → 501 `{"error": "signal_history_disabled"}`
  - empty list → 200 `{"entity_key": ..., "signals": []}`
  - 404 if entity not in store

Implement `RevokInspector.get_signals()`:
```python
async def get_signals(self, entity_key: str) -> list[SignalRecord] | None:
    entity = await self._store.get(entity_key)
    if entity is None:
        raise EntityNotFoundError(entity_key)  # handler converts to 404
    if self._history is None:
        return None  # signal history disabled
    return await self._history.get_for_entity(entity_key)
```

---

### T014 — Wire SqliteSignalHistoryStore in proxy.py
**Files**: `revok/proxy.py`

In `build_app()`:
- If `config.inspector.signal_history_enabled`:
  - Instantiate `SqliteSignalHistoryStore(path=config.state_store.sqlite_path)`
  - `await history_store.open()`
  - Pass to `SignalProcessor(..., history=history_store)`
  - Pass to `RevokInspector(..., history=history_store)`
- Else:
  - Pass `history=None` to both
- On shutdown: `await history_store.close()` (if instantiated)

---

## Phase 5 — Backend Contract Tests

### T015 — GraphReader contract test mixin
**Files**: `tests/contract/__init__.py` (NEW — empty), `tests/contract/test_graph_reader_contract.py` (NEW)

```python
class GraphReaderContract:
    """Mixin: subclass must provide self.make_backend() -> GraphBackend & GraphReader."""

    def make_backend(self): ...  # override in each subclass

    def test_has_node_false_for_unknown(self): ...
    def test_successors_empty_for_unknown(self): ...
    def test_predecessors_empty_for_unknown(self): ...
    def test_edge_weight_keyerror_for_missing_edge(self): ...
    def test_node_score_reflects_add_entity(self): ...
    def test_successors_after_add_relation(self): ...
    def test_predecessors_after_add_relation(self): ...
    def test_propagate_single_hop(self): ...
    def test_propagate_diamond_max_pressure(self): ...
    def test_propagate_cycle_terminates(self): ...
```

---

### T016 — NetworkX backend contract test
**Files**: `tests/test_causal_graph.py`

```python
class TestCausalGraphContract(GraphReaderContract):
    def make_backend(self):
        return CausalGraph()
```

Run automatically alongside existing `test_causal_graph.py` tests.

---

### T017 — End-to-end integration test
**Files**: `tests/test_inspector.py` (extend)

Round-trip test covering the core Revok explainability value: “Why did this entity’s confidence change?”

**Setup**:
1. Create `CausalGraph` + `SqliteStateStore` + `SqliteSignalHistoryStore` + `SignalProcessor` + `RevokInspector`
2. Configure causal relationship: A → B (weight=0.8)

**Explainability acceptance scenario** (entity B degraded via propagation from A):
3. Post signal for entity A via `process_one()` → verify `StateStore` updated for both A (direct) and B (propagated)
4. Verify `SignalHistoryStore` has two records: one with `is_propagated=False, entity_key="A"` and one with `is_propagated=True, entity_key="B", upstream_source="A"`
5. `inspect_entity("B")` → assert:
   - `score` reflects propagation degradation (lower than baseline)
   - `upstream` list contains A with correct edge weight (0.8)
   - `downstream` list is empty (B has no outgoing edges)
   - `inspected_at` is a recent Unix timestamp
6. `get_downstream("A")` → assert B is in results with correct propagated pressure
7. `get_paths("B")` → assert exactly one path `["A", "B"]` with per-hop pressures and `is_dominant=True`
8. `get_signals("B")` → assert record with `is_propagated=True`, `upstream_source="A"`, `score_after` matching stored score

This test validates the Inspector answers: “B’s confidence dropped because A received a signal that propagated to B with attenuated pressure.”

---

### T018 — Final quality gate
- `pytest tests/ -q` — all pass
- `mypy revok/ --strict` — clean
- `ruff check revok/ tests/` — clean
- Manual spot-check of all four Inspector endpoints via quickstart.md

---

## File Change Summary

| File | Change Type | Tasks |
|------|-------------|-------|
| `revok/models.py` | Modify | T001 |
| `revok/interfaces.py` | Modify | T002 |
| `revok/config.py` | Modify | T003 |
| `revok/causal_graph.py` | Modify | T004 |
| `revok/inspector.py` | Create | T006, T008, T009 |
| `revok/signal_history.py` | Create | T011 |
| `revok/signal_processor.py` | Modify | T012 |
| `revok/proxy.py` | Modify | T007, T010, T013, T014 |
| `config/revok.example.yaml` | Modify | T003 |
| `config/revok-zep.example.yaml` | Modify | T003 |
| `tests/contract/__init__.py` | Create | T015 |
| `tests/contract/test_graph_reader_contract.py` | Create | T015 |
| `tests/test_causal_graph.py` | Modify | T004, T016 |
| `tests/test_config.py` | Modify | T003 |
| `tests/test_inspector.py` | Create | T006–T010, T013, T017 |
| `tests/test_signal_history.py` | Create | T011 |
| `tests/test_signal_processor.py` | Modify | T012 |
| `tests/test_proxy.py` | Modify | T007, T010, T013 |
| `.github/copilot-instructions.md` | Modify | T005 |

---

## Design Artifacts

- [spec.md](spec.md) — feature requirements
- [research.md](research.md) — 8 design decisions
- [data-model.md](data-model.md) — entities, protocols, DB schema
- [contracts/inspector-api.md](contracts/inspector-api.md) — HTTP + config + GraphReader contracts
- [quickstart.md](quickstart.md) — verification workflow

---

## Agent Context Update

Plan reference updated in `.github/copilot-instructions.md` → `specs/006-inspector/plan.md`.
