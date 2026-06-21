# Implementation Readiness Checklist: Zep Community Edition Memory Adapter

**Purpose**: Pre-review gate — validate that every requirement is unambiguous enough
to implement correctly and that a reviewer can verify correctness against the spec  
**Created**: 2026-06-08  
**Audience**: Author + PR reviewer  
**Depth**: Standard (pre-review gate)  
**Domain**: Python 3.11+, asyncio, aiohttp, pytest, ruff, mypy  
**Feature**: [spec.md](../spec.md)

---

## Requirement Completeness

- [ ] CHK001 - Is the scope of the `revok/adapters/` package refactor fully specified — does the spec state which files move, what `proxy.py` retains, and what the public import surface becomes after `Mem0Adapter` relocates to `revok/adapters/mem0.py`? [Completeness, Gap]
- [ ] CHK002 - Are requirements defined for `revok/adapters/__init__.py` — what, if anything, should be re-exported from the package root so callers in `proxy.py` require no path-depth changes? [Completeness, Gap]
- [ ] CHK003 - Is the `MemoryAdapter` Protocol in `interfaces.py` fully satisfied by `ZepAdapter.write()` given that Mem0's `write()` accepts `EnrichedPayload` but Zep's `write()` sends the original body? [Consistency, Spec §FR-Z07, FR-Z08]
- [ ] CHK004 - Are all fields of `ZepUpstreamConfig` (`zep_url`, `write_methods`, `write_paths`) specified with the same validation rules as `UpstreamConfig` (e.g., HTTP/HTTPS URL check, non-empty list constraints)? [Completeness, Spec §FR-Z10]
- [ ] CHK005 - Is the `adapter_type` discriminator field placement in `Config` specified — does it sit at the top level alongside `upstream`, or nested within a new `zep` block? [Completeness, Spec §FR-Z09, Gap]
- [ ] CHK006 - Is the `load_config()` branching behavior defined — when `adapter_type: zep`, does it parse a `zep` YAML section into `ZepUpstreamConfig`, and when absent/`mem0`, does it parse the existing `upstream` section unchanged? [Completeness, Spec §FR-Z09]
- [ ] CHK007 - Are requirements defined for the case where `adapter_type: zep` is set but no `zep_url` is provided — should `load_config()` raise `ConfigError` with a specific message? [Edge Case, Spec §FR-Z10, Gap]

---

## Requirement Clarity

- [ ] CHK008 - Is "byte-identical" in FR-Z04 precisely defined for the implementation context — does it mean the same `bytes` object is passed as the `data=` argument to `aiohttp`, or is content-equality (e.g., same decoded string re-encoded) acceptable? [Clarity, Spec §FR-Z04]
- [ ] CHK009 - Is the `messages[].content` extraction path in FR-Z02 unambiguous when `messages` is absent, empty, or contains objects without a `content` key — should Revok treat these as "no entities found" and proceed, or skip enrichment entirely? [Clarity, Spec §FR-Z02, Edge Case]
- [ ] CHK010 - Is the `sessionId` extraction from the URL path in FR-Z04a specified with enough precision — which regex or path-segment index extracts it from `/api/v1/sessions/{sessionId}/memory`, and what constitutes a parse failure? [Clarity, Spec §FR-Z04a]
- [ ] CHK011 - Is the DEBUG log format in FR-Z13 specified — what fields does the log record include (sessionId value, path, entity count, timestamp) and in what order? [Clarity, Spec §FR-Z13, Gap]
- [ ] CHK012 - Is the 502 JSON response body for Zep upstream failures specified — should it mirror Mem0's `{"error": "upstream_unavailable", "detail": "..."}` shape exactly, with "Zep" substituted for "Mem0" in the detail string, or use a different key/value? [Clarity, Spec §FR-Z11]

---

## Requirement Consistency

