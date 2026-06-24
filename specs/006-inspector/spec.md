# Feature Specification: Inspector

**Feature ID**: 006
**Short Name**: inspector
**Status**: Draft
**Created**: 2026-06-23
**Last Updated**: 2026-06-23

---

## Overview

Revok's causal memory propagation makes entity confidence non-obvious: a memory's confidence may have dropped because of a direct signal, an upstream cascade, or both. Developers currently have no way to answer "why is this entity's confidence at X?" without reading logs or inspecting database records directly.

The Inspector adds a read-only explainability layer to Revok’s existing causal pipeline. It exposes a structured JSON API that allows developers to inspect any tracked entity’s current state, understand its position in the causal graph, trace propagation paths from root entities, and identify the signals responsible for confidence changes.

The Inspector is built exclusively on existing protocols (`GraphReader`, `StateStore`) and a new `Inspector` protocol defined in `revok/interfaces.py` before any implementation is written. It has no direct knowledge of NetworkX internals.

---

## Goals

- Surface entity state (score, timestamps, signal count, contradiction count) via a single read-only API call.
- Surface direct causal relationships (upstream sources, downstream targets, edge weights) for any entity.
- Surface all downstream entities reachable from a given root within configured hop and pressure bounds.
- Surface propagation paths: ordered entity chains from a root entity to any downstream target, with per-hop pressure values.
- Surface the reasons memories became invalid — which signals triggered which degradation events, direct or propagated.
- Support any `GraphReader` implementation (currently NetworkX via `CausalGraph`) without coupling Inspector logic to any concrete graph backend.
- Add zero latency to write enrichment and signal processing paths.

---

## Non-Goals

- Visual UI dashboard — output is structured JSON only; rendering is out of scope.
- Mutation of entity state, graph structure, or signal history through Inspector endpoints.
- Enterprise analytics, multi-tenancy, RBAC, SSO, or audit logging.
- Aggregated cross-entity metrics or monitoring dashboards.
- Storing or replaying full signal body payloads.
- Adding any cloud SDK or enterprise dependency forbidden by the OSS boundary (Constitution § II).

---

## User Scenarios & Testing

### US-IP1 — Inspect entity state and direct causal neighbors (Priority: P1)

As a developer integrating Revok, when a memory is behaving unexpectedly (lower confidence than anticipated), I want to query a single endpoint for an entity to see its current state and its immediate causal neighbors so I can determine at a glance whether the issue is local to that entity or caused by an upstream cascade.

**Why this priority**: Core explainability value — the minimum useful output that makes the Inspector meaningful. All higher-order inspection (paths, blast radius, signal history) builds on this foundation.

**Independent Test**: Can be fully tested by querying `GET /v1/inspector/entities/{entity_key}` for an entity with a known score and at least one configured causal neighbor, then asserting the response includes the entity's score, timestamps, signal count, contradiction count, a list of direct downstream neighbors with edge weights, and a list of direct upstream sources.

**Acceptance Scenarios**:

1. **Given** entity `alice` exists with score 0.4, two signals processed, and one configured downstream neighbor `bob` with weight 0.8, **When** `GET /v1/inspector/entities/alice` is called, **Then** the response body includes `score: 0.4`, `signal_count: 2`, a `downstream` list containing `bob` with `weight: 0.8`, and an empty `upstream` list.
2. **Given** an entity key that does not exist in the state store or graph, **When** `GET /v1/inspector/entities/{missing_key}` is called, **Then** the response status is 404 with a structured error body containing the missing key.
3. **Given** an entity that exists in the state store but has no causal relationships configured, **When** its inspection endpoint is called, **Then** the response returns the entity's state with empty `upstream` and `downstream` lists and no error.
4. **Given** `causal_graph.enabled = false` in config, **When** the entity inspection endpoint is called, **Then** entity state fields are returned and both `upstream` and `downstream` lists are empty rather than returning an error.

---

### US-IP2 — Trace propagation paths from upstream roots to a target entity (Priority: P2)

As a developer, when I see that a downstream entity has unexpectedly low confidence, I want to trace all propagation paths from upstream roots to that entity so I can identify which root entity triggered the cascade and how much pressure arrived via each path.

**Why this priority**: Answers the "why was this entity degraded?" question at causal depth — only meaningful once basic state inspection (P1) works.

**Independent Test**: Can be tested by configuring a three-entity causal chain A → B → C, posting a signal for A, waiting for processing to complete, then querying `GET /v1/inspector/entities/C/paths` and asserting the response contains at least one path with ordered hops `[A, B, C]` and per-hop pressure values showing attenuation.

