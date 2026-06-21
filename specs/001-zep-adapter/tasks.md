# Tasks: Zep Community Edition Memory Adapter

**Feature**: 001-zep-adapter  
**Branch**: `feat/zep-adapter`  
**Plan**: [plan.md](plan.md) | **Spec**: [spec.md](spec.md) | **Data model**: [data-model.md](data-model.md)  
**Total tasks**: 33  
**MVP scope**: Phase 1 (setup) + Phase 2 (foundational model & config) — enough to run existing tests clean

---

## Phase 1 — Setup

> Scaffolding: create the `revok/adapters/` package so all subsequent tasks have a landing place.

**Independent test criteria**: `from revok.adapters import Mem0Adapter` succeeds (after T002 completes).

- [X] T001 Create the `revok/adapters/` package directory and empty `__init__.py` stub in `revok/adapters/__init__.py`

---

## Phase 2 — Foundational (blocks all user-story phases)

> Core model change and `Mem0Adapter` relocation. These must land before any Zep-specific code because:
> - `ZepAdapter.write()` depends on `EnrichedPayload.original_bytes` (T003–T004)
> - `ZepAdapter` lives in the adapters package that `Mem0Adapter` must already inhabit (T005–T006)
> - `proxy.py` routing depends on all of the above (T007–T008)

**Independent test criteria**: All existing `tests/` pass after T008 with no changes other than the import fix in `test_proxy.py`.

- [X] T002 [P] Add `original_bytes: bytes` tail field with `field(default_factory=bytes)` to `EnrichedPayload` in `revok/models.py`
- [X] T003 [P] Add `ZepUpstreamConfig(frozen=True)` dataclass to `revok/config.py` immediately after `UpstreamConfig`; `write_paths: list[str]` field must have `field(default_factory=list)` so it is optional in YAML
- [X] T004 Populate `original_bytes=signal.original_body` at all three `EnrichedPayload(...)` construction sites in `revok/metadata_writer.py` — requires T002
- [X] T005 Add `adapter_type: str = "mem0"` and `zep: ZepUpstreamConfig | None = None` tail fields to `Config` in `revok/config.py` — requires T003
- [X] T006 Extend `load_config()` in `revok/config.py` to parse `adapter_type` and the `zep:` YAML section into `ZepUpstreamConfig`; raise `ConfigError` on missing `zep_url` or invalid URL; `write_paths` defaults to `[]` when absent (no non-empty validation); make `upstream` optional when `adapter_type: zep` — requires T005
- [X] T007 Move `Mem0Adapter` class verbatim from `revok/proxy.py` to new file `revok/adapters/mem0.py` with its own license header, module docstring, and the `_502_BODY`/`_502_HEADERS` constants it needs — requires T001
- [X] T008 [P] Update `revok/adapters/__init__.py` to re-export only `Mem0Adapter` via `__all__ = ["Mem0Adapter"]`; update `revok/proxy.py` to `from revok.adapters.mem0 import Mem0Adapter` (remove the inlined class); keep `_HOP_BY_HOP` in `proxy.py` — requires T007
- [X] T009 Fix import in `tests/test_proxy.py`: change `from revok.proxy import Mem0Adapter` to `from revok.adapters import Mem0Adapter` — requires T008

---

## Phase 3 — US-Z1: Transparent entity tracking for Zep agents

> Goal: a Zep-backed agent's memory writes are intercepted, entities are extracted and scored, and the original body is forwarded byte-identical to Zep.

**Story goal**: `POST /api/v1/sessions/{sessionId}/memory` through Revok updates an entity confidence score and forwards the unchanged bytes to Zep.

**Independent test criteria**:
- `store.get("entity_key").score > 0` after a write containing a known entity
- Body captured by mock Zep server `== original request bytes`
- `GET /api/v1/sessions/abc/memory` (read) creates no entity records
- `POST /api/v1/sessions/abc/memory/extra` (suffix mismatch) is not intercepted