- [ ] CHK013 - Does the spec consistently apply the same `_HOP_BY_HOP` header stripping to Zep responses as to Mem0 responses, with no Zep-specific exceptions or additions? [Consistency, Spec §FR-Z06]
- [ ] CHK014 - Is the `source_id` fallback chain in FR-Z04a consistent with `Signal` construction in `build_app._handle()` — the Mem0 path uses `request.headers.get("X-Agent-ID", "unknown")`; is the Zep path required to use the same header name casing? [Consistency, Spec §FR-Z04a]
- [ ] CHK015 - Does the spec consistently treat the write-detection logic (method + path prefix match) identically between `Mem0Adapter` and `ZepAdapter` in `build_app()`, or does Zep require a path-segment-aware match (not just `startswith`) given the `{sessionId}` wildcard? [Consistency, Spec §FR-Z01, Gap]
- [ ] CHK016 - Is the `close()` idempotency contract on `ZepAdapter` consistent with `Mem0Adapter` — both must be safe to call multiple times and must not raise? [Consistency, Spec §FR-Z07]

---

## Acceptance Criteria Quality

- [ ] CHK017 - Is the acceptance criterion for "byte-identical body" in US-Z1 testable in a unit test — can a pytest fixture intercept the `aiohttp.ClientSession.post()` call and assert the `data=` argument equals the original `body_bytes`? [Measurability, Spec §US-Z1]
- [ ] CHK018 - Is the acceptance criterion for "no entity records created on reads" in Scenario 2 measurable — is there a specified assertion (e.g., `store.list_all()` returns empty, or `store.get()` returns `None`) rather than "no update occurs"? [Measurability, Spec §Scenario 2]
- [ ] CHK019 - Is the acceptance criterion for "no Mem0 regression" (Success Criterion 6) testable without modifications — do existing `test_proxy.py` tests run unchanged, or do import paths change due to the `revok/adapters/` refactor? [Measurability, Spec §SC-6, Gap]

---

## Scenario Coverage

- [ ] CHK020 - Is a scenario defined for a write to a path that matches the Zep write pattern structurally but with a different prefix (e.g., `/v2/api/sessions/{id}/memory`) — should Revok treat it as a write or a pass-through? [Coverage, Spec §FR-Z01]
- [ ] CHK021 - Is a scenario defined for a `messages` array that contains a mix of objects with and without `content` fields — should Revok skip missing-content objects silently? [Coverage, Spec §FR-Z02, Edge Case]
- [ ] CHK022 - Is a scenario defined for `sessionId` containing special characters (e.g., UUIDs with hyphens, URL-encoded values) — is extraction still unambiguous? [Coverage, Spec §FR-Z04a]
- [ ] CHK023 - Is a scenario defined for `adapter_type: zep` combined with a `zep_url` that uses HTTPS — are TLS connections handled by the existing `aiohttp.ClientSession` without additional config? [Coverage, Spec §FR-Z10]
- [ ] CHK024 - Is a scenario defined for concurrent write requests to different `sessionId` values — is there any shared mutable state in `ZepAdapter` that would cause a race condition? [Coverage, Async Safety, Gap]

---

## Edge Case Coverage

- [ ] CHK025 - Is the behavior defined when `messages` is present in the body but is `null` or not a list — should Revok skip entity extraction and forward the body unchanged? [Edge Case, Spec §FR-Z02]
- [ ] CHK026 - Is the behavior defined when `sessionId` in the path is an empty string (e.g., `/api/v1/sessions//memory`) — does the fallback to `X-Agent-ID` apply? [Edge Case, Spec §FR-Z04a]
- [ ] CHK027 - Is the behavior defined when `ZepAdapter.write()` is called but the upstream returns a 4xx error (e.g., 401 Unauthorized from Zep) — should `is_error=True` be set and the status forwarded verbatim? [Edge Case, Spec §FR-Z11]
- [ ] CHK028 - Is the behavior defined for `ZepAdapter.close()` called on an adapter whose `aiohttp.ClientSession` was never used — does it close without error? [Edge Case, Spec §FR-Z07]

---

