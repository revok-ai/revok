# Feature Specification: Signal-Driven Causal Propagation

**Feature ID**: 005  
**Short Name**: signal-causal-propagation  
**Status**: Draft  
**Created**: 2026-06-20  
**Last Updated**: 2026-06-20

---

## Overview

Revok currently accepts external world signals via `POST /signals`, but those signals are only published to an in-memory bus and are not processed by any active consumer. As a result, external signals do not currently drive confidence degradation through the documented causal pipeline.

This feature implements real asynchronous signal processing in OSS core so that external signals degrade the signaled entity and propagate pressure through configured causal relationships using bounded BFS traversal. The solution preserves existing write-enrichment behavior and read latency characteristics while making the `/signals` path operational and consistent with documented architecture.

---

## Goals

- Make `POST /signals` functionally effective in OSS core by processing queued signals asynchronously.
- Degrade confidence for the signaled entity using existing scoring semantics.
- Add bounded, cycle-safe causal propagation so downstream entities receive attenuated pressure.
- Ensure deterministic propagation for multi-path graphs by using max pressure per target, never additive accumulation.
- Keep existing write-enrichment scoring path unchanged.
- Maintain zero added latency on memory reads by keeping signal work off the read path.

---

## Non-Goals

- Changing synchronous write-enrichment behavior in `metadata_writer.py`.
- Adding any new external dependencies beyond current stack and NetworkX.
- Introducing enterprise-only modules or closed-source integrations.
- Changing public semantics of existing read endpoints beyond expected score updates caused by processed signals.
- Implementing advanced graph analytics beyond required propagation behavior.

---

## User Scenarios & Testing

### US-CP1 — External signal degrades signaled entity

As an operator, when I send a world signal for an entity via `POST /signals`, I want that entity's confidence to drop within a bounded time window so that stale beliefs are not treated as fresh.

**Acceptance Scenarios**:
1. Given an entity with an existing record and no outgoing causal edges, when a signal is posted referencing that entity, then the entity's score is lower after consumer processing than before processing.
2. Given normal (non-degraded) runtime conditions, the signal consumer SHALL apply the resolved score update to the state store within 2 seconds of the signal being published to the queue.
3. Tests asserting "processed within bounded window" SHALL use the same 2-second bound (for example `asyncio.wait_for(..., timeout=2)` or polling with a 2-second ceiling), not arbitrary sleeps.

### US-CP2 — Causal propagation degrades downstream entities

As an operator, when an upstream entity changes, I want causally connected entities to be degraded according to configured hop attenuation and edge weights.

**Acceptance Scenarios**:
1. Given configured relationships and propagation settings, when an upstream entity is signaled, then each reachable downstream entity within bounds is degraded.
2. Propagated pressure equals prior hop pressure multiplied by edge weight and global attenuation per hop.
3. Branch propagation stops when pressure falls below minimum threshold or hop limit is reached.

### US-CP3 — Diamond graph uses strongest path only

As an operator, I want deterministic propagation where a downstream entity reachable through multiple paths receives the strongest pressure, not a sum, so confidence degradation is stable and predictable.

**Acceptance Scenarios**:
1. Given a diamond graph A->B->D and A->C->D with different cumulative path strengths, when A is signaled, then D receives exactly the larger propagated pressure of the two candidate paths.
2. D's applied pressure is not the arithmetic sum of both path pressures.

### US-CP4 — Cycles do not break processing

As an operator, I want cyclic causal graphs to be handled safely so signal processing never hangs or loops indefinitely.

**Acceptance Scenarios**:
1. Given a cycle (for example A->B->C->A), when A is signaled, then processing completes successfully within expected time.
2. No entity is visited repeatedly in a way that causes infinite traversal.

### US-CP5 — Runtime lifecycle is clean

As an operator, I need the signal consumer to start and stop with the server so there are no leaked background tasks.

**Acceptance Scenarios**:
1. Consumer starts automatically with application startup.
2. Consumer is cancelled and cleaned up during shutdown.
3. Shutdown leaves no orphaned running signal-consumer tasks.

---

## Functional Requirements

### FR-CP01 — Managed asynchronous signal consumer

The application SHALL run a managed background consumer that continuously reads signals from `AsyncioQueueBus` and processes them independently of request threads.

