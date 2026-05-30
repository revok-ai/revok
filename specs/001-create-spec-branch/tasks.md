---
description: "Task list for Revok MVP — Memory Signal Processor"
---

# Tasks: Revok MVP — Memory Signal Processor

**Input**: Design documents from `/specs/001-create-spec-branch/`

**Prerequisites**: plan.md ✅, spec.md ✅, research.md ✅, data-model.md ✅, contracts/ ✅, quickstart.md ✅

**Tests**: Not requested in spec — test tasks are NOT included per Task Generation Rules.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization, build system, and directory skeleton required before any source module can be written.

- [X] T001 Create `pyproject.toml` with AGPL v3 metadata, Python 3.11 requirement, and dependencies (aiohttp, aiosqlite, networkx, pyyaml) plus `[dev]` extras (pytest, pytest-asyncio) in `pyproject.toml`
- [X] T002 Create package entry point `revok/__init__.py` with version `0.1.0` and module-level logger in `revok/__init__.py`
- [X] T003 [P] Create `revok/__main__.py` with CLI entry point that accepts `--config <path>` argument and calls the proxy startup coroutine in `revok/__main__.py`
- [X] T004 [P] Create `tests/conftest.py` with shared pytest fixtures (tmp_path SQLite DB, minimal Config object, mock aiohttp server) in `tests/conftest.py`
- [X] T005 [P] Create annotated example config `config/revok.example.yaml` matching the full YAML schema documented in `specs/001-create-spec-branch/research.md` in `config/revok.example.yaml`

**Checkpoint**: Project installs with `pip install -e ".[dev]"` and `python -m revok --help` executes without error.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Protocol interfaces and core data models that MUST be defined before any concrete implementation. Implements FR-002 (Interface First) and FR-015 (type hints + docstrings).

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [X] T006 Create all four Protocol interface classes (`SignalSource`, `MessageBus`, `StateStore`, `MemoryAdapter`) with full type annotations and docstrings exactly as specified in `specs/001-create-spec-branch/contracts/interfaces.md` in `revok/interfaces.py`
- [X] T007 [P] Create `Signal`, `Entity`, `EntityRecord`, `EnrichedPayload`, `MemoryAdapterResponse` dataclasses with all fields, constraints, and the `EnrichedPayload.to_upstream_dict()` method as specified in `specs/001-create-spec-branch/data-model.md` in `revok/models.py`
- [X] T008 [P] Create all Config dataclasses (`ServerConfig`, `UpstreamConfig`, `PatternConfig`, `EntityMatcherConfig`, `ScoringConfig`, `StateStoreConfig`, `LoggingConfig`, `Config`) as specified in `specs/001-create-spec-branch/data-model.md` in `revok/config.py` (data structures only — no loader logic yet)
- [X] T009 [P] Create `tests/test_models.py` with tests for `Signal` immutability (`frozen=True`), `Entity` normalization rule, `EntityRecord` field constraints, and `EnrichedPayload.to_upstream_dict()` wire format in `tests/test_models.py`

**Checkpoint**: `pytest tests/test_models.py` passes. All Protocol classes are importable from `revok.interfaces`. All dataclasses importable from `revok.models` and `revok.config`.

---

## Phase 3: User Story 3 — YAML Configuration (Priority: P3) 🏗️ Unblocks All Stories

**Goal**: Deliver a working `config.py` loader so all subsequent modules can be instantiated from a YAML file rather than hardcoded values. Although spec priority is P3, this is the first story to implement because every other story (P1, P2, P4, P5) depends on `Config` being loadable.

**Independent Test**: Start Revok with two different YAML configs (different ports, different decay half-lives), confirm each instance is configured according to its own file — demonstrating the system reads and applies config at startup (spec story 3 independent test).

- [X] T010 [US3] Implement `load_config(path: str) -> Config` in `revok/config.py`: read YAML, validate all required keys, validate `server.port` (1–65535), `scoring.half_life_seconds > 0`, `scoring.score_cap > 0`, `upstream.mem0_url` is valid HTTP/HTTPS URL, `entity_matcher.patterns` non-empty with compilable regexes; raise `ConfigError` with actionable message on any violation (FR-001, FR-017) in `revok/config.py`
- [X] T011 [P] [US3] Implement `ConfigError` exception class with a `field` attribute and human-readable message in `revok/config.py`
- [X] T012 [P] [US3] Wire `revok/__main__.py` to call `load_config(args.config)` at startup; configure Python `logging` from `Config.logging`; abort with `SystemExit(1)` and printed `ConfigError` message if config is invalid (FR-017) in `revok/__main__.py`
- [X] T013 [P] [US3] Create `tests/test_config.py` with tests for: valid config loads all fields correctly, missing required key raises `ConfigError`, malformed YAML raises `ConfigError`, `half_life_seconds=0` raises `ConfigError`, invalid regex pattern raises `ConfigError` in `tests/test_config.py`