- [X] T010 [US1] Implement `_extract_session_id(path: str) -> str | None` module-level helper in `revok/adapters/zep.py` using regex `r"^/api/v1/sessions/([^/]+)/memory$"` — requires T001
- [X] T011 [US1] Implement `ZepAdapter` class in `revok/adapters/zep.py` with `__init__`, `write()`, `forward()`, and `close()` matching the `MemoryAdapter` Protocol; `write()` sends `payload.original_bytes` byte-identical via `aiohttp`; `forward()` rewrites `Host` header; both return 502 `MemoryAdapterResponse` on `ClientConnectorError`; `close()` is idempotent — requires T010, T002
- [X] T012 [US1] Add `ZepAdapter` to `revok/adapters/__init__.py`: import `from revok.adapters.zep import ZepAdapter` and append to `__all__`; update `from revok.adapters import ZepAdapter` call sites in `proxy.py` — requires T011
- [X] T013 [US1] Update `build_app()` in `revok/proxy.py`: add `is_zep_mode` branch; in `_handle()`, derive `source_id` via `_extract_session_id` → `X-Agent-ID` → `"unknown"`; detect Zep writes using prefix-and-suffix anchor; extract `messages[].content` fields for `Signal.raw_content`; instantiate `ZepAdapter` in Zep mode — requires T008, T011, T006
- [X] T014 [P] [US1] Write unit tests in `tests/test_zep_adapter.py` for `_extract_session_id` (valid path, empty segment, extra suffix, UUID with hyphens) and `ZepAdapter` method signatures (Protocol structural check via `isinstance`) — requires T011
- [X] T015 [US1] Write integration test `test_zep_write_body_byte_identical` in `tests/test_zep_proxy.py` using `aiohttp.test_utils.TestServer` as mock Zep: assert body captured by server `== original request bytes` — requires T013
- [X] T016 [US1] Write integration test `test_zep_write_updates_entity_score` in `tests/test_zep_proxy.py`: POST a message with a pattern-matching entity; assert `store.get("entity_key").score > 0` — requires T013
- [X] T017 [US1] Write integration test `test_zep_read_is_pass_through` in `tests/test_zep_proxy.py`: GET to session memory path; assert no entity records created and request forwarded — requires T013
- [X] T017b [US1] Write integration test `test_zep_delete_and_patch_are_pass_through` in `tests/test_zep_proxy.py`: DELETE and PATCH to `/api/v1/sessions/abc/memory`; assert neither is intercepted (no entity update, each forwarded as-is) — requires T013
- [X] T018 [US1] Write integration test `test_zep_write_extra_suffix_is_pass_through` in `tests/test_zep_proxy.py`: POST to `/api/v1/sessions/abc/memory/extra`; assert not intercepted (no entity update, forwarded as-is) — requires T013

---

## Phase 4 — US-Z2: Zep upstream unavailability

> Goal: when Zep CE is unreachable, Revok returns a structured 502 without hanging.

**Story goal**: Any request (write or read) to a down Zep instance returns `{"error": "upstream_unavailable", ...}` with status 502.

**Independent test criteria**:
- Write to unreachable Zep → `status == 502`, body contains `"upstream_unavailable"`
- Read to unreachable Zep → `status == 502`, body contains `"upstream_unavailable"`
- No exception propagates to caller

- [X] T019 [P] [US2] Write unit test `test_write_502_on_connector_error` in `tests/test_zep_adapter.py`: mock `aiohttp.ClientSession` to raise `ClientConnectorError`; assert `MemoryAdapterResponse(status=502, is_error=True)` returned — requires T011
- [X] T020 [P] [US2] Write unit test `test_forward_502_on_connector_error` in `tests/test_zep_adapter.py`: same as T019 for `forward()` — requires T011
- [X] T021 [US2] Write integration test `test_zep_upstream_unavailable_returns_502` in `tests/test_zep_proxy.py`: configure Zep URL to a port with no listener; assert client receives 502 JSON error — requires T013

---

## Phase 5 — US-Z3: Adapter selection via configuration

> Goal: `adapter_type: zep` in YAML routes to `ZepAdapter`; absent/`mem0` preserves Mem0 behavior with zero regression.

**Story goal**: Config file drives adapter selection; no Mem0 test breaks.

**Independent test criteria**:
- `load_config()` with `adapter_type: zep` returns `Config.adapter_type == "zep"` and populated `Config.zep`
- Missing `zep:` section → `ConfigError`
- Missing `zep_url` → `ConfigError`
- Invalid `zep_url` → `ConfigError`
- No `adapter_type` → Mem0 default; all `test_proxy.py` tests pass

- [X] T022 [P] [US3] Write config unit tests in `tests/test_config.py` for Zep mode: valid config loads, missing `zep:` raises `ConfigError`, missing `zep_url` raises `ConfigError`, invalid URL raises `ConfigError` — requires T006
- [X] T023 [P] [US3] Write integration test `test_mem0_mode_unaffected_when_no_adapter_type` in `tests/test_zep_proxy.py`: existing Mem0 config with no `adapter_type` key routes through Mem0Adapter; existing `test_proxy.py` suite passes — requires T009

---

## Phase 6 — Edge cases and observability