On any processing failure (entity resolution error, scoring error, store error, propagation error), the consumer SHALL log at `ERROR` level, discard the failed signal, and continue consuming the next signal. A single failed signal SHALL NOT terminate the consumer loop. This matches the existing resilience convention established in `metadata_writer.enrich()`.

### FR-CP02 — Signal processing for root entity

FR-CP02 root resolution: each `entity_refs` entry from the signal body is processed as an independent root entity key. No `X-Revok-Entity` header and no matcher/raw_content fallback apply to the `/signals` endpoint — that pattern is specific to the memory-write enrichment path and is out of scope here.

The pressure applied to each root entity SHALL be determined by mapping the signal’s `severity` field to a configurable weight via a `severity_weights` config block (`low`, `medium`, `high`, `critical` keys). If `severity` is absent or unrecognized, the system SHALL fall back to a configurable `default_severity` key. All weight values and the default are specified in `revok.yaml` — no hardcoded values. The resolved weight is the pressure used for root entity scoring and as the initial pressure value passed into causal graph propagation.

### FR-CP03 — Persistence of updated entity state

After scoring, the system SHALL persist updated entity records through the existing `StateStore` interface.

### FR-CP04 — Causal graph propagation operation

Per Constitution § III (Interface First — NON-NEGOTIABLE), a `GraphBackend` Protocol SHALL be defined in `revok/interfaces.py` before any `CausalGraph` implementation is written. The Protocol SHALL declare the propagation operation signature. `CausalGraph` SHALL implement this Protocol.

The `GraphBackend` Protocol operation SHALL accept a root entity key and initial pressure and return propagated pressure for reachable downstream entities.

### FR-CP05 — Propagation controls

Propagation SHALL support the following configurable controls:
- `max_hops`: upper bound on traversal depth.
- `min_pressure`: branch cutoff threshold.
- `attenuation`: multiplicative decay factor applied per hop.

### FR-CP06 — Relationship configuration

The configuration SHALL support causal relationships with `from`, `to`, and `weight` fields and load them at startup for runtime propagation.

### FR-CP07 — Pressure propagation math

For each traversed edge, propagated pressure SHALL be calculated as:

```
pressure_next = pressure_prev × edge_weight × attenuation
```

This formula is applied per traversed edge. Attenuation compounds naturally with traversal depth without requiring a separate depth counter.

### FR-CP08 — Multi-path conflict resolution

If an entity is reachable through multiple valid paths — whether from multiple paths within a single root’s BFS traversal (e.g. diamond A→B→D and A→C→D) or from independent root entities within the same signal (e.g. both entity-a and entity-b propagate to entity-d) — the propagated pressure retained for that entity SHALL be the maximum candidate pressure across all sources. Pressures are never summed.

### FR-CP09 — Cycle safety

Propagation SHALL be cycle-safe and SHALL terminate for cyclic graphs without infinite traversal.

### FR-CP10 — Branch termination rules

Propagation SHALL stop traversing a branch when either:
- computed pressure for next hop is below `min_pressure`, or
- traversal depth exceeds `max_hops`.

### FR-CP11 — Root-then-downstream application

Signal consumer behavior SHALL process the root entity and then apply degradation to each propagated downstream entity using its propagated pressure value.

### FR-CP12 — Startup and shutdown lifecycle integration

Background consumer startup and shutdown SHALL be integrated into server lifecycle management and cleanly cancel processing tasks during shutdown.

### FR-CP13 — Backward compatibility

Existing write-enrichment scoring behavior in `metadata_writer.py` SHALL remain unchanged.

### FR-CP14 — Dependency constraints

Implementation SHALL use existing project dependencies and SHALL NOT introduce new external packages.

### FR-CP15 — Documentation/config contract

Configuration SHALL accept a `causal_graph` section with this shape:

```yaml
causal_graph:
  relationships:
    - from: entity-a
      to: entity-b
      weight: 0.9
  propagation:
    min_pressure: 0.05
    max_hops: 3
    attenuation: 0.5
```

### FR-CP16 — Severity-to-pressure config block

Configuration SHALL accept severity mapping at the canonical location `scoring.signal_pressure.severity_weights` with fallback at `scoring.signal_pressure.default_severity`:

