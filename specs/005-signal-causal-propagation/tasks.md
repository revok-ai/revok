# Tasks — Signal-Driven Causal Propagation (005)
**Feature**: 005-signal-causal-propagation  
**Branch**: feat/causal-graph-bfs-propagation  
**Generated**: 2026-06-20  
**Baseline tests**: 248 passing

---

## Phase 1 — Setup: Interface Gate (Constitution § III)

> All pluggable components require a Protocol in `interfaces.py` before implementation.
> `GraphBackend` is named in the constitution and must be defined first.

- [ ] T001 Define `GraphBackend` Protocol in `revok/interfaces.py`
- [ ] T002 [P] Add `GraphBackend` import to `revok/__init__.py` exports (if applicable)

---

## Phase 2 — Foundational: Config & Scoring Prerequisites

> Config blocks (`causal_graph`, `severity_weights`) and pressure-aware scoring must
> exist before any consumer or graph implementation can be built or tested.

- [ ] T003 Add `CausalRelationshipConfig` and `CausalGraphConfig` dataclasses to `revok/config.py`
- [ ] T004 Add `severity_weights` and `default_severity` fields to `ScoringConfig` in `revok/config.py`
- [ ] T005 Add `load_config()` parsing for `causal_graph` section in `revok/config.py`
- [ ] T006 Add `load_config()` parsing for `scoring.severity_weights` and `scoring.default_severity` in `revok/config.py`
- [ ] T007 [P] Add `causal_graph` example block to `config/revok.example.yaml`
- [ ] T008 [P] Add `causal_graph` example block to `config/revok-zep.example.yaml`
- [ ] T009 Add `severity_weights` example block to `config/revok.example.yaml`
- [ ] T010 Add `severity_weights` example block to `config/revok-zep.example.yaml`
- [ ] T011 Add `score_with_pressure(existing, now, pressure)` method to `ScoringEngine` in `revok/scoring.py`
- [ ] T012 Add `resolve_pressure(severity, severity_weights, default_severity)` method to `ScoringEngine` in `revok/scoring.py`

---

## Phase 3 — US-CP2 & US-CP3 & US-CP4: CausalGraph Propagation

> Story goal: `CausalGraph.propagate()` returns correct bounded, cycle-safe,
> max-path-pressure results. Independently testable without a live consumer or server.

**Independent test criteria**: `CausalGraph.propagate()` can be called in isolation
with a manually-constructed graph and asserted with exact numeric results.

- [ ] T013 [US2] Extend `CausalGraph.__init__` to pass `weight` when adding edges in `revok/causal_graph.py`
- [ ] T014 [US2] Update `CausalGraph.add_relation()` to accept and store `weight: float` parameter in `revok/causal_graph.py`
- [ ] T015 [US2] Implement `CausalGraph.propagate(root, initial_pressure, max_hops, min_pressure, attenuation)` in `revok/causal_graph.py`
- [ ] T016 [US2] Implement `CausalGraph` as implementing `GraphBackend` Protocol in `revok/causal_graph.py`
- [ ] T017 [P] [US2] Write `TestPropagate` class — linear chain pressure attenuation in `tests/test_causal_graph.py`
- [ ] T018 [P] [US2] Write diamond topology test — max-path-wins, not sum in `tests/test_causal_graph.py`
- [ ] T019 [P] [US3] Write cross-root max-pressure test — two roots overlapping on same downstream entity in `tests/test_causal_graph.py`
- [ ] T020 [P] [US4] Write cyclic graph test — A→B→C→A terminates without error in `tests/test_causal_graph.py`
- [ ] T021 [P] [US2] Write hop-limit cutoff test — propagation stops at `max_hops` in `tests/test_causal_graph.py`
- [ ] T022 [P] [US2] Write min-pressure cutoff test — branch stops when pressure < `min_pressure` in `tests/test_causal_graph.py`
- [ ] T023 [P] [US2] Write no-outgoing-edges test — empty result dict for isolated entity in `tests/test_causal_graph.py`
- [ ] T024 [P] [US2] Write unknown root test — root not in graph returns empty dict, no error in `tests/test_causal_graph.py`

---

## Phase 4 — US-CP1: Signal Consumer Service

> Story goal: A consumed signal degrades the root entity's score and persists the
> result. Independently testable with an in-memory store and mock bus.

**Independent test criteria**: `SignalProcessor.process_one(signal)` degrades a
pre-seeded entity record when called directly; no server or proxy required.

- [ ] T025 [US1] Create `revok/signal_processor.py` with `SignalProcessor` class
- [ ] T026 [US1] Implement `SignalProcessor.__init__(bus, store, scorer, graph, config)` in `revok/signal_processor.py`
- [ ] T027 [US1] Implement `SignalProcessor._resolve_entities(signal) -> list[str]` (iterate `entity_refs` from `/signals` body only; no header/matcher fallback) in `revok/signal_processor.py`
- [ ] T028 [US1] Implement `SignalProcessor._apply_root(entity_key, pressure, now)` — fetch, score, persist in `revok/signal_processor.py`
- [ ] T029 [US2] Implement `SignalProcessor._apply_propagation(root_keys, pressure)` — call graph.propagate per root, collect max-pressure map, apply+persist each downstream in `revok/signal_processor.py`
- [ ] T030 [US1] Implement `SignalProcessor.process_one(signal)` — resolve → root pressure → apply_root per entity → apply_propagation in `revok/signal_processor.py`
- [ ] T031 [US1] Implement `SignalProcessor.run()` — async loop: `consume()` → `process_one()` → log ERROR on exception → continue in `revok/signal_processor.py`
- [ ] T032 [P] [US1] Write test: single root entity score decreases after `process_one()` in `tests/test_signal_processor.py`
- [ ] T033 [P] [US1] Write test: unknown severity falls back to `default_severity` pressure in `tests/test_signal_processor.py`
- [ ] T034 [P] [US1] Write test: missing or empty `entity_refs` → signal discarded, no error in `tests/test_signal_processor.py`
- [ ] T035 [P] [US1] Write test: store error during `_apply_root` → logs ERROR, does not propagate exception in `tests/test_signal_processor.py`
- [ ] T036 [P] [US2] Write test: downstream entity scores decrease after `process_one()` with configured graph in `tests/test_signal_processor.py`
- [ ] T037 [P] [US3] Write test: two-root signal with overlapping propagation trees — downstream entity gets max pressure, not sum in `tests/test_signal_processor.py`

