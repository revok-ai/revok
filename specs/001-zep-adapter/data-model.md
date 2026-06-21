# Data Model: Zep Community Edition Adapter

**Phase**: 1 — Design  
**Feature**: 001-zep-adapter  
**Date**: 2026-06-08  
**Research**: [research.md](research.md)

---

## Modified Entities

### `EnrichedPayload` (`revok/models.py`)

**Change**: Add one field.

```
EnrichedPayload
├── original_body: dict[str, object]   (existing — Mem0 uses this)
├── entities: list[EntityRecord]       (existing)
├── revok_version: str                 (existing)
├── processed_at: float                (existing)
└── original_bytes: bytes              (NEW — Zep uses this for byte-identical forwarding)
```

**Constraints**:
- Must be populated from `signal.original_body` inside `metadata_writer.enrich()`
- `Mem0Adapter.write()` ignores this field (uses `to_upstream_dict()` as before)
- `ZepAdapter.write()` uses `payload.original_bytes` as `data=` for `aiohttp.post()`
- No change to `to_upstream_dict()` — Mem0 behavior unchanged

**Validation rules**: None beyond type (`bytes`). Empty bytes are valid (Zep forwards
them; enrichment was already guarded upstream).

---

### `Config` (`revok/config.py`)

**Change**: Add two tail fields with defaults; all existing fields unchanged.

```
Config
├── server: ServerConfig              (existing, required)
├── upstream: UpstreamConfig          (existing, required)
├── entity_matcher: EntityMatcherConfig (existing, required)
├── scoring: ScoringConfig            (existing, required)
├── state_store: StateStoreConfig     (existing, required)
├── logging: LoggingConfig            (existing, required)
├── adapter_type: str = "mem0"        (NEW — "mem0" | "zep")
└── zep: ZepUpstreamConfig | None = None  (NEW — populated only when adapter_type="zep")
```

**Constraints**:
- `adapter_type` defaults to `"mem0"` — zero change for existing configs
- `zep` defaults to `None` — zero change for existing code
- `build_app()` branches on `config.adapter_type`
- When `adapter_type = "zep"` and `config.zep` is `None`, `build_app()` should raise
  `ValueError` at startup (not silently use Mem0)

---

## New Entities

### `ZepUpstreamConfig` (`revok/config.py`)

```
ZepUpstreamConfig  [frozen=True dataclass]
├── zep_url: str           — Validated HTTP/HTTPS base URL for Zep CE
├── write_methods: list[str] — HTTP methods that trigger enrichment (e.g., ["POST"])
└── write_paths: list[str]   — Informational; anchor rule governs actual detection
```

**Validation rules** (enforced in `load_config()`):
- `zep_url`: HTTP/HTTPS URL with non-empty netloc (same check as `mem0_url`)
- `write_methods`: non-empty list
- `write_paths`: non-empty list
- `ConfigError` raised with message `"zep.zep_url is required when adapter_type is 'zep'"` when `adapter_type: zep` but `zep` section absent

---

### `ZepAdapter` (`revok/adapters/zep.py`)

```
ZepAdapter
├── _config: ZepUpstreamConfig
├── _session: aiohttp.ClientSession
└── _closed: bool

Methods:
├── write(payload: EnrichedPayload, http_path: str = "/") → MemoryAdapterResponse
│   └── Sends payload.original_bytes to Zep byte-identically
│   └── Emits DEBUG log: session_id, http_path
│   └── Returns 502 MemoryAdapterResponse on ClientConnectorError
├── forward(signal: Signal) → MemoryAdapterResponse
│   └── Forwards raw signal unchanged (all methods, all paths)
│   └── Rewrites Host header to Zep upstream netloc
│   └── Returns 502 MemoryAdapterResponse on ClientConnectorError
└── close() → None  [idempotent]
```

**Protocol conformance**: satisfies `MemoryAdapter` Protocol from `revok/interfaces.py`
(no Protocol change required).

**Session ID extraction helper** (module-level, not a class method):
```
_extract_session_id(path: str) -> str | None
  pattern: r"^/api/v1/sessions/([^/]+)/memory$"  (query string stripped before match)
  returns: sessionId string if match and non-empty; None otherwise
```

---

## State Transitions

No new state transitions. `ZepAdapter` is stateless beyond the `_closed` flag.
The existing entity scoring state machine (`EntityRecord` in `StateStore`) is
unchanged — Zep writes feed the same `enrich()` → `scorer.score_at()` → `store.put()`
pipeline as Mem0 writes.

---

## YAML Configuration Shape

### Mem0 mode (existing — no change)
```yaml
upstream:
  mem0_url: "http://localhost:8000"
  write_methods: ["POST"]
  write_paths: ["/v1/memories"]
```

### Zep mode (new)
```yaml
adapter_type: zep
zep:
  zep_url: "http://localhost:8000"
  write_methods: ["POST"]
  write_paths: ["/api/v1/sessions/"]   # stored; anchor rule is the active gate
```

When `adapter_type: zep`, the `upstream` section is **optional**. When absent,
`load_config()` constructs a stub `UpstreamConfig` so `Config.upstream` is never
`None` (backward-compat invariant).
