# Tasks: Inspector (006)

**Feature**: 006-inspector
**Branch**: feat/inspector
**Plan**: [plan.md](plan.md)
**Spec**: [spec.md](spec.md)

---

## Phase 1: Setup — Design & Contracts

**Purpose**: Define all types and protocols before any implementation. Interface-first rule (Constitution § III): models before protocols, protocols before all concrete implementations. Every task here blocks all user story phases.

- [x] T001 Add 5 frozen dataclasses to `revok/models.py`: `CausalNeighbor`, `InspectionReport` (with `inspected_at: float`), `DownstreamEntity`, `PropagationPath`, `SignalRecord`
- [x] T002 Define 3 new Protocols in `revok/interfaces.py`: `GraphReader` (sync), `SignalHistoryStore` (async), `Inspector` (async); add to `tests/test_models.py` or new `tests/test_interfaces.py` if needed for Protocol conformance checks
- [x] T003 [P] Add `InspectorConfig` dataclass (enabled, signal_history_enabled, signal_history_max_rows, `max_paths: int = 100`) to `revok/config.py`; add YAML block to `config/revok.example.yaml` and `config/revok-zep.example.yaml`; add validation and test cases in `tests/test_config.py`
- [x] T004 [P] Extend `CausalGraph` class declaration to `class CausalGraph(GraphBackend, GraphReader):` in `revok/causal_graph.py`; implement `has_node()`, `successors()`, `predecessors()`, `edge_weight()`, `node_score()`; extend `tests/test_causal_graph.py` with GraphReader-specific tests
- [x] T005 Update plan reference between `<!-- SPECKIT START -->` and `<!-- SPECKIT END -->` markers in `.github/copilot-instructions.md` to point to `specs/006-inspector/plan.md`

**Checkpoint**: All types, protocols, and config defined. CausalGraph implements GraphReader. Phase 2+ can begin.

---

## Phase 2: Foundational

No additional shared infrastructure needed beyond Phase 1. Phase 1 is the blocking foundational layer for this feature.

---

## Phase 3: User Story US-IP1 — Inspect entity state and direct causal neighbors (Priority: P1) 🎯 MVP

**Story**: As a developer, when a memory is behaving unexpectedly (lower confidence than anticipated), I want to query a single endpoint for an entity to see its current state and its immediate causal neighbors so I can determine at a glance whether the issue is local or caused by an upstream cascade.

**Goal**: Implement `RevokInspector.inspect_entity()` and wire `GET /v1/inspector/entities/{entity_key}`.

**Independent Test**: `GET /v1/inspector/entities/alice` returns `score`, `signal_count`, `contradiction_count`, `inspected_at`, `upstream`/`downstream` neighbor lists. Returns 404 for unknown keys. Returns 503 when inspector disabled. Entity with no graph node returns empty upstream/downstream lists, not an error.

- [x] T006 [US1] Create `revok/inspector.py` (NEW) with `RevokInspector.__init__(store, graph, history)` and `inspect_entity(entity_key) -> InspectionReport | None`; create `tests/test_inspector.py` (NEW) with test cases: entity in store + graph, entity in store with no graph node, entity not in store (→ None), neighbor in graph but not in store (→ score: None)
- [x] T007 [US1] Wire `GET /v1/inspector/entities/{entity_key}` route in `revok/proxy.py`; instantiate `RevokInspector` in `build_app()`; add tests in `tests/test_proxy.py` for 200, 404, and 503 (inspector disabled)

**Checkpoint**: US-IP1 fully functional. A developer can call `GET /v1/inspector/entities/{entity_key}` and see entity state with causal neighbor lists.

---

## Phase 4: User Stories US-IP2 + US-IP3 — Propagation paths and downstream blast radius (Priority: P2)

**Story US-IP2**: As a developer, I want to trace all propagation paths from upstream roots to a target entity to identify which root triggered the cascade.

**Story US-IP3**: As an operator, I want to know all entities reachable from a root within configured propagation bounds to assess blast radius.

**Goal**: Implement `get_downstream()` and `get_paths()` methods; wire `/downstream` and `/paths` routes.

**Independent Test (US-IP2)**: `GET /v1/inspector/entities/C/paths` on chain A→B→C returns path `["A","B","C"]` with per-hop pressures. Diamond A→B→D, A→C→D returns two paths, one `is_dominant: true`. `max_paths=1` on diamond returns exactly 1 path.

**Independent Test (US-IP3)**: `GET /v1/inspector/entities/A/downstream` on a multi-level graph returns B, C, D with pressures. Cycle graph terminates. `max_hops=1` returns only direct neighbors. Entity below `min_pressure` threshold is excluded.