---

## Phase 5 — US-CP5: Lifecycle Integration

> Story goal: Consumer starts with the server and shuts down cleanly. No orphaned tasks.

**Independent test criteria**: `build_app()` returns an app where the consumer
background task is started and cancelled during the test without server teardown errors.

- [ ] T038 [US5] Add `CausalGraphConfig` and `severity_weights` loading to `revok/proxy.py`'s `build_app()` graph wiring in `revok/proxy.py`
- [ ] T039 [US5] Instantiate `CausalGraph` from config relationships at startup in `revok/proxy.py`
- [ ] T040 [US5] Instantiate `SignalProcessor` with wired dependencies in `revok/proxy.py`
- [ ] T041 [US5] Start `SignalProcessor.run()` as `asyncio` background task on `app.on_startup` in `revok/proxy.py`
- [ ] T042 [US5] Cancel and await consumer task on `app.on_cleanup` in `revok/proxy.py`
- [ ] T043 [P] [US5] Write test: consumer task is created on app startup in `tests/test_proxy.py`
- [ ] T044 [P] [US5] Write test: consumer task is cancelled on app cleanup with no exception in `tests/test_proxy.py`
- [ ] T045 [P] [US5] Write test: `POST /signals` returns 202 with consumer active in `tests/test_proxy.py`

---

## Phase 6 — Polish & Cross-Cutting Concerns

- [ ] T046 Add config tests for `causal_graph` section validation (valid/invalid weights, missing keys) in `tests/test_config.py`
- [ ] T047 Add config tests for `severity_weights` validation (out-of-range, missing default) in `tests/test_config.py`
- [ ] T048 Add scoring tests for `score_with_pressure()` and `resolve_pressure()` in `tests/test_scoring.py`
- [ ] T049 Update architecture diagram in `README.md` — mark causal graph pipeline as implemented
- [ ] T050 Add `causal_graph` operator documentation section to `README.md`
- [ ] T051 Add `[0.3.0]` entry to `CHANGELOG.md`
- [ ] T052 Add regression test: write-enrichment path in `revok/metadata_writer.py` unchanged for memory-write requests in `tests/test_metadata_writer.py`
- [ ] T053 Add proxy integration test: write requests still use enrich + upstream write path exactly as before in `tests/test_proxy.py`
- [ ] T054 Run `pytest` — all 248 + new tests must pass
- [ ] T055 Run `ruff check revok/ tests/` — 0 errors
- [ ] T056 Run `mypy revok/` — 0 errors
- [ ] T057 Verify no new runtime dependencies added (pyproject dependency diff check) in `pyproject.toml`

---

## Dependencies

```
Phase 1 (T001-T002) → Phase 2 (T003-T012) → Phase 3 (T013-T024)
                                            → Phase 4 (T025-T037)
Phase 3 + Phase 4 → Phase 5 (T038-T045)
Phase 5 → Phase 6 (T046-T057)
```

Phases 3 and 4 are independent of each other and can be worked in parallel.
Within each phase, tasks marked `[P]` are parallelizable.

---

## Parallel Execution Opportunities

**Phase 3** (after T013-T016 complete): T017–T024 are all independent test cases
and can be written in any order.

**Phase 4** (after T025-T031 complete): T032–T037 are all independent test cases.

**Phase 5** (after T038-T042 complete): T043–T045 are independent test cases.

**Phase 2**: T007–T010 (config example files) can be written in parallel with T003–T006.

---

## Implementation Strategy

**MVP scope (Phases 1-4)**: After Phase 4, `SignalProcessor` is fully functional and
testable in isolation. This is the minimum shippable unit — `/signals` degrades
entities and propagates causally, even without server lifecycle wiring.

**Full feature (Phase 5)**: Lifecycle integration makes it work in the running server.

**Polish (Phase 6)**: Documentation and lint gates. Required before merge to `develop`.

---

## File Change Summary

| File | Type | Phases |
|------|------|--------|
| `revok/interfaces.py` | Modify | 1 |
| `revok/config.py` | Modify | 2 |
| `revok/scoring.py` | Modify | 2 |
| `revok/causal_graph.py` | Modify | 3 |
| `revok/signal_processor.py` | Create | 4 |
| `revok/proxy.py` | Modify | 5 |
| `config/revok.example.yaml` | Modify | 2 |
| `config/revok-zep.example.yaml` | Modify | 2 |
| `tests/test_config.py` | Modify | 6 |
| `tests/test_scoring.py` | Modify | 6 |
| `tests/test_causal_graph.py` | Modify | 3 |
| `tests/test_signal_processor.py` | Create | 4 |
| `tests/test_proxy.py` | Modify | 5 |
| `README.md` | Modify | 6 |
| `CHANGELOG.md` | Modify | 6 |