**Checkpoint**: `pytest tests/test_config.py` passes. `python -m revok --config config/revok.example.yaml` loads config and logs startup without crash.

---

## Phase 4: User Story 1 — Signal Interception and Enrichment (Priority: P1) 🎯 MVP Core

**Goal**: Deliver the complete signal enrichment pipeline: entity extraction → scoring → state persistence → enriched payload. This is the core value proposition of Revok (spec US1).

**Independent Test**: Start Revok with a minimal YAML config, send a single simulated memory-write signal, confirm the downstream adapter receives the original content intact plus entity metadata with at least one extracted entity and its confidence score (spec story 1 independent test).

### Entity Matcher (FR-005)

- [X] T014 [P] [US1] Implement `EntityMatcher` class with `__init__(self, config: EntityMatcherConfig)` that pre-compiles all regex patterns, and `match(self, text: str) -> list[Entity]` that returns all non-overlapping entity matches with normalized keys in `revok/entity_matcher.py`
- [X] T015 [P] [US1] Create `tests/test_entity_matcher.py` with tests for: single pattern match, multiple pattern matches, no match returns empty list, overlapping spans returns longest match, case normalization of entity keys, pattern with zero matches in `tests/test_entity_matcher.py`

### Scoring Engine (FR-006)

- [X] T016 [P] [US1] Implement `ScoringEngine` class with `__init__(self, config: ScoringConfig)` and `score(self, existing: EntityRecord | None, now: float) -> float` using the exponential decay formula `score_decayed * exp(-λ * Δt) + signal_strength` capped at `score_cap`; `λ = ln(2) / half_life_seconds` as documented in `specs/001-create-spec-branch/research.md` in `revok/scoring.py`
- [X] T017 [P] [US1] Create `tests/test_scoring.py` with tests for: first signal creates score equal to `signal_strength`, second signal for same entity accumulates higher score, score after zero elapsed time equals previous score plus `signal_strength`, score decreases monotonically between signals (SC-007), score never exceeds `score_cap` in `tests/test_scoring.py`

### State Store (FR-007, FR-008)

- [X] T018 [US1] Implement `SqliteStateStore` class implementing `StateStore` Protocol in `revok/state_store.py`: `__init__` accepts `StateStoreConfig`; `open() -> None` creates SQLite WAL DB at `sqlite_path`, runs `CREATE TABLE IF NOT EXISTS entity_records` DDL as specified in `specs/001-create-spec-branch/data-model.md`; `get(entity_key) -> EntityRecord | None` checks LRU hot layer first then SQLite; `put(record) -> None` writes to SQLite then updates LRU hot layer; `close() -> None` flushes and closes connection in `revok/state_store.py`
- [X] T019 [P] [US1] Implement LRU hot layer inside `SqliteStateStore` using `collections.OrderedDict` with `move_to_end` on get and popitem(last=False) eviction when `len > hot_layer_max_entries` as documented in `specs/001-create-spec-branch/research.md` in `revok/state_store.py`
- [X] T020 [P] [US1] Create `tests/test_state_store.py` with tests for: `put` then `get` returns same record, `get` for non-existent key returns `None`, second `put` for same key overwrites, LRU evicts oldest entry when at max capacity, records survive store close and re-open (SC-008 persistence) in `tests/test_state_store.py`

### Signal Queue and Normalizer (FR-003, FR-004)

- [X] T021 [US1] Implement `AsyncioQueueBus` class implementing `MessageBus` Protocol backed by `asyncio.Queue`; implement `publish(signal)` and `consume() -> Signal` coroutines; implement `close()` that signals queue shutdown in `revok/signal_queue.py`
- [X] T022 [P] [US1] Create `tests/test_signal_queue.py` with tests for: published signal is consumed in FIFO order, consume awaits when queue is empty, close makes consume raise `asyncio.CancelledError` or return sentinel in `tests/test_signal_queue.py`