- [x] T008 [P] [US2] [US3] Implement `RevokInspector.get_downstream()` (forward BFS, max-pressure semantics, cycle-safe) in `revok/inspector.py`; add test cases in `tests/test_inspector.py`: linear chain, diamond, cycle, `max_hops=1`, pressure threshold exclusion
- [x] T009 [P] [US2] Implement `RevokInspector.get_paths()` (backward BFS, `max_paths` cap, pressure forward pass, `is_dominant` marking) in `revok/inspector.py`; add test cases: single path, diamond (two paths + dominant), no upstream (empty), cycle safety, `max_paths=1` on diamond, `max_paths=2` on 5-path graph
- [x] T010 [US2] [US3] Wire `GET /v1/inspector/entities/{entity_key}/downstream` and `GET /v1/inspector/entities/{entity_key}/paths` routes in `revok/proxy.py`; `/paths` reads `max_paths` from `config.inspector.max_paths` (not user-overridable); add tests in `tests/test_proxy.py` for 200, 404, 503

**Checkpoint**: US-IP2 and US-IP3 independently testable. All four Inspector read paths functional (entity + downstream + paths; signals pending).

---

## Phase 5: User Story US-IP4 — Inspect signal history that affected an entity (Priority: P3)

**Story**: As a developer, when an entity's confidence is unexpectedly low, I want to see a log of signal events (direct and propagated) that have affected it so I can trace invalidation back to its origin.

**Goal**: Implement `SqliteSignalHistoryStore`, inject into `SignalProcessor`, wire `/signals` endpoint, connect lifecycle in proxy.

**Independent Test**: Post signal for `alice` → `GET /v1/inspector/entities/alice/signals` returns record with `is_propagated: false`. Downstream `bob` returns record with `is_propagated: true`, `upstream_source: "alice"`. Empty list for entity with no signals. 404 for unknown entity. 501 when `signal_history_enabled: false`.

- [x] T011 [US4] Create `revok/signal_history.py` (NEW) with `SqliteSignalHistoryStore` implementing `SignalHistoryStore` Protocol (`open()`, `record()`, `get_for_entity()`, `trim_for_entity()`, `close()`); `record()` enforces `signal_history_max_rows` via trim; create `tests/test_signal_history.py` (NEW) with round-trip, trim, is_propagated persistence, idempotent close
- [x] T012 [US4] Inject `history: SignalHistoryStore | None` into `SignalProcessor.__init__()` in `revok/signal_processor.py`; record events in `_apply_root()` (is_propagated=False) and `_apply_propagation()` (is_propagated=True, upstream_source=root); `_apply_root()` must return `(score_before, score_after)`; add tests in `tests/test_signal_processor.py` including benchmark (history-enabled ≤ 2× baseline latency on 10-node graph)
- [x] T013 [US4] Implement `RevokInspector.get_signals()` in `revok/inspector.py`; wire `GET /v1/inspector/entities/{entity_key}/signals` in `revok/proxy.py`; 404 for unknown entity, 501 for history disabled (returns None), 200 with empty list for entity with no signals; add tests in `tests/test_proxy.py`
- [x] T014 [US4] Wire `SqliteSignalHistoryStore` instantiation in `revok/proxy.py` `build_app()`: open on startup, pass to both `SignalProcessor` and `RevokInspector`, close on shutdown; no-op when `signal_history_enabled: false`

**Checkpoint**: US-IP4 independently testable. Full signal history round-trip works for both direct and propagated signals.

---

## Phase 6: Polish — Contract Tests, E2E, and Quality Gate

**Purpose**: Backend contract tests ensure any future `GraphReader` implementation satisfies the protocol; E2E test validates the full explainability value proposition; quality gate confirms the feature is shippable.

- [ ] T015 [P] Create `tests/contract/__init__.py` (NEW, empty) and `tests/contract/test_graph_reader_contract.py` (NEW) with `GraphReaderContract` mixin: `test_has_node_false_for_unknown`, `test_successors_empty_for_unknown`, `test_predecessors_empty_for_unknown`, `test_edge_weight_keyerror_for_missing_edge`, `test_node_score_reflects_add_entity`, `test_successors_after_add_relation`, `test_predecessors_after_add_relation`, `test_propagate_single_hop`, `test_propagate_diamond_max_pressure`, `test_propagate_cycle_terminates`
- [ ] T016 [P] Add `class TestCausalGraphContract(GraphReaderContract)` with `make_backend(self): return CausalGraph()` to `tests/test_causal_graph.py` to run the full contract suite against the NetworkX backend
- [ ] T017 Extend `tests/test_inspector.py` with full explainability acceptance scenario: setup CausalGraph + SqliteStateStore + SqliteSignalHistoryStore + SignalProcessor + RevokInspector with A→B (weight=0.8); process signal for A; assert StateStore updated for both A and B; assert SignalHistoryStore has is_propagated=False for A and is_propagated=True, upstream_source="A" for B; assert inspect_entity("B") returns correct score + upstream list with A + empty downstream + recent inspected_at; assert get_downstream("A") returns B; assert get_paths("B") returns path ["A","B"] with is_dominant=True; assert get_signals("B") returns record with upstream_source="A"
- [ ] T018 Run final quality gate: `pytest tests/ -q` (all pass), `mypy revok/ --strict` (clean), `ruff check revok/ tests/` (clean), manual spot-check all four Inspector endpoints via `quickstart.md`

