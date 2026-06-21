# Research: Zep Community Edition Adapter

**Phase**: 0 — Pre-Design Research  
**Feature**: 001-zep-adapter  
**Date**: 2026-06-08  
**Status**: Complete — no NEEDS CLARIFICATION markers remain

---

## Decision Log

### D001 — `EnrichedPayload.original_bytes` field (byte-identical forwarding)

**Problem**: `ZepAdapter.write()` must satisfy the `MemoryAdapter` Protocol
(`write(payload: EnrichedPayload) → MemoryAdapterResponse`) but must also forward the
request body **byte-identically** to Zep. `EnrichedPayload.original_body` is a
`dict[str, object]` (parsed JSON); re-serializing it via `json.dumps` is not
byte-identical (key order, spacing, encoding may differ).

**Decision**: Add `original_bytes: bytes` to `EnrichedPayload` in `revok/models.py`.
Populate it from `signal.original_body` inside `metadata_writer.enrich()`.
`ZepAdapter.write()` reads `payload.original_bytes` and passes it verbatim as the
`data=` argument to `aiohttp`.

**Rationale**: Minimal targeted change. Keeps Protocol unchanged. No new module
dependency. `Mem0Adapter.write()` ignores `original_bytes` (uses `to_upstream_dict()`
as before) — no Mem0 behavior change.

**Alternatives considered**:
- Re-serialize `original_body` dict — rejected: not byte-identical.
- Pass `Signal` alongside `EnrichedPayload` — rejected: changes Protocol signature.
- Add raw bytes to `Signal` only and branch in `build_app` — rejected: entity scoring
  still happens via `enrich()`, which returns `EnrichedPayload`.

---

### D002 — `Config` backward-compatibility strategy

**Problem**: `Config` is a `frozen=True` dataclass with all required fields. Adding
`adapter_type` and `zep: ZepUpstreamConfig | None` without breaking existing test
fixtures and `load_config()` calls.

**Decision**: Add two tail fields with defaults to `Config`:
```
adapter_type: str = "mem0"
zep: ZepUpstreamConfig | None = None
```
All existing required fields (`server`, `upstream`, etc.) are **unchanged and remain
required**. All existing keyword-argument constructions of `Config` continue to compile
and pass without modification. In Zep mode, `upstream` is still required in the YAML
but `load_config()` is free to construct a stub `UpstreamConfig` when `adapter_type:
zep` to avoid a breaking API change on the Python object.

**Rationale**: Zero test-fixture changes. Dataclass field ordering rule satisfied
(defaults at tail). `build_app()` branches on `config.adapter_type`.

**Alternatives considered**:
- Make `upstream: UpstreamConfig | None = None` — requires reordering all fields;
  downstream `config.upstream.write_methods` accesses need None-guards everywhere.
- Separate `ZepConfig` wrapper object — cleaner long-term but out of scope for this feature.

---

### D003 — `load_config()` Zep section YAML shape

**Decision**: Top-level YAML keys for Zep mode:
```yaml
adapter_type: zep          # discriminator
zep:
  zep_url: "http://..."
  write_methods: ["POST"]
  write_paths: ["/api/v1/sessions/"]   # informational; anchor overrides
```
`upstream` section is **optional** when `adapter_type: zep`; `load_config()` builds a
stub `UpstreamConfig(mem0_url="http://unused", write_methods=[], write_paths=[])` when
absent so `Config.upstream` is never `None`.  
When `adapter_type` is absent or `"mem0"`, `upstream` is required as today.

---

### D004 — Write detection in `build_app()` for Zep

**Decision**: Prefix-and-suffix anchor (resolved via CHK040):
```python
request.path.startswith("/api/v1/sessions/") and request.path.endswith("/memory")
```
The `write_paths` field in `ZepUpstreamConfig` is parsed and stored (available for
future extension) but is **not** used in write detection in this iteration. The anchor
rule is the sole gate.

---

### D005 — `source_id` derivation for Zep signals (FR-Z04a)

**Decision**: Resolved via CHK resolved session:
1. Extract segment between `/api/v1/sessions/` and `/memory` using `re.compile`.
2. Fallback to `X-Agent-ID` header.
3. Fallback to `"unknown"`.
Empty-string segment (e.g., `/api/v1/sessions//memory`) is treated as parse failure
→ falls through to `X-Agent-ID`.

Helper `_extract_session_id(path: str) -> str | None` lives in
`revok/adapters/zep.py`.

---

### D006 — `revok/adapters/` package public surface

**Decision**: `revok/adapters/__init__.py` re-exports both adapters:
```python
from revok.adapters.mem0 import Mem0Adapter
from revok.adapters.zep import ZepAdapter
__all__ = ["Mem0Adapter", "ZepAdapter"]
```
`proxy.py` and tests import from `revok.adapters` (short path). Existing
`from revok.proxy import Mem0Adapter` in tests is updated to
`from revok.adapters import Mem0Adapter`.

---

### D007 — Test fixture strategy (CHK032)

**Decision**: Use `aiohttp.test_utils.TestServer` (same as `test_proxy.py`) for
integration tests in `tests/test_zep_proxy.py`. Use `unittest.mock.AsyncMock` on
`aiohttp.ClientSession` for unit tests in `tests/test_zep_adapter.py` that exercise
`ZepAdapter` methods in isolation.

**Rationale**: `TestServer` gives a real HTTP server to verify byte-identical
forwarding (assert captured body == original bytes). `AsyncMock` is lighter for
unit-testing error paths (502, non-JSON) without running a server.

---

### D008 — `messages[].content` concatenation into `Signal.raw_content` (CHK034)

**Decision**: Join all `content` values with a single space:
```python
contents = [
    msg["content"]
    for msg in messages
    if isinstance(msg, dict) and isinstance(msg.get("content"), str)
]
signal_raw_content = " ".join(contents)
```
Non-string or absent `content` fields are skipped silently (satisfies CHK009/CHK021).
This happens inside `build_app._handle()` for Zep writes before constructing `Signal`;
`enrich()` receives a `Signal` whose `raw_content` is already the joined text.

---

### D009 — No external library additions

**Decision**: Zero new runtime dependencies. `ZepAdapter` uses only `aiohttp`,
`json`, `re`, `urllib.parse`, and `logging` — all already present.
