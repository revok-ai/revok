# Feature Specification: Zep Community Edition Memory Adapter

**Feature ID**: 001  
**Short Name**: zep-adapter  
**Status**: Draft  
**Created**: 2026-06-08  
**Last Updated**: 2026-06-08

---

## Overview

Revok currently tracks entity confidence scores for agents that use Mem0 as their
memory backend. Agents using **Zep Community Edition** as their memory backend receive
no entity tracking today.

This feature adds a Zep Community Edition adapter so that Revok can sit transparently
between an AI agent and a Zep CE server. The adapter observes memory write events,
extracts entity references, and updates confidence scores — all without altering the
data seen by Zep or the agent. Agents require zero code changes.

---

## Goals

- Enable Revok entity tracking for AI agents that use Zep Community Edition.
- Guarantee transparent proxying: Zep always receives the original, unmodified request.
- Keep read operations fully invisible to Revok (no latency overhead on memory retrieval).
- Provide consistent configuration and operational behavior with the existing Mem0 adapter.

---

## Non-Goals

- Supporting Zep Cloud (hosted) or Zep Server editions beyond Community Edition.
- Modifying, filtering, or augmenting the data returned to the agent from Zep.
- Providing a migration path between Mem0 and Zep backends.
- Any write to Zep that originates from Revok (Revok is read-only with respect to Zep state).

---

## User Stories

### US-Z1 — Transparent entity tracking for Zep agents

> As an operator deploying a Zep-backed AI agent, I want Revok to track which
> entities appear in memory writes so that I can query confidence scores without
> changing my agent's code or the data stored in Zep.

**Acceptance Criteria**:
- Given an agent POSTs a memory message containing a known entity to the Zep session
  memory endpoint, when the request passes through Revok, then Revok updates the
  entity's confidence score.
- Given the same request, when Zep receives it, the request body is byte-identical to
  what the agent sent.
- Given an agent issues any read request to Zep (e.g. GET session memory), when the
  request passes through Revok, Revok adds no latency overhead beyond network pass-through.

### US-Z2 — Zep upstream unavailability

> As an operator, I want Revok to return a clear error response when Zep is unreachable
> so that my agent can handle the failure gracefully.

**Acceptance Criteria**:
- Given Zep is unreachable, when a write or read request arrives at Revok, Revok
  returns a structured 502 error response without hanging.

### US-Z3 — Adapter selection via configuration

> As an operator, I want to choose the Zep adapter through configuration so that
> I can switch memory backends without redeploying or recompiling Revok.

**Acceptance Criteria**:
- Given a Revok configuration file that specifies Zep as the upstream adapter, when
  Revok starts, it routes all proxy traffic to the configured Zep base URL.
- Given a configuration omitting the adapter type, Revok defaults to the existing
  Mem0 behavior (no regression).

---

## Functional Requirements

### Write Interception

**FR-Z01**: Revok intercepts HTTP POST requests whose path matches the Zep session
memory write pattern and whose HTTP method is in the configured write-methods list.
The path match uses a **prefix-and-suffix anchor**: a path is a write path if and only if
it starts with `/api/v1/sessions/` AND ends with `/memory`. A bare prefix match is
too broad (matches `/api/v1/sessions/abc/memory/extra`); an exact template match adds
unnecessary complexity. The `write_paths` config field is **optional** for the Zep
adapter (defaults to an empty list); the anchor is the sole write-detection rule.
If provided, `write_paths` is stored for informational or future use only.

**FR-Z02**: For each intercepted write request, Revok extracts entity references from
the `content` field of each object in the `messages` array of the request body.
No other fields (metadata, role, UUID fields, or top-level summary) are scanned.

**FR-Z03**: For each extracted entity, Revok updates the entity's confidence score
using the same exponential-decay scoring engine used by the Mem0 adapter.

**FR-Z04**: The write request body forwarded to Zep MUST be byte-identical to the
body received from the agent. Revok MUST NOT modify, re-encode, or reformat it.

**FR-Z04a**: When constructing a `Signal` for a Zep write event, Revok derives
`source_id` using the following priority order:
1. The `sessionId` value extracted from the URL path
   (`/api/v1/sessions/{sessionId}/memory`).
2. The `X-Agent-ID` request header (if `sessionId` cannot be parsed).
3. The literal string `"unknown"` (if neither is present).

### Read and Pass-Through

**FR-Z05**: All non-write requests (GET, DELETE, PATCH, and any POST not matching
the write path) are forwarded to Zep unchanged without entity extraction.

