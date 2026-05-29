# Research: Revok MVP

**Branch**: `001-create-spec-branch` | **Date**: 2026-05-29

All NEEDS CLARIFICATION items resolved. All decisions documented with rationale.

---

## 1. Async SQLite Access Strategy

**Decision**: Use `aiosqlite` (async wrapper around stdlib `sqlite3`)

**Rationale**: FR-012 requires all I/O to be async coroutines. `sqlite3` is synchronous. Two valid options:
1. `aiosqlite` — thin async wrapper, well-maintained, aligns with `aiohttp` style.
2. `asyncio.run_in_executor` with stdlib `sqlite3` — works but requires manual thread-pool management and more boilerplate.

`aiosqlite` is preferred: it keeps all storage code in async/await style consistent with the rest of the codebase, has no non-OSS dependencies, and requires no custom executor configuration.

**WAL configuration**: On connection open, execute `PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;` to enable WAL mode with a good single-process durability/performance tradeoff.

**Alternatives considered**: Redis (rejected — forbidden in MVP by constitution); PostgreSQL (rejected — over-engineered for single-process local deployment).

---

## 2. Mem0 HTTP API Format

**Decision**: Treat Mem0 as a generic HTTP endpoint; proxy all requests transparently. Write enrichment uses a well-known metadata key `x-revok-entities`.

**Rationale**: The Mem0 self-hosted REST API accepts and returns JSON. Revok does not need to parse Mem0's internal schema to fulfill its contract — it only needs to:
1. Detect memory-write requests (HTTP POST / PUT to `/v1/memories` or similar paths, configurable).
2. Append an `x_revok` metadata block to the request JSON body before forwarding.
3. Pass all other requests through unmodified.

The configurable write-path matcher (from YAML config, e.g., `"POST /v1/memories"`) allows operators to adapt Revok to any Mem0 version or API evolution without source changes, satisfying FR-001.

**Write enrichment payload shape** (appended to original body):
```json
{
  "x_revok": {
    "version": "0.1.0",
    "entities": [
      {"id": "alice", "score": 0.87, "signal_count": 3, "last_seen": "2026-05-29T12:00:00Z"}
    ]
  }
}
```

**Alternatives considered**: Parsing Mem0's full API schema (rejected — tight coupling, fragile across Mem0 versions); modifying Mem0 itself (rejected — out of scope).

---

## 3. Exponential Decay Formula + State Update Algorithm

**Decision**: Use standard half-life exponential decay with additive signal reinforcement.

**Score update algorithm** on each new signal for entity `e`:

```
λ = ln(2) / half_life_seconds          # decay constant from config
Δt = now - e.last_seen_timestamp       # seconds since last signal
score_decayed = e.score * exp(-λ * Δt) # apply time decay
score_new = score_decayed + signal_strength  # add signal boost (configurable)
score_new = min(score_new, score_cap)   # cap at maximum (configurable, e.g. 1.0)
```

**For entities with no prior record**: `score_new = signal_strength` (configurable initial value).

**Parameters in config** (all configurable, no hardcoded values per FR-001):
- `scoring.half_life_seconds` (e.g., `86400` = 1 day)
- `scoring.signal_strength` (e.g., `0.3`)
- `scoring.score_cap` (e.g., `1.0`)

**Validation**: Config loader must reject `half_life_seconds <= 0` at startup (FR-017).

**Alternatives considered**: Linear decay (rejected — less realistic for memory systems); TF-IDF based scoring (rejected — over-engineered for MVP).

---

## 4. aiohttp Transparent HTTP Proxy Pattern

**Decision**: Use `aiohttp.web` for the inbound server and `aiohttp.ClientSession` for upstream forwarding.

**Proxy request flow**:
1. Receive request via `aiohttp.web.Request`.
2. Inspect method + path against configured write-path patterns.
3. If write path: read body, parse JSON, run enrichment pipeline, reconstruct body with `x_revok` block.
4. Forward to upstream (Mem0) URL via `ClientSession.request(method, upstream_url, headers=..., data=body)`.
5. Stream upstream response back to caller (status code + body + headers).

**Header handling**: Copy all request headers to upstream except `Host` (rewrite to upstream host). Copy all response headers back to caller.

**Error handling**: If upstream is unreachable → return 502 to caller, log error. Do NOT drop the signal silently (FR-004 + acceptance scenario 1.4).

**Alternatives considered**: `aiohttp.TCPConnector` with SSL passthrough (rejected — Mem0 in MVP is assumed HTTP-only locally); HTTPX (rejected — not in constitution's tech stack).

---

## 5. YAML Config Schema Design

**Decision**: Flat YAML with top-level sections per component.

```yaml
server:
  host: "127.0.0.1"
  port: 8080
  startup_timeout_seconds: 10

upstream:
  mem0_url: "http://localhost:8000"
  write_methods: ["POST", "PUT", "PATCH"]
  write_paths: ["/v1/memories"]

entity_matcher:
  patterns:
    - name: "person"
      regex: "\\b[A-Z][a-z]+ [A-Z][a-z]+\\b"
    - name: "email"
      regex: "[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\\.[a-zA-Z0-9-.]+"

scoring:
  half_life_seconds: 86400
  signal_strength: 0.3
  score_cap: 1.0

state_store:
  sqlite_path: "./revok_state.db"
  hot_layer_max_entries: 1000

logging:
  level: "INFO"
  format: "%(asctime)s %(name)s %(levelname)s %(message)s"
```

**Loader validation rules** (raises `ConfigError` on violation, per FR-017):
- All required keys must be present.
- `server.port` must be 1–65535.
- `scoring.half_life_seconds` must be > 0.
- `scoring.score_cap` must be > 0.
- `upstream.mem0_url` must be a valid HTTP/HTTPS URL.
- `entity_matcher.patterns` must be a non-empty list with valid regex strings.

**Alternatives considered**: TOML (rejected — PyYAML already chosen); `.env` files (rejected — insufficient structure for nested config); Pydantic settings (rejected — adds a dependency not in constitution's stack).

---

## 6. NetworkX Causal Graph (MVP Scope)

**Decision**: Scaffold the causal graph integration — include NetworkX as a dependency, create a `CausalGraph` class backed by `nx.DiGraph`, but limit 0.1.0 to graph construction (add node/edge on entity extraction). Graph traversal and influence scoring are deferred post-0.1.0.

**Rationale**: The constitution lists NetworkX in the tech stack and says "no alternatives". Including it ensures future graph features can be added without architectural disruption. Scaffolding only (not full implementation) aligns with the constitution's "MVP Only" principle.

**Interface**: `CausalGraph.add_entity(entity_id, score)` and `CausalGraph.add_relation(source_id, target_id)` are the only 0.1.0 public methods.

---

## 7. In-Memory Hot Layer Eviction Policy

**Decision**: LRU eviction using `collections.OrderedDict` (stdlib only, no external cache library).

**Rationale**: Simplest correct eviction strategy. `OrderedDict.move_to_end` on access + pop the oldest entry when `len > hot_layer_max_entries`. No external dependency needed. Aligns with "Simplicity — MVP Only" constitution principle.
