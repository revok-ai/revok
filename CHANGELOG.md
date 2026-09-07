# Changelog

All notable changes to Revok are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Revok uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.5.0] — 2026-09-06

### Added

#### Resolver protocol for exogenous signals
- `Resolver` protocol — maps free-text external signals to causal graph nodes.
  No concrete resolver ships with Revok; the protocol is
  implementation-agnostic and an implementation is injected by the host
  application.
- `POST /signals` accepts a `signal_text` field, resolved to target nodes at
  ingestion. Explicit `entity_refs` continue to take precedence and bypass the
  resolver entirely, so existing callers are unaffected. Resolver invocation is
  bounded by a configurable `causal_graph.resolver_timeout_seconds` (default
  `30.0`), sized for LLM-backed resolvers rather than a fixed low ceiling.
  A resolver failure — including a timeout — returns `502 resolver_failed`
  with an `error_detail` naming the exception type and message, so a timeout,
  an internal resolver exception, and a zero-match result are distinguishable
  by the caller; the same detail is persisted on the failed resolution trace.
- Root pressure is scaled by resolution confidence —
  `pressure_for_severity(severity) * target.confidence`. Scaling applies at the
  root only; downstream attenuation is unchanged. Explicit `entity_refs` are
  treated as confidence `1.0`.
- Durable resolution traces recording signal text, resolved targets with
  confidences and rationales, resulting invalidation events, and the full
  propagation path — per-node BFS depth, edge weight, and effective pressure —
  plus a per-traversal termination reason (`completed`, `min_pressure`, or
  `max_hops`). Without the termination reason, "reached the target", "stopped
  early due to attenuation", and "target was never reachable" are
  indistinguishable.
  - `GET /v1/resolver/traces` — recent traces, paginated
  - `GET /v1/resolver/traces/{signal_id}` — single trace
- Runtime causal relationship registration via `POST /v1/graph/relationships`,
  an idempotent upsert: re-posting a source→target pair with a different weight
  updates it (last write wins) and returns 200 rather than a conflict.

#### SignalSource protocol with durable ingest
- `SignalSource` protocol as the single ingestion abstraction (`receive`,
  `ack`, `close`). HTTP is now one implementation of it — request shapes,
  status codes, and resolver behaviour are unchanged, so there is no
  user-visible difference.
- Durable Redis Streams source (`revok/redis_source.py`) behind the optional
  `redis` extra (`pip install revok[redis]`). Consumer groups provide
  at-least-once delivery: entries remain pending until acknowledged, so signals
  published while Revok is down are processed on start, and entries left
  unacknowledged by a crash are redelivered.
- Idempotent processing keyed by `signal_id`, stored independently of trace
  storage. Trace recording is gated by `inspector.signal_history_enabled`;
  keeping deduplication separate means disabling tracing cannot silently
  disable idempotency.
- Bounded retries with queryable dead-letter records. A repeatedly failing
  signal is set aside after a configurable number of attempts (default 3) and
  the stream advances, so one bad signal cannot block every later one. The
  record retains payload, attempt count, and failure reason.
  - `GET /v1/signals/dead-letters` — recent dead letters, paginated
  - `GET /v1/signals/dead-letters/{signal_id}` — single record
- Per-entity locking with sorted acquisition over the union of root and
  propagation-set keys. Same-entity signals apply in arrival order regardless
  of which source delivered them, while unrelated entities continue to process
  concurrently. Sorted acquisition keeps overlapping key sets deadlock-free.
- `causal_graph.processing_timeout_seconds` — the outer bound on processing one
  signal, including resolver invocation — defaults to `60.0`, giving an
  LLM-backed resolver headroom to complete within it. `build_app` validates at
  startup that it exceeds `resolver_timeout_seconds` whenever a resolver is
  configured, raising a `ConfigError` rather than allowing a combination that
  guarantees resolution aborts.
- New config sections: `sources` (`http`, `redis_streams`) and `ingestion`
  (`dedupe_max_rows`, default `100000`; `max_concurrent_signals`, default `16`).

### Changed
- `SignalProcessor.process_one` returns a `ProcessingOutcome`
  (`applied` / `duplicate` / `failed`) so a source knows whether to acknowledge.
  It still logs and swallows per-entity failures rather than raising, leaving
  existing callers unaffected.