**Checkpoint**: All acceptance criteria met. Feature complete and ready for review.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 3 (US-IP1)**: Requires Phase 1 complete — BLOCKS all user story phases
- **Phase 4 (US-IP2+3)**: Requires Phase 3 complete (`revok/inspector.py` must exist before adding methods)
- **Phase 5 (US-IP4)**: T011 and T012 can start after Phase 1 complete (independent of inspector.py); T013–T014 require Phase 3 complete
- **Phase 6 (Polish)**: Requires Phases 3, 4, and 5 complete

### User Story Dependencies

- **US-IP1 (P1)**: Depends only on Phase 1 — no story dependencies
- **US-IP2 (P2)**: Depends on US-IP1 (adds methods to existing `RevokInspector` class)
- **US-IP3 (P2)**: Depends on US-IP1 (same); T008 and T009 are independent of each other
- **US-IP4 (P3)**: T011/T012 depend only on Phase 1; T013/T014 depend on Phase 3 (US-IP1)

### Within Each Phase

- T001 → T002 (models before protocols — protocols reference model types)
- T002 → T006 (Inspector Protocol before RevokInspector implementation)
- T002, T003, T004 → parallel after T001
- T004 → T016 (CausalGraph must implement GraphReader before contract test)
- T006 → T007 (inspector.py must exist before proxy wires it)
- T008 ‖ T009 (parallel — different methods in inspector.py, no cross-dependency)
- T008, T009 → T010 (both methods must exist before wiring routes)
- T011 → T012 → T013 (history store → inject → wire endpoint)
- T012, T013 → T014 (both must be in place before proxy lifecycle wires them)

### Parallel Opportunities

- **Phase 1**: T002, T003, T004 can run in parallel once T001 is done
- **Phase 4**: T008 and T009 run in parallel (different methods, no conflict)
- **Phase 5**: T011 and T012 can start after Phase 1 (independent of Phase 3 work)
- **Phase 6**: T015 and T016 run in parallel

---

## Parallel Execution Example: Phase 4

```bash
# After Phase 3 (US-IP1) is complete:
# Launch T008 and T009 simultaneously — they touch different methods in inspector.py
Task T008: "Implement get_downstream() in revok/inspector.py + tests"
Task T009: "Implement get_paths() with max_paths cap in revok/inspector.py + tests"
# Then proceed sequentially:
Task T010: "Wire /downstream and /paths routes in revok/proxy.py"
```

## Parallel Execution Example: Phase 5 (overlap with Phase 4)

```bash
# T011 and T012 can start as soon as Phase 1 is done — independent of inspector.py:
Task T011: "Create SqliteSignalHistoryStore in revok/signal_history.py"
Task T012: "Inject SignalHistoryStore into SignalProcessor"
# T013 and T014 must wait for Phase 3 (RevokInspector must exist):
Task T013: "Implement get_signals() and wire /signals route"
Task T014: "Wire history lifecycle in build_app()"
```

---

## Implementation Strategy

### MVP First (US-IP1 Only)

1. Complete Phase 1 (Setup — all design contracts, ~5 tasks)
2. Complete Phase 3 (US-IP1 — entity inspection endpoint, 2 tasks)
3. **STOP and VALIDATE**: Call `GET /v1/inspector/entities/{entity_key}` end-to-end
4. Demo / merge if the core explainability endpoint is sufficient

### Incremental Delivery

1. **Phase 1** → Foundation complete (types, protocols, config, CausalGraph extended)
2. **Phase 3** (US-IP1) → Entity inspection API ← **MVP**
3. **Phase 4** (US-IP2 + US-IP3) → Propagation paths + blast radius
4. **Phase 5** (US-IP4) → Signal history persistence
5. **Phase 6** → Contract tests, E2E, quality gate

### File Change Map

| File | Change | Tasks |
|------|--------|-------|
| `revok/models.py` | Modify | T001 |
| `revok/interfaces.py` | Modify | T002 |
| `revok/config.py` | Modify | T003 |
| `config/revok.example.yaml` | Modify | T003 |
| `config/revok-zep.example.yaml` | Modify | T003 |
| `revok/causal_graph.py` | Modify | T004 |
| `.github/copilot-instructions.md` | Modify | T005 |
| `revok/inspector.py` | Create | T006, T008, T009, T013 |
| `revok/proxy.py` | Modify | T007, T010, T013, T014 |
| `revok/signal_history.py` | Create | T011 |
| `revok/signal_processor.py` | Modify | T012 |
| `tests/contract/__init__.py` | Create | T015 |
| `tests/contract/test_graph_reader_contract.py` | Create | T015 |
| `tests/test_causal_graph.py` | Modify | T004, T016 |
| `tests/test_config.py` | Modify | T003 |
| `tests/test_inspector.py` | Create | T006–T010, T013, T017 |
| `tests/test_signal_history.py` | Create | T011 |
| `tests/test_signal_processor.py` | Modify | T012 |
| `tests/test_proxy.py` | Modify | T007, T010, T013 |