### Enrichment Pipeline (ties US1 together)

- [X] T023 [US1] Implement `enrich(signal: Signal, matcher: EntityMatcher, scorer: ScoringEngine, store: StateStore) -> EnrichedPayload` coroutine in `revok/metadata_writer.py`: for each entity from matcher, call `store.get`, call `scorer.score`, create updated `EntityRecord`, call `store.put`, assemble `EnrichedPayload`; on any exception log at ERROR and return `EnrichedPayload` with empty entities list (acceptance scenario 1.4) in `revok/metadata_writer.py`
- [X] T024 [P] [US1] Create `tests/test_metadata_writer.py` with tests for: signal with matching entity produces enriched payload with that entity's score, signal with no matches produces enriched payload with empty entities list, enrichment failure does not raise (resilience per scenario 1.4), second signal for same entity produces higher score than first in `tests/test_metadata_writer.py`

**Checkpoint**: `pytest tests/test_entity_matcher.py tests/test_scoring.py tests/test_state_store.py tests/test_signal_queue.py tests/test_metadata_writer.py` all pass.

---

## Phase 5: User Story 2 — Mem0 HTTP Proxy Integration (Priority: P2) 🔌

**Goal**: Deliver the aiohttp proxy server and Mem0 adapter so the full end-to-end round-trip (agent → Revok → Mem0) works. Implements the HTTP proxy contract from `specs/001-create-spec-branch/contracts/proxy-api.md`.

**Independent Test**: Configure Revok with a running Mem0 instance URL, issue a memory-write request through Revok's proxy, verify the write appears in Mem0 with enriched metadata fields present — complete round-trip (spec story 2 independent test).

### Mem0 Adapter (MemoryAdapter Protocol)

- [X] T025 [US2] Implement `Mem0Adapter` class in `revok/proxy.py` implementing the `MemoryAdapter` Protocol: `__init__(self, config: UpstreamConfig, session: aiohttp.ClientSession)`; `write(payload: EnrichedPayload) -> MemoryAdapterResponse` POSTs `payload.to_upstream_dict()` to `config.mem0_url + original_path`; `forward(signal: Signal) -> MemoryAdapterResponse` replays the raw request bytes to upstream unchanged; both methods return `MemoryAdapterResponse` without raising on upstream HTTP errors; 502 on connection refused (FR-009, FR-010, FR-011, proxy-api.md error semantics) in `revok/proxy.py`
- [X] T026 [P] [US2] Create `tests/test_proxy.py` with tests using `aiohttp.test_utils.TestServer` as a mock Mem0: write request produces enriched `x_revok` block in upstream payload, read request is forwarded unchanged, unreachable upstream returns `MemoryAdapterResponse` with `status=502`, `is_error=True` in `tests/test_proxy.py`

### aiohttp Proxy Server (AiohttpSignalSource)

- [X] T027 [US2] Implement `build_app(config: Config, store: StateStore, matcher: EntityMatcher, scorer: ScoringEngine) -> aiohttp.web.Application` in `revok/proxy.py`: create a catch-all route handler that inspects method + path against `config.upstream.write_methods` and `config.upstream.write_paths`; on write match: **(FR-004)** normalize the raw `aiohttp.web.Request` into a `Signal` dataclass (populate `raw_content`, `source_id`, `timestamp`, `http_method`, `http_path`, `original_body`, `headers`) before any other processing; parse JSON body, call `enrich()`, call `adapter.write()`; on non-write: call `adapter.forward()`; stream upstream response (status + headers + body) back to caller; rewrite `Host` header; return 502 JSON body on `aiohttp.ClientConnectorError` (contracts/proxy-api.md) in `revok/proxy.py`
- [X] T028 [US2] Wire `revok/__main__.py` to call `build_app(...)`, start `aiohttp.web.AppRunner`, bind to `config.server.host:config.server.port`; **(FR-016)** wrap the site startup in `asyncio.wait_for(..., timeout=config.server.startup_timeout_seconds)` and raise `SystemExit(1)` with a CRITICAL log if the timeout elapses before the socket is bound; log `"Revok listening on http://{host}:{port}"` at startup, handle `KeyboardInterrupt` for graceful shutdown in `revok/__main__.py`

