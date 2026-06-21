# Implementation Plan: Zep Community Edition Memory Adapter

**Feature**: 001-zep-adapter  
**Branch**: feat/zep-adapter  
**Date**: 2026-06-08  
**Status**: Ready for implementation  
**Spec**: [spec.md](spec.md)  
**Data model**: [data-model.md](data-model.md)  
**Research**: [research.md](research.md)  
**Quickstart**: [quickstart.md](quickstart.md)

---

## Technical Context

| Item | Value |
|------|-------|
| Language | Python 3.11+ |
| Async runtime | asyncio (`asyncio_mode = "auto"` in pytest) |
| HTTP client/server | aiohttp 3.9 |
| Test framework | pytest + pytest-asyncio + aiohttp.test_utils |
| Linting | ruff (target py311, line-length 88) |
| Type checking | mypy (strict=false, ignore_missing_imports=true) |
| New runtime deps | None |
| Files created | `revok/adapters/__init__.py`, `revok/adapters/mem0.py`, `revok/adapters/zep.py`, `tests/test_zep_adapter.py`, `tests/test_zep_proxy.py` |
| Files modified | `revok/models.py`, `revok/metadata_writer.py`, `revok/config.py`, `revok/proxy.py`, `tests/test_proxy.py`, `config/revok.example.yaml` |

---

## Constitution Check

No project constitution file found at `.specify/memory/constitution.md`.
The following principles are inferred from the existing codebase conventions:

| Principle | Status |
|-----------|--------|
| Interface First — Protocol defined before implementation | ✅ `MemoryAdapter` Protocol in `interfaces.py` is unchanged; `ZepAdapter` implements it |
| No new runtime dependencies | ✅ Only stdlib + existing aiohttp |
| Frozen dataclasses for config | ✅ `ZepUpstreamConfig(frozen=True)` |
| Never raise on upstream HTTP errors | ✅ `ZepAdapter` returns 502 `MemoryAdapterResponse` on `ClientConnectorError` |
| Enrichment pipeline resilience | ✅ `enrich()` unchanged; Zep feeds through same pipeline |
| No regression on existing tests | ✅ Achieved via tail-field defaults on `Config` and re-exports in `adapters/__init__.py` |

---

## Dependency Graph

```
P001 (models.py: original_bytes)
  └─→ P002 (metadata_writer: populate original_bytes)
        └─→ P008 (ZepAdapter: write uses original_bytes)

P003 (config.py: ZepUpstreamConfig)
  └─→ P004 (config.py: Config tail fields)
        └─→ P005 (config.py: load_config Zep branch)
              └─→ P009 (proxy.py: Zep routing in build_app)

P006 (adapters/__init__.py)
  └─→ P007 (adapters/mem0.py: move Mem0Adapter)
        └─→ P008 (adapters/zep.py: ZepAdapter)
              └─→ P009 (proxy.py: import from adapters)
                    └─→ P010 (test_proxy.py: import fix)
                          └─→ P011 (test_zep_adapter.py)
                                └─→ P012 (test_zep_proxy.py)
                                      └─→ P013 (revok.example.yaml)
                                            └─→ P014–P016 (quality gates)
```

---

## Tasks

### Group A — Core model & config

---

#### P001 — Add `original_bytes` field to `EnrichedPayload`

**File**: `revok/models.py`  
**Spec**: FR-Z04, FR-Z08 (D001 in research.md)  
**Effort**: Trivial

Add `original_bytes: bytes` as the last field of `EnrichedPayload`:

```python
@dataclass
class EnrichedPayload:
    original_body: dict[str, object]
    entities: list[EntityRecord]
    revok_version: str
    processed_at: float
    original_bytes: bytes = field(default_factory=bytes)  # NEW
```

Use `dataclasses.field(default_factory=bytes)` so existing instantiations that omit
the field (tests, etc.) don't break. The empty-bytes default is safe — the Mem0 path
never reads this field.

**Acceptance**:
- `EnrichedPayload(original_body={}, entities=[], revok_version="x", processed_at=0.0)` instantiates without error
- `EnrichedPayload(..., original_bytes=b'{"messages":[]}')` stores bytes

---

#### P002 — Populate `original_bytes` in `metadata_writer.enrich()`

**File**: `revok/metadata_writer.py`  
**Spec**: FR-Z04 (byte-identical forwarding depends on this)  
**Effort**: Trivial