**Acceptance Scenarios**:

1. **Given** a configured causal chain A → B → C with `attenuation: 0.5` and all edge weights 1.0, **When** A is signaled and the consumer finishes processing, **Then** `GET /v1/inspector/entities/C/paths` returns a path `[A, B, C]` with pressure values reflecting two attenuation steps.
2. **Given** a diamond graph (A → B → D and A → C → D), **When** A is signaled, **Then** the paths response for D contains two paths and the higher-pressure path is marked as dominant, consistent with max-pressure semantics used by the signal processor.
3. **Given** an entity with no upstream sources, **When** `GET /v1/inspector/entities/{entity_key}/paths` is called, **Then** the response returns an empty `paths` list, not an error.
4. **Given** a graph with a cycle (A → B → A), **When** the paths endpoint is called, **Then** each entity appears at most once in any returned path and the call completes without hanging.

---

### US-IP3 — List all downstream entities within propagation reach of a root (Priority: P2)

As an operator debugging a cascade, I want to know all entities that are reachable from a given root entity within the configured propagation bounds, so I can assess the blast radius of a signal before or after it is sent.

**Why this priority**: Equal priority to path tracing — provides the "blast radius" view essential for operating causal graphs in production.

**Independent Test**: Can be tested by configuring a multi-level graph, calling `GET /v1/inspector/entities/{root_key}/downstream`, and asserting the response lists all entities reachable within configured `max_hops` and `min_pressure` limits, excluding entities that would fall below the pressure threshold.

**Acceptance Scenarios**:

1. **Given** a three-level graph A → B, A → C, B → D with all computed pressures above `min_pressure`, **When** `GET /v1/inspector/entities/A/downstream` is called, **Then** the response lists B, C, and D with their computed pressures.
2. **Given** a graph where entity D would receive pressure below `min_pressure` after attenuation, **When** the downstream endpoint is called for the root, **Then** D is excluded from the response, consistent with signal processor propagation semantics.
3. **Given** a graph with a cycle (A → B → A), **When** the downstream endpoint is called, **Then** no entity appears more than once in the response and the call completes without infinite recursion.
4. **Given** `max_hops: 1`, **When** the downstream endpoint is called for a root with a three-hop chain, **Then** only direct neighbors are returned.

---

### US-IP4 — Inspect signal history that affected an entity (Priority: P3)

As a developer, when an entity's confidence is unexpectedly low, I want to see a log of signal events that have affected it (directly signaled or received via propagation) so I can trace invalidation back to its origin.

**Why this priority**: Requires signal events to be persisted by the processor — a new data concern; dependent on P1–P3 being operational first.

**Independent Test**: Can be tested by posting a signal for entity `alice`, waiting for processing, then querying `GET /v1/inspector/entities/alice/signals` and asserting the response contains a record with source_id, processed_at, and before/after score fields. Propagation can be tested by asserting a downstream entity `bob` has a signal record with `is_propagated: true` and `upstream_source: alice`.

**Acceptance Scenarios**:

1. **Given** a signal is posted and processed for entity `alice`, **When** `GET /v1/inspector/entities/alice/signals` is called, **Then** the response includes at least one record containing `source_id`, `processed_at`, `score_before`, `score_after`, and `is_propagated: false`.
2. **Given** entity `bob` was degraded via causal propagation from a signal to `alice`, **When** `GET /v1/inspector/entities/bob/signals` is called, **Then** the response includes a propagation record with `is_propagated: true` and `upstream_source: alice`.
3. **Given** no signals have been processed for an entity, **When** its signals endpoint is called for an entity that exists, **Then** the response returns an empty list, not an error.
4. **Given** an entity that does not exist, **When** its signals endpoint is called, **Then** the response returns 404.

---

### Edge Cases

- What happens when the graph backend is not configured (`causal_graph.enabled = false`)? Graph-related fields (upstream, downstream, paths) return empty results; entity state is still returned.
- What happens when the state store contains an entity with no corresponding graph node? Entity state is returned with empty causal relationship lists.
- What happens when `max_hops: 0`? The downstream and paths endpoints return empty lists; entity state is still returned.
- What happens when two propagation paths reach the same entity? The response lists both paths; the entity appears once in downstream results with the maximum pressure value (consistent with propagation semantics).
- How does the Inspector behave under concurrent signal processing writes? Inspector reads are point-in-time; partially-applied writes from an in-progress signal consumer cycle are invisible.

---

## Requirements

### Functional Requirements

