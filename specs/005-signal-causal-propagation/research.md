# Research — Signal-Driven Causal Propagation (005)

## Decision 1: Signal consumer lifecycle is app-managed background task
- **Decision**: Run a long-lived async consumer task tied to aiohttp app startup/shutdown.
- **Rationale**: `/signals` must remain fast (`202`) while processing happens asynchronously; lifecycle-bound task prevents orphan workers.
- **Alternatives considered**:
  - Process inline in request handler: rejected (adds request latency, violates async separation).
  - External worker process: rejected for OSS MVP scope and operational complexity.

## Decision 2: Root entity resolution prioritizes explicit references
- **Decision**: Resolve root entity in this order: `X-Revok-Entity` header (if present), then `entity_refs` payload, then matcher fallback on `raw_content`.
- **Rationale**: explicit references are deterministic and match current production recommendation; matcher fallback preserves usability.
- **Alternatives considered**:
  - Matcher-only: rejected (ambiguous for sparse/malformed signal payloads).
  - Require header only: rejected (too strict for existing `/signals` JSON producers).

## Decision 3: Pressure-aware scoring uses existing formula family
- **Decision**: Introduce pressure parameterization around existing degradation logic, keeping current `score()` semantics for write-enrichment call sites.
- **Rationale**: feature requires propagated pressures; preserving existing method behavior avoids regressions.
- **Alternatives considered**:
  - Reuse fixed `signal_strength` only: rejected (cannot model attenuation).
  - Separate bespoke propagation scorer: rejected (drifts from core scoring behavior).

## Decision 4: BFS propagation with max-path pressure and cycle safety
- **Decision**: Propagation computes downstream pressure via breadth-first traversal bounded by `max_hops`, cutoff by `min_pressure`, and per-hop multiplication by `attenuation` and edge weight; retain maximum pressure per target.
- **Rationale**: directly satisfies bounded traversal, diamond max-path rule, and deterministic behavior.
- **Alternatives considered**:
  - Summing pressures from all paths: rejected (explicitly disallowed).
  - DFS only: rejected (less intuitive for hop-bounded propagation semantics).

## Decision 5: Relationship weights are configured statically in YAML
- **Decision**: Load weighted relationships at startup from `causal_graph.relationships`.
- **Rationale**: keeps runtime fast and deterministic; aligns with existing config-driven architecture.
- **Alternatives considered**:
  - Dynamic relationship updates at runtime: rejected (out of scope for this feature).
  - Unweighted graph only: rejected (cannot express varying causal strength).

## Decision 6: `/signals` path remains publish-only API, processing decoupled
- **Decision**: Keep `POST /signals` contract (`202 Accepted`) unchanged; only consumer implementation changes behavior downstream.
- **Rationale**: preserves compatibility with existing clients and operational expectations.
- **Alternatives considered**:
  - Add synchronous confirmation response with score deltas: rejected (not required; introduces latency/coupling).