In `enrich()`, populate `original_bytes` from `signal.original_body` when constructing `EnrichedPayload`:

```python
return EnrichedPayload(
    original_body=original_body,
    entities=scored_records,
    revok_version=__version__,
    processed_at=now,
    original_bytes=signal.original_body,  # NEW
)
```

Applies to all three return sites in `enrich()` (success path, exception fallback,
and the empty-entities resilience path).

**Acceptance**:
- `EnrichedPayload.original_bytes == signal.original_body` (same bytes object)
- Existing `test_metadata_writer.py` passes unchanged

---

#### P003 — Add `ZepUpstreamConfig` dataclass to `config.py`

**File**: `revok/config.py`  
**Spec**: FR-Z09, FR-Z10  
**Effort**: Small

Insert immediately after `UpstreamConfig`:

```python
@dataclass(frozen=True)
class ZepUpstreamConfig:
    """Zep CE upstream target and write-detection settings.

    Attributes:
        zep_url: Validated HTTP/HTTPS base URL for Zep CE.
        write_methods: HTTP methods that trigger enrichment (e.g., ["POST"]).
        write_paths: Informational path list; active gate is the path anchor in proxy.
    """
    zep_url: str
    write_methods: list[str]
    write_paths: list[str]
```

**Acceptance**:
- `ZepUpstreamConfig(zep_url="http://localhost:8000", write_methods=["POST"], write_paths=["/api/v1/sessions/"])` instantiates without error
- `mypy` reports no errors on the new class

---

#### P004 — Add tail fields to `Config`

**File**: `revok/config.py`  
**Spec**: FR-Z09 (D002 in research.md)  
**Effort**: Small

Add two fields with defaults at the end of `Config`, after `logging`:

```python
@dataclass(frozen=True)
class Config:
    server: ServerConfig
    upstream: UpstreamConfig
    entity_matcher: EntityMatcherConfig
    scoring: ScoringConfig
    state_store: StateStoreConfig
    logging: LoggingConfig
    adapter_type: str = "mem0"              # NEW: "mem0" | "zep"
    zep: ZepUpstreamConfig | None = None    # NEW: populated when adapter_type="zep"
```

Update the module docstring to mention `adapter_type` and `zep`.

**Acceptance**:
- All existing `Config(server=..., upstream=..., ...)` constructions compile and run
  (no positional change; new fields have defaults)
- `Config(..., adapter_type="zep", zep=ZepUpstreamConfig(...))` works

---

#### P005 — Extend `load_config()` for Zep mode

**File**: `revok/config.py`  
**Spec**: FR-Z09, FR-Z10, CHK005, CHK006, CHK007  
**Effort**: Medium

**Logic to add** at the end of `load_config()`, after the `logging` section is parsed
and before the `return Config(...)`:

```python
# --- adapter_type (optional, defaults to "mem0") ---
adapter_type = str(data.get("adapter_type") or "mem0").lower()
if adapter_type not in ("mem0", "zep"):
    raise ConfigError(
        f"adapter_type must be 'mem0' or 'zep'; got '{adapter_type}'."
    )

zep_cfg: ZepUpstreamConfig | None = None
if adapter_type == "zep":
    zep_raw = data.get("zep")
    if not isinstance(zep_raw, dict):
        raise ConfigError(
            "A 'zep' section is required when adapter_type is 'zep'."
        )
    zep_url = zep_raw.get("zep_url")
    if not zep_url:
        raise ConfigError("zep.zep_url is required when adapter_type is 'zep'.")
    parsed_zep = urllib.parse.urlparse(str(zep_url))
    if parsed_zep.scheme not in ("http", "https") or not parsed_zep.netloc:
        raise ConfigError(
            f"zep.zep_url must be a valid HTTP/HTTPS URL; got '{zep_url}'."
        )
    zep_wm = zep_raw.get("write_methods") or []
    zep_wp = zep_raw.get("write_paths") or []
    if not isinstance(zep_wm, list) or not zep_wm:
        raise ConfigError("zep.write_methods must be a non-empty list.")
    if not isinstance(zep_wp, list) or not zep_wp:
        raise ConfigError("zep.write_paths must be a non-empty list.")
    zep_cfg = ZepUpstreamConfig(
        zep_url=str(zep_url).rstrip("/"),
        write_methods=[str(m).upper() for m in zep_wm],
        write_paths=[str(p) for p in zep_wp],
    )
```