- `GraphBackend` gained `propagate_detailed`, returning traversal metadata and a
  termination reason. The existing `propagate` mapping contract is unchanged,
  and both NetworkX and FalkorDB Lite implement the new method with identical
  observable semantics.
- `interfaces.py` gained the `Resolver`, `ResolverTraceStore`, `DedupeStore`,
  and `DeadLetterStore` protocols; the previously unused `SignalSource`
  protocol gained `ack` and is now implemented.
- `Resolver` and `SignalSource` are now exported from the package root
  (`from revok import Resolver, SignalSource`) alongside `GraphBackend`. Both
  are primary integration points — a host implements `Resolver` to use
  free-text signal resolution and `SignalSource` for custom ingest. The
  remaining protocols stay under `revok.interfaces`.

### Notes
- `pip install revok` remains dependency-free. Redis Streams ingest is an
  optional extra mirroring the existing `falkordb-lite` pattern — importing the
  module without the extra installed is safe, and the resulting error names both
  the extra and the install command.

---

## [0.4.0] — 2026-08-25

### Added

#### Pluggable graph backends
- `FalkorDBLite` graph backend (`revok/falkordb_backend.py`) — an optional
  embedded alternative to the default NetworkX backend, implemented against the
  `GraphBackend` protocol. Installed via the `falkordb-lite` extra
  (`pip install revok[falkordb-lite]`); the default install is unchanged and
  dependency-free.
- New `causal_graph` config keys: `graph_backend` (`"networkx"` | `"falkordb-lite"`,
  default `"networkx"`) and `graph_backend_db_path`. Invalid backend names are
  rejected at config load with a typed `ConfigError`.
- Backend-agnostic contract tests (`tests/contract/`) that both backends must
  satisfy, plus a path-filtered `falkordb-conformance` CI job that runs the
  FalkorDB Lite suite against a containerised instance.

#### Inspector read API
- `RevokInspector` (`revok/inspector.py`) and five read-only endpoints for
  auditing why a belief holds its current confidence:
  - `GET /v1/inspector/entities/{entity_key}` — inspection report
  - `GET /v1/inspector/entities/{entity_key}/downstream` — causal dependents
  - `GET /v1/inspector/entities/{entity_key}/paths` — propagation paths
  - `GET /v1/inspector/entities/{entity_key}/signals` — signal history
  - `GET /v1/inspector/graph` — full causal graph topology snapshot
- `GET /inspector` — self-hosted viewer page with a Cytoscape.js graph renderer,
  signal timeline, and entity table. Assets are vendored under
  `revok/inspector/vendor/`; no CDN or network fetch at runtime.
- SQLite-backed signal history (`revok/signal_history.py`) with automatic
  row-count trimming per entity.
- New `inspector` config section: `enabled`, `signal_history_enabled`,
  `signal_history_max_rows` (default `10000`), `max_paths` (default `100`).

#### Contributor infrastructure
- CLA workflow and `CLA.md` — contributor licence agreements are collected
  automatically on pull requests, enabling the dual AGPL v3 / commercial model.

### Changed
- Callers now route through the `GraphBackend` interface rather than the
  concrete `CausalGraph`, decoupling propagation from any single graph library.
- `interfaces.py` gained four protocols: `GraphReader`, `GraphBackend`,
  `SignalHistoryStore`, and `Inspector`.
- Subscription demo hardened for public deployment — optional basic-auth
  middleware, concurrent memory-write and signal dispatch so a slow write can
  no longer delay causal propagation, baked-in config, and UI gating fixes.
- Demos standardised on `gpt-5-mini` (`gpt-4` deprecated) and on consistent
  compose/Dockerfile naming.

### Fixed
- `ruff` is now pinned to an exact version with an explicit
  `[tool.ruff.lint] select` list. Previously the range `>=0.4,<1` with no
  `select` meant the project inherited ruff's implicit defaults; ruff 0.16
  widened those defaults and turned a clean tree into 41 CI errors on a fresh
  install while pinned local installs still passed.