```yaml
scoring:
  # existing fields ...
  signal_pressure:
    severity_weights:
      low: 0.2
      medium: 0.4
      high: 0.6
      critical: 1.0
    default_severity: medium
```

All values SHALL be validated at startup: weights must be in `(0, score_cap]`; `default_severity` must be one configured key in `severity_weights`. Missing `scoring.signal_pressure.severity_weights` SHALL cause a `ConfigError` for this feature's runtime mode.

---

## Key Entities

- **Signal Event**: External world-change event received via `/signals` and queued for asynchronous processing.
- **Entity Record**: Persisted confidence state for a tracked entity, including score and temporal metadata.
- **Causal Relationship**: Directed weighted link from one entity to another indicating influence direction and strength.
- **Propagation Policy**: Runtime parameters (`max_hops`, `min_pressure`, `attenuation`) controlling traversal scope and pressure decay.
- **Propagation Result**: Mapping of reachable entity keys to final propagated pressure values (max-path semantics).

---

## Success Criteria

- For a signaled entity without configured outgoing relations, a `POST /signals` request causes that entity's score to decrease and be persisted within 2 seconds of queue publish under normal (non-degraded) runtime conditions.
- For a signaled entity with configured causal relations, all and only entities reachable within configured hop/pressure bounds show score degradation consistent with configured attenuation and edge weights.
- In a diamond-path topology, downstream pressure equals the higher path pressure and is never additive.
- In a cyclic topology, processing completes successfully without hang or unbounded runtime.
- Application startup and shutdown tests demonstrate consumer task creation and clean cancellation with no orphaned tasks.
- Existing test suite continues to pass with no regressions after feature integration.

---

## Assumptions

- Existing scoring behavior already defines acceptable pressure/severity semantics and remains the source of truth for pressure application.
- Entity keys used by this endpoint come from `entity_refs` entries in the `/signals` request body.
- Causal relationship configuration is loaded at startup and remains stable during process lifetime.
- Asynchronous processing windows used in tests are deterministic enough to assert bounded completion under CI conditions.

---

## Dependencies

- Existing modules: signal bus, scoring engine, state store, proxy lifecycle, and causal graph scaffold.
- Existing runtime dependencies only (`asyncio`, `aiohttp`, `aiosqlite`, `networkx`, current test/tooling stack).
- Affected OSS core areas expected: `revok/interfaces.py` (new `GraphBackend` Protocol), `revok/proxy.py`, `revok/signal_queue.py` integration points, `revok/causal_graph.py`, config loading, and tests in `tests/`.

---

## Clarifications

### Session 2026-06-20

- Q: Should this feature define a `GraphBackend` Protocol in `revok/interfaces.py` as a constitutional gate before the `CausalGraph` implementation, per Constitution § III (Interface First — NON-NEGOTIABLE)? → A: Yes — `GraphBackend` Protocol defined in `interfaces.py` first; `CausalGraph` implements it.
- Q: What pressure value should the consumer use for root entity degradation — fixed `signal_strength`, a severity-field mapping, or overridable per call? → A: Map `severity` field to pressure using a config-driven `severity_weights` block (low/medium/high/critical keys) with a `default_severity` fallback for missing/unrecognized values; fully configurable in `revok.yaml`, no hardcoded values; resolved weight is also the initial pressure for causal graph propagation.
- Q: What should the consumer do when a signal fails to process (store error, entity resolution failure, propagation error)? → A: Log at ERROR, discard the failed signal, continue consuming — matches existing resilience convention in `enrich()`; a single bad signal never terminates the consumer loop.
- Q: When a signal carries multiple `entity_refs`, how should the consumer behave? → A: Process each `entity_refs` entry from the signal body as an independent root entity key. No `X-Revok-Entity` header and no matcher/raw_content fallback apply to the `/signals` endpoint. When multiple roots' propagation trees overlap on a downstream entity, max-pressure-wins applies across roots too, not only within a single root's BFS traversal — pressures are never summed.
- Q: What is the exact per-hop pressure formula — per-edge multiplication or power-of-depth? → A: Per-edge: `pressure_next = pressure_prev × edge_weight × attenuation`; applied at each traversed edge, attenuation compounds naturally with depth, no depth counter needed.