For Zep mode, `upstream` is optional; if the `upstream` section is absent, build a
stub so `Config.upstream` is never `None`:

```python
# In the upstream-parsing block, wrap in a conditional:
if adapter_type == "mem0":
    # existing required upstream parsing (unchanged)
    ...
    upstream_cfg = UpstreamConfig(...)
else:
    # Zep mode: upstream section optional; build stub if absent
    up = data.get("upstream")
    if up is not None:
        # parse normally (operator may still provide it)
        ...
    else:
        upstream_cfg = UpstreamConfig(
            mem0_url="http://unused",
            write_methods=[],
            write_paths=[],
        )
```

Update the final `return Config(...)` to pass `adapter_type=adapter_type, zep=zep_cfg`.

**Acceptance**:
- `load_config()` with `adapter_type: zep` and valid `zep:` section returns `Config`
  with `adapter_type="zep"` and populated `zep`
- Missing `zep:` section raises `ConfigError("A 'zep' section is required...")`
- Missing `zep_url` raises `ConfigError("zep.zep_url is required...")`
- Invalid URL raises `ConfigError` mentioning `zep.zep_url`
- `adapter_type: mem0` (or absent) behaves identically to today
- Existing `test_config.py` passes unchanged

---

### Group B — Adapters package

---

#### P006 — Create `revok/adapters/__init__.py`

**File**: `revok/adapters/__init__.py` (new file — also creates package)  
**Spec**: CHK001, CHK002  
**Effort**: Trivial

```python
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Revok Contributors
"""Upstream memory-backend adapters (Mem0, Zep CE)."""
from revok.adapters.mem0 import Mem0Adapter
from revok.adapters.zep import ZepAdapter

__all__ = ["Mem0Adapter", "ZepAdapter"]
```

**Acceptance**:
- `from revok.adapters import Mem0Adapter, ZepAdapter` succeeds

---

#### P007 — Move `Mem0Adapter` from `proxy.py` to `revok/adapters/mem0.py`

**Files**: `revok/adapters/mem0.py` (new), `revok/proxy.py` (modified)  
**Spec**: CHK001, CHK019 (SC-6 regression gate)  
**Effort**: Small — cut-paste with import adjustments, no logic change

**`revok/adapters/mem0.py`**: Contains:
- License header (same as `proxy.py`)
- Module docstring referencing `Mem0Adapter`
- All imports `Mem0Adapter` needs: `json`, `logging`, `urllib.parse`, `aiohttp`,
  `revok.config.UpstreamConfig`, `revok.models.EnrichedPayload`, `MemoryAdapterResponse`, `Signal`
- The `_502_BODY`, `_502_HEADERS`, `_HOP_BY_HOP` module-level constants that `Mem0Adapter` uses
- The `Mem0Adapter` class verbatim (zero logic change)

**`revok/proxy.py`**: Remove `Mem0Adapter` class definition and its constants.
Add import: `from revok.adapters.mem0 import Mem0Adapter`.
Keep `_HOP_BY_HOP` in `proxy.py` if `build_app()` still uses it for response stripping
(it does — response headers are stripped in `_handle`).

> Note: `_502_BODY`, `_502_HEADERS`, `_HOP_BY_HOP` should remain in `proxy.py` for
> use by `build_app._handle()`. The copies in `adapters/mem0.py` are for `Mem0Adapter`
> only and can use the same values (either re-declare or import from a shared location).
> Simplest: re-declare them independently in each module — they are small literals.

**Acceptance**:
- `from revok.adapters.mem0 import Mem0Adapter` succeeds
- `from revok.proxy import Mem0Adapter` raises `ImportError` (no longer there)
- All existing `test_proxy.py` tests pass after import path update in P010

---

#### P008 — Implement `ZepAdapter` in `revok/adapters/zep.py`

**File**: `revok/adapters/zep.py` (new file)  
**Spec**: FR-Z01, FR-Z04, FR-Z04a, FR-Z07, FR-Z08, FR-Z11, FR-Z12, FR-Z13  
**Effort**: Medium

Full implementation:

```python
# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Revok Contributors
"""Zep Community Edition upstream adapter implementing the MemoryAdapter Protocol."""
from __future__ import annotations

import json
import logging
import re
import urllib.parse

import aiohttp

from revok.config import ZepUpstreamConfig
from revok.models import EnrichedPayload, MemoryAdapterResponse, Signal

logger = logging.getLogger(__name__)

_502_BODY: bytes = json.dumps(
    {"error": "upstream_unavailable", "detail": "Zep endpoint is not reachable"}
).encode()
_502_HEADERS: dict[str, str] = {"Content-Type": "application/json"}

_HOP_BY_HOP: frozenset[str] = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
})

# Matches /api/v1/sessions/{sessionId}/memory (no trailing slash, no extra segments)
_SESSION_RE = re.compile(r"^/api/v1/sessions/([^/]+)/memory$")


def _extract_session_id(path: str) -> str | None:
    """Extract sessionId from the Zep write path; returns None on no-match or empty."""
    clean = path.split("?")[0]  # strip query string before matching
    match = _SESSION_RE.match(clean)
    if match:
        value = match.group(1)
        return value if value else None
    return None


class ZepAdapter:
    """Upstream Zep CE HTTP adapter implementing the MemoryAdapter Protocol.

    Attributes:
        _config: Upstream Zep CE configuration.
        _session: Shared aiohttp client session (caller-managed lifetime).
        _closed: Whether close() has already been called.
    """

    def __init__(self, config: ZepUpstreamConfig, session: aiohttp.ClientSession) -> None:
        self._config = config
        self._session = session
        self._closed = False

    async def write(
        self,
        payload: EnrichedPayload,
        http_path: str = "/",
    ) -> MemoryAdapterResponse:
        """Forward original bytes byte-identically to Zep (FR-Z04, FR-Z08).

        Entity metadata from enrichment is stored in Revok only; the body forwarded
        to Zep is payload.original_bytes — unchanged from what the agent sent.
        """
        url = self._config.zep_url.rstrip("/") + http_path
        session_id = _extract_session_id(http_path)
        logger.debug(
            "Zep write intercepted: session_id=%s path=%s",
            session_id or "unknown",
            http_path,
        )
        body_bytes = payload.original_bytes
        try:
            async with self._session.post(
                url,
                data=body_bytes,
                headers={"Content-Type": "application/json"},
                allow_redirects=False,
            ) as resp:
                resp_body = await resp.read()
                return MemoryAdapterResponse(
                    status=resp.status,
                    body=resp_body,
                    headers=dict(resp.headers),
                    is_error=resp.status >= 400,
                )
        except aiohttp.ClientConnectorError:
            logger.error("Zep upstream unreachable at %s", url)
            return MemoryAdapterResponse(
                status=502,
                body=_502_BODY,
                headers=_502_HEADERS,
                is_error=True,
            )

    async def forward(self, signal: Signal) -> MemoryAdapterResponse:
        """Forward a raw signal to Zep unchanged (reads, pass-throughs)."""
        url = self._config.zep_url.rstrip("/") + signal.http_path
        headers = dict(signal.headers)
        parsed = urllib.parse.urlparse(self._config.zep_url)
        headers["Host"] = parsed.netloc
        body = signal.original_body if signal.original_body else None
        try:
            async with self._session.request(
                method=signal.http_method,
                url=url,
                data=body,
                headers=headers,
                allow_redirects=False,
            ) as resp:
                resp_body = await resp.read()
                return MemoryAdapterResponse(
                    status=resp.status,
                    body=resp_body,
                    headers=dict(resp.headers),
                    is_error=resp.status >= 400,
                )
        except aiohttp.ClientConnectorError:
            logger.error("Zep upstream unreachable at %s", url)
            return MemoryAdapterResponse(
                status=502,
                body=_502_BODY,
                headers=_502_HEADERS,
                is_error=True,
            )

    async def close(self) -> None:
        """Close the underlying HTTP session (idempotent)."""
        if not self._closed:
            await self._session.close()
            self._closed = True
```

**Acceptance**:
- `isinstance(ZepAdapter(...), MemoryAdapter)` is `True` (Protocol structural check)
- `write()` sends `payload.original_bytes` (not `to_upstream_dict()`) to upstream
- `forward()` sends raw signal body unchanged
- `close()` is idempotent (second call does not raise)
- `_extract_session_id("/api/v1/sessions/abc123/memory")` returns `"abc123"`
- `_extract_session_id("/api/v1/sessions//memory")` returns `None`
- `_extract_session_id("/api/v1/sessions/abc/memory/extra")` returns `None`
- `_extract_session_id("/api/v1/sessions/uuid-with-hyphens/memory")` returns `"uuid-with-hyphens"`

