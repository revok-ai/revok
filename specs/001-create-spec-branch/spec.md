# Feature Specification: Revok MVP — Memory Signal Processor

**Feature Branch**: `001-create-spec-branch`

**Created**: 2026-05-29

**Status**: Draft

**Version**: 0.1.0

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Signal Interception and Enrichment (Priority: P1)

An AI agent developer wants all memory writes from their agent to pass through Revok so that each memory entry arrives at the memory store enriched with extracted entities and their current confidence scores. The developer makes no changes to their agent's code; they simply point the agent at Revok's proxy endpoint instead of the memory store directly.

**Why this priority**: This is the core value proposition of Revok. Without signal interception and enrichment, the system has no function. Every other story depends on this pipeline being operational.

**Independent Test**: Start Revok with a minimal YAML config, send a single simulated memory-write signal, and confirm that the downstream memory adapter receives the original content plus entity metadata and confidence scores in the response — delivering the enriched write as the only observable output.

**Acceptance Scenarios**:

1. **Given** Revok is running and a memory adapter is configured, **When** an AI agent sends a memory-write signal containing free-text content, **Then** the signal reaches the downstream adapter with the original content intact and an enriched metadata payload including at least one extracted entity and its confidence score.
2. **Given** a signal containing no recognizable entity patterns, **When** the signal is processed, **Then** it is forwarded to the adapter unchanged with an empty entity metadata section (not dropped or rejected).
3. **Given** two signals referencing the same entity arrive in sequence, **When** both are processed, **Then** the entity's confidence score in the second signal's metadata reflects the accumulated signal history (higher than after only one signal).
4. **Given** Revok is processing a signal, **When** an unrecoverable error occurs in the enrichment pipeline, **Then** the original signal is still forwarded to the adapter and the error is logged — no data is lost.

---

### User Story 2 — Mem0 HTTP Proxy Integration (Priority: P2)

A developer using Mem0 as their agent's memory store wants to insert Revok between their agent and Mem0 with minimal configuration. They provide the Mem0 server URL in Revok's YAML config, start Revok, and from that point all agent memory traffic flows through Revok's proxy before reaching Mem0. No agent-side code changes are needed.

**Why this priority**: Mem0 is the first and only memory adapter in the MVP. The proxy must be functional for any end-to-end test to be meaningful. Without a working Mem0 adapter, the enrichment pipeline cannot be validated against a real memory store.

**Independent Test**: Configure Revok with a running Mem0 instance URL, issue a memory-write request through Revok's proxy, and verify the write appears in Mem0 with the enriched metadata fields present — demonstrating a complete round-trip.

**Acceptance Scenarios**:

1. **Given** Mem0's HTTP endpoint is specified in config, **When** Revok starts, **Then** Revok is reachable at its own proxy address and forwards all requests to Mem0.
2. **Given** a memory-write request arrives at Revok's proxy, **When** processing succeeds, **Then** Mem0 receives the write with the original payload plus enriched entity metadata fields appended.
3. **Given** Mem0 is temporarily unavailable, **When** a signal arrives, **Then** Revok returns an appropriate error to the caller without crashing and without discarding the signal silently.
4. **Given** a memory-read request arrives at Revok's proxy (not a write), **When** it is forwarded to Mem0, **Then** the response is returned to the caller unchanged — Revok does not modify read responses.

---

### User Story 3 — YAML Configuration (Priority: P3)

A developer deploying Revok wants to control all runtime behavior — listening address, Mem0 endpoint, entity patterns, decay parameters, persistence path — through a single YAML file without touching source code or environment variables for normal settings.

**Why this priority**: Configuration-driven behavior is required by the architecture constraints. It is the foundation for making Revok adaptable to different deployments and testable in isolation. It unlocks independent testing of all other components.

**Independent Test**: Start Revok with two different YAML configs (e.g., different ports, different decay half-lives), confirm each instance behaves according to its own config — demonstrating the system reads and applies config at startup.

**Acceptance Scenarios**:

