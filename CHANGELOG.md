# Changelog

All notable changes to Revok are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Revok uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.1.1] — 2026-06-06

### Fixed
- `GET /v1/entities/{key}` now returns the time-recovered confidence score via
  `decay_at()` instead of the frozen stored value from the last write.
- `POST /signals` endpoint added — dedicated signal ingestion path separate
  from the memory write path.
- README corrected — confidence retrieval requires an explicit
  `GET /v1/entities/{key}` call; it is not returned automatically in the
  memory search response.

### Changed
- `pyproject.toml` build backend corrected to `setuptools.build_meta`.
- `pyproject.toml` version bumped to `0.1.1`.
- `pyproject.toml` URLs updated with correct GitHub repository (`robertopc1/revok`).

---

## [0.1.0] — 2026-05-29

Initial MVP release. Builds the complete OSS core: transparent HTTP proxy,
entity extraction, exponential decay scoring, SQLite persistence, causal graph
scaffold, and the Mem0 memory adapter.

### Added

#### HTTP proxy (`revok/proxy.py`)
- `build_app()` — constructs an `aiohttp.web.Application` with a catch-all
  route handler that classifies incoming requests as writes or pass-throughs
  based on configured HTTP method + path pairs.
- Write path: normalises the raw `aiohttp` request into a `Signal`, runs the
  enrichment pipeline, and POSTs the enriched payload to the upstream Mem0
  instance with the `x_revok` metadata block injected.
- Read/pass-through path: forwards requests to upstream unchanged and streams
  the response back to the caller.
- `GET /v1/entities/{entity_key}` — returns the live-decayed `EntityRecord`
  for a named entity; 404 if not found.
- `GET /v1/entities` — paginated list of all entity records for operator
  inspection.
- 413 response when a request body exceeds `max_signal_size_bytes`.
- 502 JSON error body on upstream connection failure.
- Non-JSON write bodies are forwarded unchanged with a `WARNING` log rather
  than dropped.
- `Mem0Adapter` — implements the `MemoryAdapter` Protocol; handles upstream
  HTTP errors without raising.

#### Entity matcher (`revok/entity_matcher.py`)
- `EntityMatcher` — pre-compiles YAML-configured regex patterns at startup;
  `match(text)` returns all non-overlapping `Entity` objects with normalised
  lowercase keys.

#### Scoring engine (`revok/scoring.py`)
- `ScoringEngine` — exponential decay formula
  `score_decayed * exp(-ln(2) / half_life_seconds * Δt) + signal_strength`,
  capped at `score_cap`.
- First signal for an entity initialises the score to `signal_strength`.
- Raises `ValueError` at construction time if `half_life_seconds ≤ 0`.

#### State store (`revok/state_store.py`)
- `SqliteStateStore` — implements the `StateStore` Protocol backed by SQLite
  WAL mode via `aiosqlite`.
- In-memory LRU hot layer (`collections.OrderedDict`) with configurable
  `hot_layer_max_entries` cap to reduce database reads for frequently accessed
  entities.
- Read-time decay: `get()` applies the current decay formula before returning
  so callers always see a live score; the persisted value is not mutated.
- Graceful handling of `sqlite3.DatabaseError` at `open()` — logs `CRITICAL`
  and raises `RuntimeError` with an actionable message.

#### Signal queue (`revok/signal_queue.py`)
- `AsyncioQueueBus` — implements the `MessageBus` Protocol backed by
  `asyncio.Queue`; provides `publish()` and `consume()` coroutines and a
  `close()` method for graceful shutdown.

#### Enrichment pipeline (`revok/metadata_writer.py`)
- `enrich()` coroutine — orchestrates entity extraction → scoring → state
  persistence → `EnrichedPayload` assembly.
- On any per-entity exception: logs at `ERROR` and returns an empty entities
  list so upstream forwarding is never blocked by enrichment failures.
- Calls `CausalGraph.add_entity()` for each extracted entity (scaffold).

#### Causal graph scaffold (`revok/causal_graph.py`)
- `CausalGraph` — wraps `networkx.DiGraph`; exposes `add_entity()` and
  `add_relation()` for future BFS traversal of causal relationships between
  entities. No traversal logic in v0.1.0 — scaffold only.

#### Configuration (`revok/config.py`)
- `load_config(path)` — YAML loader with validation: port range, positive
  half-life, valid upstream URL, non-empty and compilable regex patterns.
- `ConfigError` — typed exception with a `field` attribute and human-readable
  message for every validation failure.
- Full config dataclass hierarchy: `Config`, `ServerConfig`, `UpstreamConfig`,
  `EntityMatcherConfig`, `PatternConfig`, `ScoringConfig`, `StateStoreConfig`,
  `LoggingConfig`.

#### Data models (`revok/models.py`)
- `Signal` — frozen dataclass; represents a normalised inbound request.
- `Entity` — extracted named entity with a normalised key.
- `EntityRecord` — persisted state for one entity: score, signal count,
  first-seen and last-seen timestamps.
- `EnrichedPayload` — assembles the upstream-ready dict via
  `to_upstream_dict()`, injecting `x_revok.entities` and `x_revok.proxy_version`
  into the original memory payload.
- `MemoryAdapterResponse` — typed response envelope from the upstream adapter.

#### Interfaces (`revok/interfaces.py`)
- `SignalSource`, `MessageBus`, `StateStore`, `MemoryAdapter` — four Protocol
  classes that define the pluggable extension points for future adapters and
  signal sources.

#### CLI entry point (`revok/__main__.py`)
- `python -m revok --config <path>` starts the proxy; configures logging from
  YAML; aborts with `SystemExit(1)` on config error or startup timeout.
- Startup socket binding wrapped in `asyncio.wait_for` with a configurable
  timeout (`server.startup_timeout_seconds`).
- `KeyboardInterrupt` triggers graceful shutdown.

#### Example demo (`examples/mem0_basic/`)
- Docker Compose stack: Qdrant → Mem0 (FastAPI wrapper) → Revok proxy.
- `demo.py` — three-step end-to-end scenario: write memory → observe decay
  over one half-life → fire invalidation signal; prints live scores at each
  step.
- Supports Azure OpenAI / Azure AI Foundry and plain OpenAI via environment
  variable auto-detection.
- `revok.yaml` — demo config with `half_life_seconds: 10` for visible decay.

#### Configuration example (`config/revok.example.yaml`)
- Annotated reference config covering all supported fields.

#### Test suite (`tests/`)
- 90 tests across 8 modules: `test_config`, `test_models`, `test_entity_matcher`,
  `test_scoring`, `test_state_store`, `test_signal_queue`, `test_proxy`,
  `test_metadata_writer`.
- All tests pass; `ruff` and `mypy` report zero errors.

### Dependencies

| Package | Version constraint | Purpose |
|---------|--------------------|---------|
| `aiohttp` | `>=3.9,<4` | Async HTTP server and client |
| `aiosqlite` | `>=0.20,<1` | Async SQLite for state persistence |
| `networkx` | `>=3.2,<4` | Causal graph (scaffold) |
| `pyyaml` | `>=6.0,<7` | YAML config loader |

---

[0.1.0]: https://github.com/robertopc1/revok/releases/tag/v0.1.0
