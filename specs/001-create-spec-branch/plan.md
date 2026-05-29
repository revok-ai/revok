# Implementation Plan: Revok MVP — Memory Signal Processor

**Branch**: `001-create-spec-branch` | **Date**: 2026-05-29 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/001-create-spec-branch/spec.md`

## Summary

Build Revok 0.1.0: a transparent Python async HTTP proxy that intercepts AI-agent memory writes, extracts named entities via regex, scores them with exponential decay, persists state to SQLite WAL, and forwards enriched payloads to the Mem0 memory adapter. Every pluggable component is backed by a Protocol interface defined before any implementation.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: aiohttp 3.x (async HTTP server + client), aiosqlite 0.20+ (async SQLite), NetworkX 3.x (causal graph, scaffolded in 0.1.0), PyYAML 6.x (config loader), pytest 8.x + pytest-asyncio (test suite)

**Storage**: SQLite WAL mode via aiosqlite; Python dict in-memory hot layer (stdlib only — no Redis, no external cache library)

**Testing**: pytest + pytest-asyncio; one test module per source module in `tests/`

**Target Platform**: Linux / macOS / Windows developer machine (single-process, local)

**Project Type**: Async HTTP proxy service / library (AGPL v3)

**Performance Goals**: Full end-to-end pipeline (receive → extract → score → persist → proxy forward) completes for a single signal in under 5 seconds on a 4-core / 8 GB developer machine (SC-005)

**Constraints**:
- No managed cloud-provider SDK imports (Azure, AWS, GCP, Confluent) — AGPL OSS boundary
- No Redis, no spaCy, no rapidfuzz in MVP
- All I/O must be async (aiohttp + aiosqlite); `requests` is forbidden
- No `print` statements — `logging` module only
- Every public function/method: type-annotated signature + docstring
- AGPL v3 license in every file

**Scale/Scope**: Single local process, v0.1.0 — no multi-tenancy, no distribution

## Constitution Check

*All gates evaluated against `.specify/memory/constitution.md` v1.0.0.*

| Gate | Requirement | Status | Notes |
|------|-------------|--------|-------|
| I. License | AGPL v3 on every file in `revok/` | ✅ PASS | License header required in every .py file |
| II. OSS Boundary | No azure/boto3/botocore/google.cloud/confluent_kafka imports | ✅ PASS | Dependencies list contains none of these |
| II. OSS Boundary | No enterprise features (multi-tenancy, SSO, RBAC, audit, SaaS dashboard, advanced scoring) | ✅ PASS | MVP scope explicitly excludes all enterprise features |
| III. Interface First | Protocol classes for SignalSource, MessageBus, StateStore, MemoryAdapter in `revok/interfaces.py` before any implementation | ✅ PASS | Interfaces module is Task 1 in implementation order |
| IV. Async First | aiohttp everywhere; no `requests` | ✅ PASS | aiohttp used for both server and client; requests not in dependencies |
| V. Simplicity | SQLite WAL (not Redis); NetworkX (not alternatives); Python re (not spaCy/rapidfuzz) | ✅ PASS | All alternatives forbidden by constitution are absent |

**Verdict**: All gates pass. No violations requiring justification. Proceed to Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/001-create-spec-branch/
├── spec.md         ✅ Created — feature specification
├── plan.md         ✅ This file — implementation plan
├── research.md     ✅ Phase 0 — resolved unknowns + decisions
├── data-model.md   ✅ Phase 1 — entity schemas + state transitions
├── quickstart.md   ✅ Phase 1 — getting-started guide
└── contracts/      ✅ Phase 1 — HTTP proxy API + Protocol interfaces
    ├── proxy-api.md
    └── interfaces.md
```

### Source Code (repository root)

```text
revok/
├── __init__.py
├── interfaces.py          # Protocol definitions: SignalSource, MessageBus, StateStore, MemoryAdapter
├── config.py              # YAML config loader + Config dataclass
├── models.py              # Signal, Entity, EntityRecord, EnrichedPayload dataclasses
├── entity_matcher.py      # Regex-based entity extractor
├── state_store.py         # SQLite WAL persistence + in-memory hot layer
├── scoring.py             # Exponential decay scoring engine
├── signal_queue.py        # Async signal queue + normalizer
├── proxy.py               # aiohttp HTTP proxy server + Mem0 adapter
└── metadata_writer.py     # Enriched metadata writer back to Mem0

tests/
├── conftest.py
├── test_config.py
├── test_entity_matcher.py
├── test_state_store.py
├── test_scoring.py
├── test_signal_queue.py
├── test_proxy.py
└── test_metadata_writer.py

config/
└── revok.example.yaml     # Annotated example configuration

README.md
pyproject.toml             # PEP 517/518 build system + dependencies
```

## Phase 0: Research

See [research.md](research.md) — all unknowns resolved, decisions documented.

## Phase 1: Design Artifacts

- [data-model.md](data-model.md) — entity schemas, relationships, state transitions, SQLite schema
- [contracts/proxy-api.md](contracts/proxy-api.md) — HTTP proxy endpoint contracts
- [contracts/interfaces.md](contracts/interfaces.md) — Protocol interface definitions
- [quickstart.md](quickstart.md) — Developer getting-started guide

## Implementation Order

> **Note**: This table reflects **module dependency order** (what must exist before what can compile/run). It does not match the user-story phase sequence in `tasks.md`, which reorders stories by technical dependency (e.g., Config / US3 is implemented in Phase 3 before US1 / Enrichment in Phase 4, because every module imports `Config`).

Components are built strictly bottom-up per the constitution's MVP scope:

| Step | Module | Depends On |
|------|--------|------------|
| 0 | `revok/interfaces.py` | (none — interfaces first) |
| 1 | `revok/models.py` | interfaces |
| 2 | `revok/config.py` | models |
| 3 | `revok/entity_matcher.py` | models, config |
| 4 | `revok/state_store.py` | models, config |
| 5 | `revok/scoring.py` | models, config, state_store |
| 6 | `revok/signal_queue.py` | models, config, entity_matcher, scoring |
| 7 | `revok/proxy.py` | all above + aiohttp |
| 8 | `revok/metadata_writer.py` | proxy, models |
| 9 | `tests/` | all modules |
| 10 | `README.md` + `config/revok.example.yaml` | all modules |