1. **Given** a valid YAML config file exists at the path provided on startup, **When** Revok starts, **Then** all runtime parameters match the values in that file.
2. **Given** a YAML config file is missing or malformed, **When** Revok attempts to start, **Then** startup fails immediately with a clear, actionable error message indicating the problem.
3. **Given** no hardcoded values exist in the source code for configurable parameters, **When** a developer inspects the code, **Then** every tuneable value (ports, paths, decay constants, pattern sets) is sourced from config.

---

### User Story 4 — Entity State Visibility (Priority: P4)

An operator running Revok wants to inspect the current confidence scores of tracked entities without modifying the system, to understand what the signal processor has learned over time.

**Why this priority**: Observability validates that the scoring engine works correctly and that state persists across restarts. It is essential for debugging and acceptance testing, but the core pipeline (P1–P3) must work first.

**Independent Test**: Process several signals referencing a known entity, then query the state store directly and confirm entity records with timestamps and scores are present and reflect the decay model.

**Acceptance Scenarios**:

1. **Given** signals referencing entity "Alice" have been processed, **When** the state store is queried for "Alice", **Then** a record exists with a confidence score, last-seen timestamp, and signal count.
2. **Given** no signal referencing "Bob" has been processed, **When** the state store is queried for "Bob", **Then** no record is returned.
3. **Given** an entity was last seen more than one configured half-life ago, **When** the state store is queried, **Then** that entity's score is lower than its peak score, reflecting exponential decay.
4. **Given** Revok is restarted after processing signals, **When** the state store is queried post-restart, **Then** entity records from before the restart are still present (persistence is durable).

---

### User Story 5 — Developer Onboarding and Test Suite (Priority: P5)

A new contributor wants to clone the repository, read the README, run the test suite, and understand the architecture — all without needing external dependencies beyond those listed in the project requirements.

**Why this priority**: Documentation and tests are required by the architecture constraints and are the safety net for all future development. They are the last story because they require all other components to exist before they can be fully written.

**Independent Test**: Clone the repository on a clean machine, follow the README, run `pytest`, and confirm all tests pass — demonstrating the project is self-contained and well-documented.

**Acceptance Scenarios**:

1. **Given** a developer follows the README setup instructions, **When** they run the test suite, **Then** all tests pass with no additional configuration.
2. **Given** a developer reads the README, **When** they want to start Revok locally, **Then** they can do so in under 15 minutes without referring to any external documentation.
3. **Given** any public function or method in the codebase, **When** a developer inspects it, **Then** it has both a type-annotated signature and a docstring.
4. **Given** the test suite runs, **When** it completes, **Then** every module (config loader, entity matcher, state store, signal queue, scoring engine, proxy, metadata writer) has at least one test that exercises its primary behavior.

---

### Edge Cases

- What happens when a signal payload exceeds a configurable maximum size?
- How does the system handle entity patterns that match overlapping spans in the same signal?
- What happens when the SQLite WAL file is corrupted at startup?
- How does the system behave when the in-memory hot layer is full (eviction policy)?
- What happens when the same entity name appears with different capitalizations across signals?
- How does the system handle a Mem0 response that contains unexpected or malformed JSON?
- What happens when the decay half-life is set to zero or a negative value in config?

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST load all runtime configuration from a YAML file; no configurable value may be hardcoded in source.
- **FR-002**: System MUST define a `Protocol` interface class for each pluggable component (`SignalSource`, `MessageBus`, `StateStore`, `MemoryAdapter`) in a dedicated interfaces module before any concrete implementation is written.
- **FR-003**: System MUST accept incoming memory signals via an asynchronous HTTP endpoint acting as a transparent proxy.
- **FR-004**: System MUST normalize incoming signals into a canonical internal representation before any further processing.
- **FR-005**: System MUST extract entities from signal content using configurable regular expression patterns; no third-party NLP library may be used for this purpose.
- **FR-006**: System MUST score each extracted entity using an exponential decay model where the decay rate is a configurable parameter.
- **FR-007**: System MUST persist entity state (entity identifier, current score, last-seen timestamp, signal count) to a durable local store using SQLite in WAL mode.
- **FR-008**: System MUST maintain an in-memory cache layer for recently accessed entity state to reduce persistence read overhead.
- **FR-009**: System MUST forward all memory-write signals to the Mem0 HTTP endpoint with enriched metadata (extracted entities and their current scores) appended to the payload.
- **FR-010**: System MUST forward all memory-read and non-write signals to the Mem0 HTTP endpoint without modification.
- **FR-011**: System MUST NOT delete or overwrite any existing memory entries in Mem0; it may only append metadata or update confidence scores.
- **FR-012**: All I/O operations (HTTP, file, database) MUST be implemented as asynchronous coroutines; the `requests` library and any synchronous HTTP client MUST NOT be used.
- **FR-013**: Every module MUST have at least one automated test covering its primary behavior.
- **FR-014**: All runtime diagnostic output MUST use the Python `logging` module; `print` statements are prohibited in non-test code.
- **FR-015**: Every public function and method MUST carry a type-annotated signature and a docstring describing its purpose, parameters, and return value.
- **FR-016**: System MUST start and be ready to accept signals within a configurable startup timeout after launch.
- **FR-017**: System MUST reject startup and emit a clear error if the YAML configuration file is absent, unreadable, or structurally invalid.
- **FR-018**: System MUST NOT import any managed cloud-provider SDK (Azure, AWS, GCP, Confluent) in any module.