**FR-Z06**: Responses from Zep are forwarded to the caller with hop-by-hop headers
stripped, matching the behavior of the existing Mem0 adapter.

### Adapter Interface

**FR-Z07**: The Zep adapter satisfies the same `MemoryAdapter` interface as the
existing Mem0 adapter, exposing `write()`, `forward()`, and `close()` operations.
The `MemoryAdapter` Protocol in `interfaces.py` is **not modified**.

**FR-Z08**: `ZepAdapter.write()` accepts an `EnrichedPayload` (same signature as
`Mem0Adapter.write()`) and extracts the original Zep request body from
`EnrichedPayload.original_bytes` (`bytes`) to forward byte-identical to Zep. Entity metadata
accumulated during enrichment is recorded in the Revok state store only; it is
never injected into the upstream request. This mirrors the Mem0 pattern: the adapter
receives the enriched wrapper and decides what to forward — Mem0 sends the enriched
dict, Zep sends the original bytes.

### Configuration

**FR-Z09**: Revok configuration accepts an `adapter_type` discriminator field that
selects between `mem0` (default, preserves existing behavior) and `zep` adapter modes.

**FR-Z10**: When `adapter_type: zep` is set, a new independent `ZepUpstreamConfig`
dataclass (distinct from `UpstreamConfig`) holds the Zep CE base URL (`zep_url`) plus
`write_methods` (required, non-empty) and `write_paths` (optional, defaults to `[]`).
`write_paths` has different semantics from the Mem0 counterpart: for the Zep adapter
it is informational only and does not gate write detection (see FR-Z01). The existing
`UpstreamConfig` dataclass is not modified; this avoids coupling two unrelated adapters
in one struct and prevents misconfiguration (e.g., setting both `mem0_url` and
`zep_url` simultaneously).

### Observability

**FR-Z13**: The Zep adapter emits log messages at the same severity levels as the Mem0
adapter: `ERROR` when the Zep upstream is unreachable (502 path), `WARNING` when an
incoming write body is not valid JSON. In addition, the adapter emits a `DEBUG`-level
log on each intercepted write containing the following fields: `session_id` (the
`sessionId` extracted from the request path) and `http_path` (the full request path),
enabling per-session tracing without producing INFO noise in production.

### Error Handling

**FR-Z11**: When Zep is unreachable, Revok returns a 502 Bad Gateway response with a
JSON body identifying the upstream as unavailable. No exception propagates to the caller.

**FR-Z12**: When the incoming write request body is not valid JSON, Revok forwards
it to Zep unchanged without attempting entity extraction (consistent with Mem0 behavior).

---

## User Scenarios & Testing

### Scenario 1 — Successful memory write with entity match

1. Agent sends `POST /api/v1/sessions/abc123/memory` with a JSON body containing
   a message referencing a known entity (e.g., a product name).
2. Revok intercepts the request, extracts the entity, updates its confidence score.
3. Revok forwards the original, unmodified body to the Zep upstream.
4. Zep returns `200 OK`; Revok returns the same `200 OK` to the agent.
5. **Verify**: `GET /v1/entities/{entityKey}` on Revok returns an updated score.
6. **Verify**: The body received by Zep is byte-identical to the body sent by the agent.

### Scenario 2 — Memory read (pass-through)

1. Agent sends `GET /api/v1/sessions/abc123/memory` to Revok.
2. Revok forwards the request to Zep without interception or enrichment.
3. Zep returns its response; Revok forwards it unchanged.
4. **Verify**: No entity records are created or updated for read requests.

### Scenario 3 — Non-JSON write body

1. Agent sends `POST /api/v1/sessions/abc123/memory` with a non-JSON body.
2. Revok detects the invalid JSON and skips entity extraction.
3. Revok forwards the original body to Zep unchanged.
4. **Verify**: No exception is raised; the upstream receives the original body.

### Scenario 4 — Zep upstream unavailable

1. Zep CE is not running; agent sends a write request to Revok.
2. Revok attempts to contact Zep and receives a connection error.
3. Revok returns `502 Bad Gateway` with a JSON error body to the agent.
4. **Verify**: No unhandled exception; structured error response returned.

### Scenario 5 — Configuration fallback (no adapter_type set)

1. Operator deploys Revok with an existing Mem0 configuration (no `adapter_type` field).
2. Revok starts and uses the Mem0 adapter (default behavior).
3. **Verify**: No regression; existing Mem0 tests pass unchanged.

---

## Success Criteria

