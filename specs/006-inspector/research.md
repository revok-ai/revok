# Research — Inspector (006)

**Branch**: feat/inspector
**Date**: 2026-06-23
**Status**: Complete — all clarifications resolved

---

## 1. GraphReader vs. GraphBackend Extension

**Decision**: Introduce a separate `GraphReader` Protocol; do **not** extend `GraphBackend`.

**Rationale**:
- `GraphBackend` has two write methods (`add_entity`, `add_relation`) and one propagation-compute method (`propagate`). These are the consumer concerns of the signal processor.
- The Inspector needs read-only structural queries: `has_node`, `successors`, `predecessors`, `edge_weight`, `node_score`. Attaching these to `GraphBackend` creates a fat interface that write-only callers must stub.
- A separate `GraphReader` Protocol maintains single-responsibility: signal processor depends on `GraphBackend`; Inspector depends on `GraphReader`. Both protocols are implemented by `CausalGraph`.
- This approach follows the Interface Segregation principle without violating Constitution § III (Interface First — both protocols are defined in `revok/interfaces.py` before any implementation).

**User direction**: User explicitly stated "Prefer separate read/query capabilities if the existing GraphBackend abstraction becomes too broad."

**Alternatives considered**:
- Extend `GraphBackend` with optional read methods → rejected (fat interface, forces unnecessary stubs for existing signal-processor-only callers)
- Single `CausalInspector` that directly uses `CausalGraph` → rejected (violates FR-002, couples Inspector to NetworkX)
- Mixin class `GraphBackend + GraphReader` → rejected (unnecessary complexity; both protocols can independently be runtime_checkable)

---

## 2. Signal History Persistence

**Decision**: Introduce a `SignalHistoryStore` Protocol and a `SqliteSignalHistoryStore` implementation. Inject into `SignalProcessor` as an optional dependency (None disables recording).

**Rationale**:
- The signal processor must remain unaware of Inspector concerns. A Protocol injection point is the cleanest boundary.
- `SqliteStateStore` already owns the `entity_records` table. Adding a `signal_events` table to the same database file is operationally simpler (single backup, single WAL checkpoint) but adding it to `SqliteStateStore` would conflate entity state with event log concerns.
- Separate `SqliteSignalHistoryStore` class with its own connection to the same database file: separate concern, same file path (configurable). Simpler than a separate DB file.
- Config flag `inspector.signal_history_enabled: bool` gates both recording and the `/signals` endpoint. When false, `SignalProcessor` receives `None` for the history store and skips recording.

**Alternatives considered**:
- Persist signal history to a separate SQLite file → rejected (operational overhead)
- Add `SignalRecord` list to `EntityRecord` → rejected (bloats entity model, breaks StateStore schema)
- Append-only log file → rejected (not queryable, not config-driven)

---

## 3. Graph Backend Scope

**Decision**: NetworkX (`CausalGraph`) is the only `GraphReader` implementation in this feature. The `GraphReader` Protocol is intentionally designed to be backend-agnostic.

**Rationale**:
- NetworkX (BSD-3) has no licensing concerns.
- The Protocol abstraction ensures any future graph backend can be added by implementing `GraphReader` with zero changes to the Inspector.

---

## 4. Inspector Implementation: Sync vs. Async

**Decision**: `GraphReader` methods are **synchronous** (matching current `GraphBackend`). `Inspector` methods are **async** (for `StateStore` access).

**Rationale**:
- Both `GraphBackend` and `CausalGraph` are already synchronous (in-memory NetworkX DiGraph with no I/O).
- `StateStore` is async (SQLite via aiosqlite). Inspector must `await store.get()`.
- Keeping `GraphReader` sync preserves the existing pattern and avoids unnecessary async overhead for in-memory graph operations.

---

## 5. Propagation Path Algorithm (US-IP2)

**Decision**: Backward BFS from target entity using `predecessors()`.

**Rationale**:
- To find "all paths from any upstream root to target", traverse the graph backward (reversed edge direction).
- A root entity is any node with no predecessors (in-degree 0).
- Backward BFS terminates when it reaches root nodes (no predecessors).
- Per-hop pressure is computed forward (root → target) after path discovery for display purposes.
- Cycle safety: visited set prevents revisiting nodes.
- For max_hops, cap backward BFS depth.

**Alternatives considered**:
- Forward BFS from every possible root → rejected (requires `all_nodes()`, expensive for large graphs)
- NetworkX `all_simple_paths` → rejected (couples path algorithm to NetworkX; must work through GraphReader interface)

---

## 6. API Namespace and Response Schema

**Decision**: Routes at `/v1/inspector/entities/{entity_key}` (and sub-paths). JSON responses with stable snake_case fields. HTTP 404 for missing entity keys.

**Rationale**:
- Namespace `/v1/inspector/` avoids collision with existing `/v1/entities/{key}` read route.
- Consistent with existing Revok route style.
- `entity_key` in path is URL-decoded at the handler (aiohttp does this automatically via `request.match_info`).

---

## 7. InspectorConfig Placement

**Decision**: Add `InspectorConfig` to `revok/config.py` with `enabled: bool`, `signal_history_enabled: bool`. Reuse `causal_graph` config for `max_hops`, `min_pressure`, `attenuation` in Inspector queries (no duplication).

**Rationale**:
- `causal_graph` config already owns propagation bounds; duplicating them in inspector config creates drift risk.
- Inspector reads those bounds from the existing `CausalGraphConfig` at query time.

---

## 8. Contract Test Structure

**Decision**: Define a shared abstract test contract in `tests/contract/test_graph_reader_contract.py` as a mixin/base class. `CausalGraph` test module imports and runs the contract.

**Rationale**:
- Ensures any `GraphReader` implementation satisfies the same behavioral guarantees.
- Sets up the pattern for future backend implementations without coupling to them now.

---

## Summary of Resolved Clarifications

| # | Clarification | Resolution |
|---|---------------|------------|
| 1 | Extend GraphBackend vs. new GraphReader? | New `GraphReader` Protocol |
| 2 | Where to persist signal history? | New `SqliteSignalHistoryStore`, same DB file |
| 3 | Graph backend scope | NetworkX only; `GraphReader` Protocol is backend-agnostic |
| 4 | GraphReader sync or async? | Sync (in-memory graph, consistent with GraphBackend) |
| 5 | Path-finding algorithm? | Backward BFS via `predecessors()` |
| 6 | API namespace? | `/v1/inspector/entities/{entity_key}/...` |
| 7 | Inspector config placement? | New `InspectorConfig`; reuse `CausalGraphConfig` bounds |
| 8 | Contract test structure? | Shared mixin in `tests/contract/` |