### Key Entities

- **Signal**: A memory event submitted by an AI agent, carrying raw content, a source identifier, and a timestamp. Signals are immutable once received.
- **Entity**: A named concept extracted from a signal's content by pattern matching. Identified by a normalized string key.
- **EntityRecord**: The persisted state of an entity, comprising its identifier, current confidence score, last-seen timestamp, and cumulative signal count.
- **EnrichedPayload**: The original signal payload augmented with a structured metadata block listing matched entities and their current scores.
- **Config**: The runtime configuration tree loaded from YAML, governing all tuneable parameters across every component.
- **MemoryAdapterResponse**: The response returned by the downstream memory adapter (Mem0) after a proxied write or read operation.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A developer with no prior knowledge of the codebase can clone the repository, read the README, and have a working local Revok instance processing signals within 15 minutes.
- **SC-002**: 100% of memory-write signals processed by Revok reach the downstream Mem0 adapter with their original content fully intact (zero data-loss writes).
- **SC-003**: Entity confidence scores are updated and readable within a single signal-processing cycle after each new signal arrives (no deferred or batched-only scoring).
- **SC-004**: Every module in the codebase has at least one passing automated test at the time of the 0.1.0 release.
- **SC-005**: Revok starts, processes a single end-to-end signal through the full pipeline, and writes enriched metadata back to Mem0 in under 5 seconds on a standard developer machine (4-core CPU, 8 GB RAM).
- **SC-006**: Zero existing Mem0 memory entries are deleted during any Revok operation across all test scenarios.
- **SC-007**: All entity scores in the state store decrease monotonically over time when no new signals arrive for those entities, confirming exponential decay is applied correctly.
- **SC-008**: Revok restarts and restores all entity state from the durable store within the configured startup timeout, with no entity records lost.

## Assumptions

- Revok runs as a single local process on the same machine as the AI agent (no distributed deployment, no multi-tenancy in v0.1.0).
- Mem0 is already deployed and reachable via HTTP; Revok does not provision or manage the Mem0 instance.
- The Mem0 HTTP API accepts standard JSON payloads and returns JSON responses; no authentication beyond URL-based routing is assumed for the MVP.
- The memory adapter interface is designed for extension but only Mem0 is implemented in v0.1.0.
- Entity patterns are defined by the operator in YAML config; Revok ships with a minimal default pattern set for demonstration purposes only.
- SQLite WAL mode is sufficient for single-process concurrency; no external database is needed.
- Redis and any in-process caching library beyond Python's built-in data structures are out of scope for v0.1.0.
- Enterprise features — multi-tenancy, SSO, RBAC, audit logging, SaaS dashboard, and advanced scoring models — are explicitly deferred beyond v0.1.0.
- Python 3.11 or later is available in the deployment environment.
- Tests are written using the standard Python `pytest` ecosystem; no additional test infrastructure (Docker, CI) is required to run the suite locally.
- The causal graph (NetworkX) is included as a dependency in v0.1.0 but graph-traversal features may be scaffolded without full implementation if time-constrained.