**Checkpoint**: `pytest tests/test_proxy.py` passes. `python -m revok --config config/revok.example.yaml` starts and processes a test `curl` POST with enriched metadata visible in the forwarded request.

---

## Phase 6: User Story 4 — Entity State Visibility (Priority: P4) 🔍

**Goal**: Expose the state store for direct inspection so operators can verify entity scores and confirm exponential decay is working correctly.

**Independent Test**: Process several signals referencing a known entity, then query the state store directly and confirm entity records with timestamps and scores are present and reflect the decay model (spec story 4 independent test).

- [X] T029 [US4] Add `GET /v1/entities/{entity_key}` route to the aiohttp app in `revok/proxy.py`: call `store.get(entity_key)`, return 200 with JSON-serialised `EntityRecord` if found, 404 `{"error":"not_found"}` if `None` (acceptance scenarios 4.1 and 4.2) in `revok/proxy.py`
- [X] T030 [P] [US4] Add read-time decay computation to `store.get()` in `revok/state_store.py`: after fetching the raw `EntityRecord`, apply `score * exp(-λ * (now - last_seen))` using `ScoringEngine` before returning so the returned score always reflects current time (acceptance scenario 4.3) — note: persisted `score` is NOT updated on read-only access in `revok/state_store.py`

  > **Note**: `SqliteStateStore.get()` needs a reference to `ScoringEngine` to apply decay. Update `__init__` signature to accept optional `scorer: ScoringEngine | None = None` — if `None`, return raw score (used in tests that don't need decay).

- [X] T031 [P] [US4] Add `GET /v1/entities` route returning paginated JSON list of all `EntityRecord`s from the store (for operator inspection) in `revok/proxy.py`
- [X] T032 [P] [US4] Extend `tests/test_state_store.py` with decay-on-read test: store record at `t=0`, retrieve at `t=half_life`, confirm returned score equals approximately `initial_score / 2` in `tests/test_state_store.py`

**Checkpoint**: `curl http://127.0.0.1:8080/v1/entities/alice` returns entity record after signals referencing "Alice" have been processed.

---

## Phase 7: User Story 5 — Developer Onboarding and Test Suite (Priority: P5) 📖

**Goal**: Complete README, ensure all modules have tests, and validate the quickstart guide end-to-end.

**Independent Test**: Clone on a clean machine, follow the README, run `pytest`, all tests pass (spec story 5 independent test).

- [X] T033 Create `README.md` with: project description (AGPL v3), architecture diagram (from `specs/001-create-spec-branch/quickstart.md`), prerequisites, install steps (`pip install -e ".[dev]"`), usage (`python -m revok --config ...`), test suite (`pytest`), YAML config reference, and contributing section in `README.md`
- [X] T034 [P] [US5] Verify test coverage: confirm `tests/test_config.py`, `tests/test_models.py`, `tests/test_entity_matcher.py`, `tests/test_scoring.py`, `tests/test_state_store.py`, `tests/test_signal_queue.py`, `tests/test_proxy.py`, `tests/test_metadata_writer.py` all exist and each has at least one test that exercises the module's primary behavior (FR-013, SC-004); add any missing tests in `tests/`
- [X] T035 [P] [US5] Add AGPL v3 license header comment to all `.py` files in `revok/` and `tests/` that are missing it (Constitution § I) in `revok/` and `tests/`
- [X] T036 [P] [US5] Audit all public functions and methods across `revok/` — add any missing type annotations or docstrings (FR-015, acceptance scenario 5.3) in `revok/`
- [X] T037 [P] [US5] Add `NetworkX` causal graph scaffold: create `CausalGraph` class with `add_entity(entity_id: str, score: float) -> None` and `add_relation(source_id: str, target_id: str) -> None` backed by `nx.DiGraph`; call `add_entity` from `enrich()` in `metadata_writer.py` (constitution § V — scaffold only, no traversal) in `revok/causal_graph.py`

**Checkpoint**: `pytest` passes with zero failures across all test files. `README.md` exists and is complete.

---

## Phase 8: Polish & Cross-Cutting Concerns

**Purpose**: Final wiring, edge cases, and hardening across all modules.

- [ ] T038 Add `max_signal_size_bytes` validation to the proxy request handler: if request body exceeds configured limit, return 413 and log warning without forwarding (edge case: signal payload exceeds max size) in `revok/proxy.py`
- [ ] T039 [P] Add startup validation that `scoring.half_life_seconds > 0` is enforced at `ScoringEngine.__init__` time (not only at config-load time) — raises `ValueError` with descriptive message (edge case: decay half-life set to zero or negative) in `revok/scoring.py`
- [ ] T040 [P] Add graceful SQLite corruption handling in `SqliteStateStore.open()`: catch `sqlite3.DatabaseError`, log `CRITICAL`, and raise `RuntimeError` with actionable message so startup aborts cleanly (edge case: SQLite WAL file corrupted at startup) in `revok/state_store.py`
- [ ] T041 [P] Add `Content-Type: application/json` guard in proxy handler: if write-request body is not parseable as JSON, forward the raw body unchanged (do not enrich) and log a warning at `WARNING` level (edge case: Mem0 response contains unexpected/malformed JSON) in `revok/proxy.py`
- [ ] T042 Run `python -m revok --config config/revok.example.yaml` end-to-end and validate `quickstart.md` steps produce expected output within 5 seconds (SC-005) — update `quickstart.md` if any step is incorrect in `specs/001-create-spec-branch/quickstart.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)**: No dependencies — start immediately
- **Phase 2 (Foundational)**: Depends on Phase 1 — **BLOCKS all user stories**
- **Phase 3 (US3 — Config)**: Depends on Phase 2 — **BLOCKS Phase 4 and beyond** (every module reads `Config`)
- **Phase 4 (US1 — Enrichment)**: Depends on Phase 3 (needs `Config`) — this is the MVP core
- **Phase 5 (US2 — Proxy)**: Depends on Phase 4 (proxy calls `enrich()`)
- **Phase 6 (US4 — Visibility)**: Depends on Phase 5 (adds routes to the same aiohttp app)
- **Phase 7 (US5 — Onboarding)**: Depends on all phases complete (documents and audits final code)
- **Phase 8 (Polish)**: Depends on Phase 7

### User Story Dependencies

| Story | Spec Priority | Impl Phase | Depends On |
|-------|--------------|------------|------------|
| US3 — YAML Config | P3 | Phase 3 | Foundational |
| US1 — Enrichment | P1 | Phase 4 | US3 (Config) |
| US2 — Mem0 Proxy | P2 | Phase 5 | US1 (enrich) |
| US4 — Visibility | P4 | Phase 6 | US2 (app) |
| US5 — Onboarding | P5 | Phase 7 | All above |

> **Note**: US3 (Config) is implemented before US1 (Enrichment) despite spec priority P3 < P1 because `Config` is a hard dependency of every other module. The spec priority reflects business value; the implementation order reflects technical dependency.

### Within Each Phase

- `[P]` tasks have no inter-dependencies and can be worked in parallel
- Non-`[P]` tasks must complete their phase's `[P]` tasks first
- Checkpoint must be reached before next phase begins

---

## Parallel Example: Phase 4 (US1 — Enrichment)

Tasks T014–T024 can be parallelised as follows:

```
Start Phase 4
    ├── T014 EntityMatcher class     ─┐
    ├── T015 test_entity_matcher.py  ─┘ (parallel — different files)
    ├── T016 ScoringEngine class     ─┐
    ├── T017 test_scoring.py         ─┘ (parallel — different files)
    ├── T018 SqliteStateStore class  ─┐
    ├── T019 LRU hot layer          ─┘ (T019 extends T018 — sequential within store)
    ├── T020 test_state_store.py     (parallel with T018/T019)
    ├── T021 AsyncioQueueBus class   (parallel with above)
    └── T022 test_signal_queue.py    (parallel with T021)
        ↓  (all above complete)
    T023 enrich() pipeline          (sequential — depends on T014, T016, T018)
    T024 test_metadata_writer.py    (parallel with T023)
```

---

## Implementation Strategy

**MVP Scope** (minimum to demonstrate core value proposition):
- Phases 1–5 (T001–T028): A working enrichment pipeline with Mem0 proxy integration.
- After Phase 5, an AI agent developer can point their agent at Revok and see enriched memory writes in Mem0.

**Incremental delivery**:
- Phase 3 alone delivers: a startable Revok process that reads YAML config.
- Phase 4 alone delivers: the full enrichment pipeline (testable with mock upstream).
- Phase 5 completes: the end-to-end proxy round-trip with real Mem0.
- Phases 6–8 add: observability, developer experience, and hardening.
