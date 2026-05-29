# Data Model: Revok MVP

**Branch**: `001-create-spec-branch` | **Date**: 2026-05-29

---

## Entities

### Signal

The primary input to the Revok pipeline. A memory event submitted by an AI agent. Immutable once received (FR-004).

```python
@dataclass(frozen=True)
class Signal:
    raw_content: str          # Original text payload from the agent
    source_id: str            # Identifier of the originating agent or session
    timestamp: float          # Unix epoch seconds (float for sub-second precision)
    http_method: str          # Original HTTP method (e.g., "POST")
    http_path: str            # Original HTTP path (e.g., "/v1/memories")
    original_body: bytes      # Raw request body bytes (for pass-through on error)
    headers: dict[str, str]   # Original HTTP headers (for upstream forwarding)
```

**Constraints**:
- `raw_content` must be non-empty for write signals
- `timestamp` is set at receive time; not trusted from caller
- `frozen=True` enforces immutability

---

### Entity

A named concept extracted from signal content. Identified by a normalized key.

```python
@dataclass(frozen=True)
class Entity:
    key: str            # Normalized identifier (lowercased, stripped)
    raw_text: str       # Original matched text from signal
    pattern_name: str   # Name of the config pattern that matched (e.g., "person")
```

**Normalization rule**: `key = raw_text.lower().strip()`

**Constraints**:
- `key` must be non-empty after normalization
- Two entities with the same `key` are considered the same entity regardless of `raw_text` casing

---

### EntityRecord

The persisted state of a tracked entity in the SQLite store and hot layer.

```python
@dataclass
class EntityRecord:
    entity_key: str       # Normalized entity identifier (FK → Entity.key)
    score: float          # Current confidence score [0.0, score_cap]
    last_seen: float      # Unix epoch seconds of most recent signal
    signal_count: int     # Total number of signals that referenced this entity
    pattern_name: str     # Pattern category from most recent match
```

**Constraints**:
- `score` ∈ [0.0, `scoring.score_cap`] from config
- `signal_count` ≥ 1 for any stored record
- `last_seen` is always ≤ current time

**SQLite table schema** (`entity_records`):

```sql
CREATE TABLE IF NOT EXISTS entity_records (
    entity_key   TEXT    PRIMARY KEY,
    score        REAL    NOT NULL DEFAULT 0.0,
    last_seen    REAL    NOT NULL,
    signal_count INTEGER NOT NULL DEFAULT 1,
    pattern_name TEXT    NOT NULL DEFAULT ''
);
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
```

---

### EnrichedPayload

The original signal JSON body augmented with Revok's entity metadata block. Forwarded to the upstream Mem0 adapter.

```python
@dataclass
class EnrichedPayload:
    original_body: dict           # Parsed original JSON body from the signal
    entities: list[EntityRecord]  # All entity records scored from this signal
    revok_version: str            # e.g., "0.1.0"
    processed_at: float           # Unix epoch seconds of enrichment completion

    def to_upstream_dict(self) -> dict:
        """Merge original body with x_revok metadata block."""
```

**Wire format** (sent to Mem0):
```json
{
  "<original body fields>": "...",
  "x_revok": {
    "version": "0.1.0",
    "processed_at": "2026-05-29T12:00:00Z",
    "entities": [
      {
        "id": "alice",
        "score": 0.87,
        "signal_count": 3,
        "last_seen": "2026-05-29T11:59:00Z",
        "pattern_name": "person"
      }
    ]
  }
}
```

---

### Config

The runtime configuration tree loaded from YAML. All tuneable parameters across every component. Loaded once at startup; immutable thereafter.

```python
@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int                       # 1–65535
    startup_timeout_seconds: float

@dataclass(frozen=True)
class UpstreamConfig:
    mem0_url: str                   # Validated HTTP/HTTPS URL
    write_methods: list[str]        # e.g., ["POST", "PUT", "PATCH"]
    write_paths: list[str]          # e.g., ["/v1/memories"]

@dataclass(frozen=True)
class PatternConfig:
    name: str
    regex: str                      # Validated as compilable at startup

@dataclass(frozen=True)
class EntityMatcherConfig:
    patterns: list[PatternConfig]   # Non-empty

@dataclass(frozen=True)
class ScoringConfig:
    half_life_seconds: float        # > 0
    signal_strength: float          # > 0
    score_cap: float                # > 0

@dataclass(frozen=True)
class StateStoreConfig:
    sqlite_path: str
    hot_layer_max_entries: int      # > 0

@dataclass(frozen=True)
class LoggingConfig:
    level: str
    format: str

@dataclass(frozen=True)
class Config:
    server: ServerConfig
    upstream: UpstreamConfig
    entity_matcher: EntityMatcherConfig
    scoring: ScoringConfig
    state_store: StateStoreConfig
    logging: LoggingConfig
```

---

### MemoryAdapterResponse

The response returned by the downstream memory adapter (Mem0) after a proxied operation.

```python
@dataclass
class MemoryAdapterResponse:
    status: int           # HTTP status code from upstream
    body: bytes           # Raw response body bytes
    headers: dict[str, str]  # Response headers from upstream
    is_error: bool        # True if status >= 400
```

---

## Relationships

```
Signal ──(1:N)──> Entity               (one signal produces many entities via matching)
Entity ──(1:1)──> EntityRecord         (one entity has one persisted state record)
Signal + EntityRecord ──> EnrichedPayload   (scoring output combines signal + records)
Config ──(1:1)──> all components       (single config tree governs all)
```

## State Transitions

### EntityRecord lifecycle:

```
[Not Exists] ──(first signal match)──> [Created: score = signal_strength]
[Exists]     ──(subsequent match)   ──> [Updated: score = decayed + signal_strength]
[Exists]     ──(time passes, no match)──> [Implicitly decayed: score read-time only]
```

Note: Scores are not pre-computed on a timer. Decay is applied at **read time** (when a new signal arrives or the record is queried). The persisted `score` field represents the score at `last_seen` time, and the current score is computed as `score * exp(-λ * (now - last_seen))`.

## Hot Layer

The in-memory LRU cache mirrors `EntityRecord` for frequently accessed entities:

- Keyed by `entity_key` (string)
- Value: `EntityRecord` dataclass
- Max entries: `state_store.hot_layer_max_entries` (from config)
- Eviction: LRU via `collections.OrderedDict`
- Write-through: every SQLite write also updates the hot layer
- On startup: hot layer is empty (cold); warmed on first access