---

### Group C — Proxy integration

---

#### P009 — Update `build_app()` in `proxy.py` for Zep routing

**File**: `revok/proxy.py`  
**Spec**: FR-Z01, FR-Z04a, FR-Z09, FR-Z13  
**Effort**: Medium

**Import additions** at top of `proxy.py`:
```python
from revok.adapters.zep import ZepAdapter
from revok.adapters.zep import _extract_session_id as _zep_session_id
```
(Remove the inline `Mem0Adapter` class — now imported from `revok.adapters.mem0`.)

**In `build_app()`**, before the inner `_handle` closure:
```python
is_zep_mode = config.adapter_type == "zep"

# Zep mode: write_methods come from zep config; Mem0: from upstream config
if is_zep_mode:
    if config.zep is None:
        raise ValueError(
            "build_app: adapter_type='zep' but config.zep is None. "
            "Check load_config() output."
        )
    write_methods: frozenset[str] = frozenset(
        m.upper() for m in config.zep.write_methods
    )
else:
    write_methods = frozenset(
        m.upper() for m in config.upstream.write_methods
    )
```

**In `_handle()`**, replace the `signal = Signal(...)` construction and adapter
selection block:

```python
# FR-Z04a: Zep mode derives source_id from sessionId → X-Agent-ID → "unknown"
if is_zep_mode:
    session_id = _zep_session_id(request.path)
    source_id = session_id or request.headers.get("X-Agent-ID") or "unknown"
else:
    source_id = request.headers.get("X-Agent-ID", "unknown")

signal = Signal(
    raw_content=body_bytes.decode("utf-8", errors="replace"),
    source_id=source_id,
    timestamp=time.time(),
    http_method=request.method,
    http_path=http_path,
    original_body=body_bytes,
    headers=dict(request.headers),
)

async with aiohttp.ClientSession() as session:
    if is_zep_mode:
        adapter: Mem0Adapter | ZepAdapter = ZepAdapter(config.zep, session)
        # FR-Z01: prefix-and-suffix anchor write detection
        is_write = (
            signal.http_method.upper() in write_methods
            and request.path.startswith("/api/v1/sessions/")
            and request.path.endswith("/memory")
        )
    else:
        adapter = Mem0Adapter(config.upstream, session)
        is_write = signal.http_method.upper() in write_methods and any(
            request.path.startswith(p) for p in config.upstream.write_paths
        )

    if is_write:
        try:
            json.loads(body_bytes)
        except (json.JSONDecodeError, ValueError):
            logger.warning(
                "Write request body is not valid JSON; forwarding raw to upstream "
                "(path=%s, size=%d bytes)",
                http_path,
                len(body_bytes),
            )
            result = await adapter.forward(signal)
        else:
            if is_zep_mode:
                # FR-Z02: extract text only from messages[].content for raw_content
                try:
                    parsed_body = json.loads(body_bytes)
                    messages = parsed_body.get("messages") if isinstance(parsed_body, dict) else None
                    if isinstance(messages, list):
                        contents = [
                            msg["content"]
                            for msg in messages
                            if isinstance(msg, dict) and isinstance(msg.get("content"), str)
                        ]
                        if contents:
                            signal = Signal(
                                raw_content=" ".join(contents),
                                source_id=signal.source_id,
                                timestamp=signal.timestamp,
                                http_method=signal.http_method,
                                http_path=signal.http_path,
                                original_body=signal.original_body,
                                headers=signal.headers,
                            )
                except Exception:
                    pass  # resilience: use original raw_content on any error
            enriched = await enrich(signal, matcher, scorer, store)
            result = await adapter.write(enriched, http_path)
    else:
        result = await adapter.forward(signal)
```

**Acceptance**:
- `is_zep_mode=False` path is byte-for-byte identical to today's behavior
- POST to `/api/v1/sessions/abc/memory` is intercepted; entity scores updated
- POST to `/api/v1/sessions/abc/memory/extra` is NOT intercepted (suffix anchor)
- GET to `/api/v1/sessions/abc/memory` is forwarded as pass-through
- `signal.source_id` equals the `sessionId` for valid Zep write paths