1. **Zero agent-side changes**: An agent already configured to write to a Zep CE
   instance can route through Revok without modifying any client-side code or
   request format.

2. **Lossless proxying**: 100% of write requests delivered to Zep carry a body
   byte-identical to the body received from the agent.

3. **Entity tracking parity**: Entity confidence scores are updated on Zep write
   events at the same accuracy and latency as on Mem0 write events.

4. **Read-path overhead**: Read requests add no measurable processing overhead
   beyond the network round-trip to Zep.

5. **Operational consistency**: The Zep adapter shares configuration schema
   patterns, error codes, and observability behavior with the Mem0 adapter so
   that operators face no new operational concepts.

6. **No Mem0 regression**: All existing Mem0 adapter tests continue to pass
   after the Zep adapter is introduced.

---

## Key Entities

| Entity | Description |
|--------|-------------|
| **Session** | A Zep CE session identified by `sessionId` in the write path. Maps conceptually to a Revok `source_id`. |
| **Memory message** | A single message object within a Zep write payload; the textual unit from which entities are extracted. |
| **Entity record** | Revok's persisted confidence-score record for a named entity, updated on each write event. |
| **Adapter** | The pluggable upstream component that forwards requests. Either `Mem0Adapter` or `ZepAdapter`. |

---

## Dependencies

- Existing Revok components: `MemoryAdapter` interface, `EntityMatcher`, `ScoringEngine`,
  `StateStore`, `enrich()` pipeline function.
- Zep Community Edition running at a reachable HTTP/HTTPS base URL.
- No new external libraries beyond those already used by the Mem0 adapter.

---

## Assumptions

1. The Zep CE session memory write endpoint is always `POST /api/v1/sessions/{sessionId}/memory`.
   If Zep changes this path in a future CE release, the optional `write_paths` config field
   is available for documentation or custom tooling; it does not affect write detection.
2. Entity-extractable text lives in the `content` fields of the `messages` array in the
   Zep write payload. Other fields (metadata, role, facts) are not scanned — this is now
   an explicit functional requirement (FR-Z02).
3. The `sessionId` in the path is the primary `source_id` for the Revok signal;
   `X-Agent-ID` header is the fallback; `"unknown"` is the final fallback (FR-Z04a).
4. Zep CE returns standard HTTP status codes; no Zep-specific error format handling is required.
5. A new `ZepUpstreamConfig` dataclass is introduced; the existing `UpstreamConfig` is
   not modified. This keeps the two adapters independently validated and avoids any
   misconfiguration risk from a shared struct with optional fields.
6. **Authentication**: Zep CE authentication (e.g., API key) is carried by the agent via standard
   HTTP headers (e.g., `Authorization`). Revok forwards all headers verbatim to Zep as part of
   normal header pass-through. Revok does not store, inject, or validate any Zep credential;
   operators configure credentials on their agent, not on Revok.

---

## Out of Scope

- Zep fact or knowledge-graph extraction (only raw message content is scanned).
- Bidirectional sync between Revok entity scores and Zep metadata fields.
- Multi-tenant or multi-session aggregation beyond what the existing store supports.

---

## Clarifications

### Session 2026-06-08

- Q: Does Zep CE require any auth token/API key that Revok must handle? → A: Zep CE API key is forwarded transparently via header pass-through — no special Revok config needed.
- Q: Which fields in the Zep write payload should Revok scan for entities? → A: Only `messages[].content` (the text of each memory message turn).
- Q: Should `ZepAdapter` share `UpstreamConfig` with `Mem0Adapter` or use its own dataclass? → A: Introduce a new, independent `ZepUpstreamConfig` dataclass; do not modify `UpstreamConfig`.
- Q: What log levels should the Zep adapter emit? → A: Match Mem0 (ERROR on 502, WARNING on non-JSON) plus DEBUG per intercepted write including the `sessionId` from the path.
- Q: What value should be used as `source_id` when constructing a Signal for a Zep write? → A: `sessionId` from URL path → `X-Agent-ID` header → `"unknown"`.
- Q: Does `ZepAdapter.write()` accept `EnrichedPayload` or a different type, and does the `MemoryAdapter` Protocol need updating? → A: `write()` accepts `EnrichedPayload` unchanged; Protocol is not modified. The adapter extracts `original_bytes` (bytes) from the payload and forwards it byte-identical to Zep.
- Q: Should write-path detection use `startswith`, an exact template match, or another strategy? → A: Prefix-and-suffix anchor — `path.startswith("/api/v1/sessions/") and path.endswith("/memory")`. Avoids false positives without template-parsing complexity.
