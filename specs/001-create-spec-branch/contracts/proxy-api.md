# Contract: HTTP Proxy API

**Service**: Revok Memory Signal Proxy | **Version**: 0.1.0

Revok acts as a transparent HTTP proxy between AI agents and Mem0. It intercepts memory-write requests, enriches them, and forwards all requests to the configured Mem0 upstream.

---

## Endpoints

### Memory Write (Intercepted + Enriched)

**Trigger**: Any request whose HTTP method matches `upstream.write_methods` AND whose path matches a prefix in `upstream.write_paths` (both configurable in YAML).

**Default match**: `POST /v1/memories`

#### Request (from AI agent to Revok)

```
POST /v1/memories
Host: <revok-host>:<port>
Content-Type: application/json

{
  "content": "Alice mentioned the project deadline is next Friday.",
  "agent_id": "agent-42",
  ...
}
```

*Any JSON body is accepted. Revok does not validate the shape of the original payload.*

#### Processing

1. Revok receives the request and parses the JSON body.
2. Runs entity extraction on `content` (if present) or full body text.
3. Scores extracted entities and updates state store.
4. Reconstructs body with `x_revok` metadata block appended.
5. Forwards enriched body to `upstream.mem0_url + original_path`.
6. Returns Mem0's response to the caller.

#### Request forwarded to Mem0 (enriched)

```json
{
  "content": "Alice mentioned the project deadline is next Friday.",
  "agent_id": "agent-42",
  "x_revok": {
    "version": "0.1.0",
    "processed_at": "2026-05-29T12:00:00.000Z",
    "entities": [
      {
        "id": "alice",
        "score": 0.87,
        "signal_count": 3,
        "last_seen": "2026-05-29T11:59:00.000Z",
        "pattern_name": "person"
      }
    ]
  }
}
```

#### Response to AI agent

Revok returns the unmodified response from Mem0 (status code, headers, body).

| Scenario | Status |
|----------|--------|
| Mem0 accepted write | `201 Created` (or whatever Mem0 returns) |
| Enrichment succeeded, Mem0 error | Upstream status (4xx/5xx) |
| Enrichment failed (pipeline error) | Forward original (non-enriched) body to Mem0; log error |
| Mem0 unreachable | `502 Bad Gateway` |
| Revok internal error | `500 Internal Server Error` |

---

### Memory Read + All Non-Write Requests (Pass-Through)

**Trigger**: Any request NOT matching a configured write path/method.

**Default match**: `GET /v1/memories`, `DELETE /v1/memories/{id}`, and all other paths/methods.

#### Behavior

Revok forwards the request to Mem0 exactly as received — no modification to body, headers (except `Host` rewrite), or path.

The response from Mem0 is returned to the caller exactly as received.

```
GET /v1/memories?agent_id=agent-42 → forwarded unchanged to Mem0
← Mem0 response returned unchanged to caller
```

---

## Headers

| Direction | Header | Treatment |
|-----------|--------|-----------|
| Agent → Revok → Mem0 | `Host` | Rewritten to `upstream.mem0_url` host |
| Agent → Revok → Mem0 | All others | Copied verbatim |
| Mem0 → Revok → Agent | All | Copied verbatim |

---

## Error Semantics

Revok MUST NOT silently discard signals. On any enrichment failure:
1. Log the error with full stack trace at `ERROR` level.
2. Forward the original (non-enriched) payload to Mem0.
3. Return Mem0's response to the caller.

On Mem0 unreachability: Return `502 Bad Gateway` with a JSON body:

```json
{"error": "upstream_unavailable", "detail": "Mem0 endpoint is not reachable"}
```