## Type Annotation & Static Analysis Requirements

- [ ] CHK029 - Are type annotations required on all public methods of `ZepAdapter` (`__init__`, `write`, `forward`, `close`) matching the `MemoryAdapter` Protocol signatures exactly, so `mypy --strict` passes without `type: ignore` comments? [Clarity, Non-Functional]
- [ ] CHK030 - Is `ZepUpstreamConfig` required to be `frozen=True` like `UpstreamConfig`, and are its field types (`str`, `list[str]`) specified precisely enough for `mypy` to validate `load_config()` without casts? [Clarity, Spec §FR-Z10]
- [ ] CHK031 - Are the return types of the `sessionId` extraction helper fully specified — does it return `str | None` or `str` (with fallback already applied inside it)? [Clarity, Spec §FR-Z04a, Gap]

---

## Test Requirement Quality

- [ ] CHK032 - Are the mock/fixture boundaries for the Zep upstream server defined — should tests use `aiohttp.test_utils.TestServer` (as `test_proxy.py` does for Mem0) or a simpler `unittest.mock.AsyncMock` on `ClientSession`? [Coverage, Gap]
- [ ] CHK033 - Is a fixture defined or specified for constructing a `Config` with `adapter_type: zep` and a `ZepUpstreamConfig` analogous to the `_config_with_upstream()` helper in `test_proxy.py`? [Completeness, Gap]
- [ ] CHK034 - Is a test specified for the `messages[].content` concatenation used as `raw_content` in `Signal` — does the spec define what `raw_content` looks like when multiple messages are present (joined by space, newline, or concatenated)? [Clarity, Spec §FR-Z02, Gap]
- [ ] CHK035 - Are test cases specified for all three branches of the `source_id` fallback chain (sessionId present, sessionId absent + X-Agent-ID present, both absent)? [Coverage, Spec §FR-Z04a]
- [ ] CHK036 - Is a test specified that verifies `Mem0Adapter` import still works from its new location (`revok.adapters.mem0`) after the refactor, and that existing `test_proxy.py` import paths are updated consistently? [Completeness, Spec §SC-6, Gap]

---

## Dependencies & Assumptions

- [ ] CHK037 - Is Assumption 4 ("Zep CE returns standard HTTP status codes") validated against the actual Zep CE API — if Zep returns a non-standard body on auth failure (e.g., HTML error page), is there a requirement to handle or log it? [Assumption, Spec §Assumption 4]
- [ ] CHK038 - Is it documented that `ZepAdapter` shares the same `enrich()` pipeline function from `metadata_writer.py` as `Mem0Adapter`, and that `enrich()` requires no changes for Zep? [Dependency, Spec §Dependencies]

---

## Ambiguities & Conflicts

- [x] CHK039 - FR-Z07 states `ZepAdapter` satisfies the `MemoryAdapter` Protocol, but the Protocol's `write()` signature takes `EnrichedPayload` while FR-Z08 states the Zep adapter sends the original body. Is the Protocol definition in `interfaces.py` required to change, or will `ZepAdapter.write()` accept `EnrichedPayload` and extract the original body from it? [Conflict, Spec §FR-Z07 vs FR-Z08] → **Resolved**: `write()` accepts `EnrichedPayload`; Protocol unchanged. Adapter extracts `original_bytes` (bytes) and forwards it. (Spec FR-Z08 updated.)
- [x] CHK040 - FR-Z01 specifies path matching via `write_paths` config, but the Zep session path `/api/v1/sessions/{sessionId}/memory` contains a wildcard segment. Is a `startswith("/api/v1/sessions/")` prefix match sufficient (as the Mem0 path matching uses), or does the spec require an exact path-template match to avoid false positives on paths like `/api/v1/sessions/abc/memory/extra`? [Ambiguity, Spec §FR-Z01] → **Resolved**: Use prefix-and-suffix anchor — `path.startswith("/api/v1/sessions/") and path.endswith("/memory")`. (Spec FR-Z01 updated.)