- **FR-001**: A new `Inspector` Protocol MUST be defined in `revok/interfaces.py` before any implementation is written (Interface First — Constitution § III).
- **FR-002**: The `Inspector` implementation MUST depend only on `GraphReader` and `StateStore` protocols — it MUST NOT import `CausalGraph` or any concrete graph backend directly.
- **FR-003**: A new `GraphReader` Protocol MUST be defined in `revok/interfaces.py` providing read-only graph introspection methods (`has_node`, `successors`, `predecessors`, `edge_weight`, `node_score`) sufficient for the Inspector to query neighbors and paths. This protocol is distinct from the existing `GraphBackend` write protocol and MUST NOT require changes to `GraphBackend`.
- **FR-004**: The existing `CausalGraph` (NetworkX) implementation MUST implement the extended `GraphBackend` protocol; no existing tests may regress.
- **FR-005**: The system MUST expose `GET /v1/inspector/entities/{entity_key}` returning entity state and direct causal neighbors (upstream and downstream) with edge weights.
- **FR-006**: The system MUST expose `GET /v1/inspector/entities/{entity_key}/downstream` returning all entities reachable from the given entity within `causal_graph.max_hops` and `causal_graph.min_pressure` bounds.
- **FR-007**: The system MUST expose `GET /v1/inspector/entities/{entity_key}/paths` returning all propagation paths from any upstream root to the given entity, including per-hop pressure values.
- **FR-008**: The system MUST expose `GET /v1/inspector/entities/{entity_key}/signals` returning a log of signal events (direct and propagated) that have affected the entity.
- **FR-009**: Signal event records MUST be persisted by the signal processor at processing time; the persistence mechanism MUST use the existing SQLite WAL store and MUST be config-driven.
- **FR-011**: All Inspector API responses MUST be JSON with a stable, documented schema; response structure MUST NOT vary between different `GraphReader` implementations.
- **FR-012**: Inspector endpoints MUST be read-only — they MUST NOT mutate entity state, graph structure, or signal history.
- **FR-013**: Inspector endpoints MUST return 404 with a structured error body for any entity key that does not exist in the state store.
- **FR-014**: Inspector endpoints MUST respect `causal_graph.enabled = false` by returning empty graph data (upstream, downstream, paths) rather than an error.
- **FR-015**: All Inspector code MUST include type hints on all functions and docstrings on all public methods (Constitution § Architecture Constraints).
- **FR-016**: Every new module introduced by this feature MUST have a corresponding test module in `tests/`.
- **FR-017**: Inspector endpoints MUST add no measurable latency to `POST /signals` or `GET /v1/entities/{key}` — all Inspector reads are independent of the write and signal paths.

### Key Entities

- **InspectionReport**: Point-in-time view of one entity — state fields (score, valid_time, transaction_time, signal_count, contradiction_count) plus direct causal neighbor lists.
- **CausalNeighbor**: One directed edge endpoint in the inspection response — entity_key, direction (upstream | downstream), edge weight, and current score from the state store.
- **PropagationPath**: Ordered list of entity keys from a root to a target, with per-hop pressure values and a flag marking the dominant path in multi-path graphs.
- **SignalRecord**: A persisted event record written by the signal processor — source_id, entity_key, processed_at, score_before, score_after, is_propagated, upstream_source (nullable).

---

## Success Criteria

### Measurable Outcomes

- **SC-001**: A developer can determine the reason for an entity's confidence value (direct signal vs. propagation cascade, and from which root) with no more than two API calls per entity.
- **SC-002**: Inspector read endpoints add no measurable latency to `POST /signals` processing or `GET /v1/entities/{key}` read responses under equivalent load.
- **SC-003**: Inspector endpoints correctly resolve downstream entities and propagation paths for graphs of up to 100 nodes and 500 edges within the same response-time budget as existing read endpoints.
- **SC-004**: All Inspector endpoints return schema-conformant JSON for all valid inputs including missing entities (404) and entities with no causal relationships (empty lists).
- **SC-005**: Zero regressions in existing test suite; zero new OSS boundary violations (Constitution § II).

---

## Assumptions


- Signal event history can be persisted in a new table within the existing SQLite WAL store; no new external storage dependency is required.
- A new `GraphReader` Protocol (FR-003) can be defined in `revok/interfaces.py` with read-only methods; the existing `CausalGraph` implementation can implement it without breaking current behavior.
- The Inspector targets developer and operator tooling; no end-user authentication, access control, or rate limiting is in scope.
- The `causal_graph` config block already in use for propagation will be reused to drive Inspector behavior (max_hops, min_pressure, attenuation, enabled flag) — no new top-level config section is required.
