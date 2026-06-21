# Implementation Plan — Signal-Driven Causal Propagation (005)
**Feature**: 005-signal-causal-propagation  
**Branch**: feat/causal-graph-bfs-propagation  
**Date**: 2026-06-20  
**Status**: Planning complete

---

## Technical Context

- **Python**: 3.11+, `from __future__ import annotations` throughout
- **Async runtime**: `asyncio` + `aiohttp` server lifecycle
- **Queue**: `AsyncioQueueBus` (already implemented; currently publish-only)
- **Persistence**: `SqliteStateStore` with WAL + hot layer
- **Scoring**: `ScoringEngine` decay + degradation model (currently fixed signal strength)
- **Graph**: `CausalGraph` backed by `networkx.DiGraph` (currently scaffold)
- **Tests**: `pytest` + `pytest-asyncio`, strict type/lint gates (`mypy`, `ruff`)
- **Constraints**:
  - Preserve `metadata_writer.py` write-enrichment behavior
  - No new dependencies
  - Keep read path latency unchanged

## Constitution Check

Constitution found at `.specify/memory/constitution.md`. Planning and implementation must comply with all non-negotiable gates, especially Interface First (GraphBackend protocol before graph implementation), OSS boundary, and async-first behavior.

| Gate | Status | Notes |
|------|--------|-------|
| Existing write path behavior preserved | PASS | `metadata_writer.enrich()` unchanged |
| No new dependencies | PASS | networkx + stdlib asyncio only |
| Read path remains non-blocking | PASS | async background consumer off read path |
| Backward compatibility | PASS | additive config section with safe defaults |
| Test and lint quality gates | PASS | must keep pytest + ruff + mypy clean |

---

## Phase 0 — Research

Research completed in [research.md](research.md) with all clarifications resolved:
- Consumer lifecycle integration model
- Root-entity resolution for `/signals`
- Pressure application semantics aligned with existing scorer behavior
- BFS propagation algorithm for cycle safety + max-path pressure in diamonds
- Config schema for causal relationships and propagation controls

---

## Phase 1 — Design & Contracts

### T001 — Config model for causal graph
**Files**: `revok/config.py`, `config/revok.example.yaml`, `config/revok-zep.example.yaml`, `tests/test_config.py`
- Add `causal_graph` config block with:
  - `relationships: list[{from,to,weight}]`
  - `propagation: {min_pressure,max_hops,attenuation}`
- Validate bounds and defaults:
  - `0 < attenuation <= 1`
  - `0 <= min_pressure <= 1`
  - `max_hops >= 0`
  - edge `weight` in `(0, 1]`

### T002 — Causal graph propagation implementation
**Files**: `revok/causal_graph.py`, `tests/test_causal_graph.py`
- Extend graph to store weighted directed edges
- Add `propagate(root, initial_pressure, max_hops, min_pressure, attenuation)`
- Requirements:
  - bounded traversal
  - cycle-safe
  - max pressure per target (diamond path behavior)
  - no pressure summation

### T003 — Signal processor service
**Files**: `revok/signal_processor.py` (new), `tests/test_signal_queue.py` (or new `tests/test_signal_processor.py`)
- Add async consumer loop:
  - `consume()` from bus
  - resolve root entity from signal
  - apply root degradation
  - call graph propagation
  - apply degradation to downstream entities
  - persist via `StateStore`
- Handle malformed/non-resolvable signals with safe logging and continue

### T004 — Scoring pressure integration
**Files**: `revok/scoring.py`, `tests/test_scoring.py`
- Add pressure-aware scoring method that preserves existing formula semantics and defaults
- Keep existing `score()` behavior unchanged for current call sites
- Ensure propagated pressure can be applied without modifying write-enrichment path

### T005 — Proxy lifecycle integration
**Files**: `revok/proxy.py`, `tests/test_proxy.py`
- Start background signal-consumer task on app startup
- Clean cancellation on shutdown
- Keep `/signals` endpoint behavior (`202 Accepted`) unchanged

### T006 — Runtime graph wiring
**Files**: `revok/proxy.py` (or constructor wiring module), tests as needed
- Build causal graph from config relationships at startup
- Inject graph + propagation settings into signal processor

### T007 — End-to-end `/signals` behavior tests
**Files**: `tests/test_proxy.py`, `tests/test_state_store.py`, new integration test file if needed
- Validate measurable degradation for root-only signals
- Validate downstream propagation with attenuation/hops/threshold
- Validate diamond max-path behavior
- Validate cyclic graph termination and correctness

### T008 — Documentation alignment
**Files**: `README.md`, `CHANGELOG.md`
- Update architecture section from aspirational to implemented behavior
- Add concise operator docs for `causal_graph` config block

### T009 — Final validation
- Run targeted tests for new modules/features
- Run full test suite
- Run `ruff` and `mypy`

---

## File Change Summary

| File | Change Type | Tasks |
|------|-------------|-------|
| `revok/config.py` | Modify | T001 |
| `revok/causal_graph.py` | Modify | T002 |
| `revok/signal_processor.py` | Create | T003 |
| `revok/scoring.py` | Modify | T004 |
| `revok/proxy.py` | Modify | T005, T006 |
| `config/revok.example.yaml` | Modify | T001 |
| `config/revok-zep.example.yaml` | Modify | T001 |
| `tests/test_config.py` | Modify | T001 |
| `tests/test_causal_graph.py` | Modify | T002 |
| `tests/test_scoring.py` | Modify | T004 |
| `tests/test_proxy.py` | Modify | T005, T007 |
| `tests/test_signal_processor.py` | Create | T003, T007 |
| `README.md` | Modify | T008 |
| `CHANGELOG.md` | Modify | T008 |

---

## Design Artifacts

- [spec.md](spec.md) — feature requirements
- [research.md](research.md) — decisions and tradeoffs
- [data-model.md](data-model.md) — entities and transitions
- [contracts/signal-processing.md](contracts/signal-processing.md) — `/signals` and config contract
- [quickstart.md](quickstart.md) — verification workflow

---

## Agent Context Update

`.github/copilot-instructions.md` updated to reference this plan:
- `specs/005-signal-causal-propagation/plan.md`

(Repository does not include `.specify/scripts/bash/setup-plan.sh` or agent update script; equivalent manual update applied.)