---

### Group D — Tests

---

#### P010 — Update import paths in `tests/test_proxy.py`

**File**: `tests/test_proxy.py`  
**Spec**: SC-6 (no Mem0 regression)  
**Effort**: Trivial

Change one import line:
```python
# Before
from revok.proxy import Mem0Adapter, build_app

# After
from revok.adapters import Mem0Adapter
from revok.proxy import build_app
```

No other changes. All existing test logic is unchanged.

**Acceptance**:
- All existing `test_proxy.py` tests pass under `pytest tests/test_proxy.py`

---

#### P011 — Unit tests for `ZepAdapter` in `tests/test_zep_adapter.py`

**File**: `tests/test_zep_adapter.py` (new file)  
**Spec**: FR-Z04, FR-Z04a, FR-Z07, FR-Z08, FR-Z11, FR-Z13, Scenarios 3 & 4  
**Effort**: Medium

Tests to include (using `pytest-asyncio` + `unittest.mock.AsyncMock`):

| Test | FR | Asserts |
|------|----|---------|
| `test_write_sends_original_bytes` | FR-Z08 | `session.post.call_args.kwargs["data"] == payload.original_bytes` |
| `test_write_502_on_connector_error` | FR-Z11 | Returns `MemoryAdapterResponse(status=502, is_error=True)` |
| `test_write_emits_debug_log` | FR-Z13 | `caplog` captures DEBUG with sessionId |
| `test_forward_sends_raw_body` | FR-Z05 | `session.request` called with original body |
| `test_forward_rewrites_host_header` | FR-Z06 | Host header == Zep netloc |
| `test_forward_502_on_connector_error` | FR-Z11 | 502 MemoryAdapterResponse |
| `test_close_idempotent` | FR-Z07 | Second `close()` does not raise |
| `test_extract_session_id_valid` | FR-Z04a | Returns sessionId string |
| `test_extract_session_id_empty_segment` | FR-Z04a | Returns None for `//memory` |
| `test_extract_session_id_extra_suffix` | FR-Z40 | Returns None for `/memory/extra` |
| `test_extract_session_id_uuid_with_hyphens` | FR-Z04a | Returns full UUID string |
| `test_protocol_structural_check` | FR-Z07 | `isinstance(adapter, MemoryAdapter)` |

**Fixture pattern** (mirror `test_proxy.py` style):
```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import aiohttp
from revok.adapters.zep import ZepAdapter, _extract_session_id
from revok.config import ZepUpstreamConfig
from revok.models import EnrichedPayload, Signal
from revok.interfaces import MemoryAdapter

def _zep_config(url: str = "http://zep:8000") -> ZepUpstreamConfig:
    return ZepUpstreamConfig(
        zep_url=url,
        write_methods=["POST"],
        write_paths=["/api/v1/sessions/"],
    )

def _enriched_payload(original_bytes: bytes = b'{}') -> EnrichedPayload:
    return EnrichedPayload(
        original_body={},
        entities=[],
        revok_version="test",
        processed_at=0.0,
        original_bytes=original_bytes,
    )
```

---

#### P012 — Integration tests for Zep proxy in `tests/test_zep_proxy.py`

**File**: `tests/test_zep_proxy.py` (new file)  
**Spec**: FR-Z01, FR-Z02, FR-Z04, FR-Z05, FR-Z06, Scenarios 1–5  
**Effort**: Medium-large

Tests to include (using `aiohttp.test_utils.TestServer` + `TestClient`):

| Test | Scenario | Asserts |
|------|----------|---------|
| `test_zep_write_updates_entity_score` | S1 | `store.get("entity_key").score > 0` |
| `test_zep_write_body_byte_identical` | S1 (FR-Z04) | Body captured by mock Zep == original bytes |
| `test_zep_read_is_pass_through` | S2 | No entity record created; GET forwarded |
| `test_zep_non_json_write_forwarded_raw` | S3 | Non-JSON body forwarded unchanged; no exception |
| `test_zep_upstream_unavailable_returns_502` | S4 | Client receives 502 JSON error |
| `test_mem0_mode_unaffected_when_no_adapter_type` | S5 | Existing Mem0 behavior unchanged |
| `test_zep_write_extra_path_is_pass_through` | FR-Z01 | `/api/v1/sessions/x/memory/extra` not intercepted |
| `test_zep_source_id_from_session_id` | FR-Z04a | `signal.source_id` == sessionId |
| `test_zep_source_id_fallback_to_header` | FR-Z04a | Falls back to X-Agent-ID when no sessionId |
| `test_zep_messages_content_extraction` | FR-Z02 | Only `messages[].content` text drives entity match |
| `test_zep_missing_content_field_skipped` | CHK021 | Mixed messages; entities found only in content fields |
| `test_zep_empty_messages_array` | CHK009 | Empty messages → no entities; body still forwarded |
| `test_hop_by_hop_headers_stripped` | FR-Z06 | `Transfer-Encoding` absent in response to client |