> Cross-cutting correctness: non-JSON bodies, mixed messages arrays, source_id fallback chain, DEBUG logging, hop-by-hop stripping.

**Independent test criteria**: Each test case is independent; no new story blocked by this phase.

- [X] T024 [P] Write unit test `test_zep_write_emits_debug_log` in `tests/test_zep_adapter.py`: use `pytest`'s `caplog` fixture; assert DEBUG record contains `session_id` and `http_path` — requires T011
- [X] T025 [P] Write integration test `test_zep_non_json_write_forwarded_raw` in `tests/test_zep_proxy.py`: POST non-JSON body to session write path; assert body forwarded unchanged, no exception, no entity record created — requires T013
- [X] T026 [P] Write integration test `test_zep_messages_content_extraction` in `tests/test_zep_proxy.py`: body has two messages with `content`; assert entity found in content drives score update — requires T016
- [X] T027 [P] Write integration test `test_zep_missing_content_field_skipped` in `tests/test_zep_proxy.py`: body has mixed messages (some without `content`); assert only content-bearing messages drive entity extraction — requires T016
- [X] T028 [P] Write integration test `test_zep_empty_messages_array` in `tests/test_zep_proxy.py`: body has `messages: []`; assert no entities created, body forwarded unchanged — requires T013
- [X] T029 [P] Write integration tests for `source_id` fallback chain in `tests/test_zep_proxy.py`: (a) valid sessionId present → `source_id == sessionId`; (b) invalid path + X-Agent-ID header present → `source_id == header value`; (c) both absent → `source_id == "unknown"` — requires T013
- [X] T030 [P] Write integration test `test_hop_by_hop_headers_stripped` in `tests/test_zep_proxy.py`: mock Zep returns `Transfer-Encoding: chunked`; assert client response does not contain that header — requires T013

---

## Phase 7 — Polish & cross-cutting concerns

> Documentation update and quality gates.

- [X] T031 Append commented Zep CE example block to `config/revok.example.yaml` showing `adapter_type: zep` and `zep:` section
- [X] T032 [P] Run full quality gate: `pytest` (0 failures), `ruff check revok/ tests/` (exit 0), `mypy revok/ tests/` (0 errors) — requires T030, T031

---

## Dependency Graph

```
T001
 ├─→ T007 → T008 → T009
 └─→ T010 → T011 → T012 → T013
                → T014
                → T015, T016, T017, T017b, T018
                → T019, T020, T021
                → T024, T025, T026, T027, T028, T029, T030

T002 → T004
     → T011 (also deps T010)

T003 → T005 → T006
                → T008 (also deps T007)
                → T013 (also deps T011, T012)
                → T022

T009 → T023
T030 → T031 → T032
```

---

## Parallel Execution — Phase by Phase

| Phase | Tasks that can run in parallel |
|-------|-------------------------------|
| 2 | T002 ∥ T003 (different files, no dependency) |
| 2 | T007 ∥ T004 (after T002), T005 (after T003) |
| 3 | T014 ∥ T015 ∥ T016 ∥ T017 ∥ T017b ∥ T018 (each is an independent test file) |
| 4 | T019 ∥ T020 ∥ T021 |
| 5 | T022 ∥ T023 |
| 6 | T024 ∥ T025 ∥ T026 ∥ T027 ∥ T028 ∥ T029 ∥ T030 |
| 7 | T031 (can start once T013 done; no code dependency on T030) |

---

## Implementation Strategy

**MVP (just US-Z3 + US-Z1 skeleton)**: Complete T001–T009 (foundational refactor and config) to get existing tests green on the new package layout. Then T010–T018 to have a working Zep write path end-to-end.

**Incremental delivery order**:
1. T001–T009 → existing test suite passes with new import paths
2. T010–T018 → US-Z1 passes (entity tracking + byte-identical proxy)
3. T019–T021 → US-Z2 passes (502 on unavailability)
4. T022–T023 → US-Z3 passes (config-driven selection + regression gate)
5. T024–T030 → edge cases and observability
6. T031–T032 → polish and quality gates

---

## Task Count Summary

| Phase | Story | Tasks | Parallel |
|-------|-------|-------|---------|
| Phase 1 — Setup | — | 1 | 0 |
| Phase 2 — Foundational | — | 8 | 2 |
| Phase 3 — US-Z1 | [US1] | 10 | 7 |
| Phase 4 — US-Z2 | [US2] | 3 | 2 |
| Phase 5 — US-Z3 | [US3] | 2 | 2 |
| Phase 6 — Edge cases | — | 7 | 7 |
| Phase 7 — Polish | — | 2 | 1 |
| **Total** | | **33** | **21** |