- `examples/subscription_demo/dashboard/lib/` is no longer swallowed by the
  Python-packaging `lib/` ignore rule, which had left `api.ts` and `utils.ts`
  untracked and broke the dashboard build from a clean clone.
- `tests/__init__.py` added so `tests` is a real package — `tests.contract`
  imports resolved locally by accident but failed in CI's clean environment.

---

## [0.3.0] — 2026-06-21

### Added
- Signal-driven causal propagation worker (`revok/signal_processor.py`) with async consumer loop, bounded processing timeout, and error-isolation semantics.
- `GraphBackend` protocol and concrete weighted propagation in `revok/causal_graph.py`.
- Bounded BFS causal propagation with cycle safety, max-pressure aggregation, hop-limit cutoff, and min-pressure pruning.
- New config sections for causal propagation and severity mapping:
  - `causal_graph` (`enabled`, `max_hops`, `min_pressure`, `attenuation`, `processing_timeout_seconds`, `relationships`)
  - `scoring.signal_pressure` (`severity_weights`, `default_severity`)
- Proxy lifecycle integration: consumer starts on app startup and is cancelled on cleanup.
- Expanded test coverage for causal propagation, signal processing, proxy lifecycle, and config/scoring validation.
- `Mem0Adapter` write-path boundary-matching fix — replaced naive `path.startswith()` with boundary-aware matching (exact match or prefix + `"/"`) plus a new opt-in `UpstreamConfig.read_subpaths` field for excluding known read sub-resources (e.g. `"search"`) that share a write path prefix with a write endpoint. Default `[]` preserves existing behavior for all prior configs.
- `Mem0Adapter` trailing-slash preservation fix — write forwarding now preserves the caller's exact path including trailing slash, fixing 405 errors against upstreams (e.g. Redis Agent Memory Server) that require a trailing slash on write endpoints. Regression tests pin both directions.
- New flagship example demo (`examples/subscription_demo/`) — Microsoft AgentFramework customer success agent backed by Redis Agent Memory Server and Azure Managed Redis. Demonstrates a SaaS subscription/entitlement cascade: a plan-tier change degrades the root entity and propagates via causal graph BFS to four dependent entitlement facts (seat limit, feature entitlements, API rate limit, billing terms). Includes a write-back correction tool so confidence recovery reflects genuinely corrected memory, not just elapsed time.

### Changed
- `POST /signals` is now consumed by a background processor that updates root entities and optional downstream causal relationships.
- `CausalGraph.add_relation()` now accepts weighted edges (`weight` in `(0,1]`).
- Example configs updated to include causal graph and signal pressure mapping blocks.
- Consolidated to a single flagship example demo. Removed `examples/crewai_pricing/` and `examples/langgraph_pricing/` (redundant parallel demos of the same scenario across three agent frameworks); added an "Integration Examples" section to `README.md` with minimal CrewAI/LangGraph/generic config snippets instead.
- Roadmap table clarified: Agent Memory Server support ships via Revok's generic adapter config (`read_subpaths` exclusion) rather than a dedicated adapter for v0.3.0; a dedicated `RedisAmsAdapter` remains planned for a future release.

---

## [0.2.0] — 2026-06-11

### Added
- Zep CE memory adapter — `revok/adapters/zep.py`
- Bitemporal scoring — `valid_time` and `transaction_time` tracking, decay anchored to event time
- Contradiction detection with configurable confidence penalty
- Fuzzy entity matching via `rapidfuzz`
- GitHub Actions CI workflow with OSS boundary check
- GitFlow branch policy enforcement
- Branching strategy documented in `CONTRIBUTING.md`

### Changed
- Adapter registry refactored for Open/Closed principle
- Test suite expanded from 119 to 248 tests

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

[0.5.0]: https://github.com/revok-ai/revok/releases/tag/v0.5.0
[0.4.0]: https://github.com/revok-ai/revok/releases/tag/v0.4.0
[0.3.0]: https://github.com/revok-ai/revok/releases/tag/v0.3.0
[0.2.0]: https://github.com/revok-ai/revok/releases/tag/v0.2.0
[0.1.1]: https://github.com/revok-ai/revok/releases/tag/v0.1.1
[0.1.0]: https://github.com/revok-ai/revok/releases/tag/v0.1.0