**Config helper** (mirrors `_config_with_upstream` from `test_proxy.py`):
```python
def _zep_config(zep_url: str, tmp_path: Path) -> Config:
    return Config(
        server=ServerConfig(host="127.0.0.1", port=8080, startup_timeout_seconds=5.0),
        upstream=UpstreamConfig(mem0_url="http://unused", write_methods=[], write_paths=[]),
        entity_matcher=EntityMatcherConfig(
            patterns=[PatternConfig(name="product", regex=r"\bApex\b")]
        ),
        scoring=ScoringConfig(half_life_seconds=86400.0, signal_strength=0.3, score_cap=1.0),
        state_store=StateStoreConfig(
            sqlite_path=str(tmp_path / "zep_test.db"), hot_layer_max_entries=10
        ),
        logging=LoggingConfig(level="WARNING", format="%(levelname)s %(message)s"),
        adapter_type="zep",
        zep=ZepUpstreamConfig(
            zep_url=zep_url,
            write_methods=["POST"],
            write_paths=["/api/v1/sessions/"],
        ),
    )
```

---

### Group E — Documentation & quality gates

---

#### P013 — Update `config/revok.example.yaml` with Zep example

**File**: `config/revok.example.yaml`  
**Spec**: FR-Z09, FR-Z10  
**Effort**: Trivial

Append a commented Zep CE section after the existing Mem0 `upstream` block:

```yaml
# --- Zep Community Edition mode ---
# To use Zep CE instead of Mem0, replace the 'upstream' section with:
#
# adapter_type: zep
#
# zep:
#   zep_url: "http://localhost:8000"   # Base URL of your Zep CE instance
#   write_methods:
#     - "POST"
#   write_paths:
#     - "/api/v1/sessions/"
#
# The 'upstream' section is optional (can be omitted) in Zep mode.
```

---

#### P014 — `pytest` clean

**Command**: `pytest`  
**Scope**: Full test suite  
**Acceptance**: 0 failures, 0 errors. No deprecation warnings from new code.

---

#### P015 — `ruff check` clean

**Command**: `ruff check revok/ tests/`  
**Acceptance**: Exit code 0. No new lint violations in any modified or new file.

Key rules to watch:
- `PLC0415` — no conditional imports (deferred imports inside functions)
- `E501` — line length ≤ 88
- No unused imports in `adapters/__init__.py` (`__all__` keeps them)

---

#### P016 — `mypy` clean

**Command**: `mypy revok/ tests/`  
**Acceptance**: 0 errors (warnings acceptable per project config `strict=false`).

Known type annotation requirements:
- `ZepAdapter.__init__`, `write`, `forward`, `close` — all fully annotated
- `_extract_session_id(path: str) -> str | None`
- `Config.zep: ZepUpstreamConfig | None` — needs `from __future__ import annotations`
  or direct import of `ZepUpstreamConfig` in module scope (already satisfied since it's
  in the same file)
- `adapter: Mem0Adapter | ZepAdapter` union type in `proxy.py` — annotate explicitly

---

## Execution Order Summary

```
P001 → P002                           (models, then metadata_writer)
P003 → P004 → P005                    (config dataclass, Config, load_config)
P006 → P007 → P008                    (package, Mem0 move, ZepAdapter)
P001+P008 → P009                      (proxy: needs both model + adapters)
P009 → P010                           (test import fix after proxy stable)
P010 → P011 → P012                    (unit tests, then integration)
P012 → P013 → P014 → P015 → P016     (docs then quality gates)
```

Parallel opportunities:
- P001+P003 can proceed in parallel (different files, no dependency)
- P006+P007 can proceed immediately after P001/P003 are done (P008 depends on both)
