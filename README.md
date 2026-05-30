# Revok

**The memory validity layer for AI agents.**

Every memory system updates beliefs from conversation.
None listen to the world changing around them.
Revok does.

---

## The problem

Your agent remembers that Redis Enterprise costs $500/month.
Then Redis changes their pricing.

Your agent still says $500/month.

No memory system today — Mem0, Zep, Redis AMR — knows that 
the world changed. They track *when a memory was stored*, 
not *whether it is still true*.

Revok fixes this.

---

## How it works

Revok sits between your agent and its memory store as a 
transparent HTTP proxy. It listens for real-world signals, 
resolves which memories are affected using a causal graph, 
and attaches a confidence score at retrieval time.

**Zero agent refactoring required.**

```python
# Your existing code — completely unchanged
memories = mem0.search(query, user_id=user_id)

# Confidence already in the response
# {"content": "Redis Enterprise is $500/month",
#  "metadata": {"revok_confidence": 0.26, "revok_status": "stale"}}
```

Signal processing is async. Zero added read latency.

---

## Confidence states

| Status     | Score   | Meaning                  |
|------------|---------|--------------------------|
| `fresh`    | > 0.7   | Memory is reliable       |
| `degraded` | 0.3–0.7 | Use with caution         |
| `stale`    | < 0.3   | Do not trust             |

---

## Configuration

```yaml
server:
  port: 7771
  upstream_mem0_url: http://localhost:7770

entity_matcher:
  entities:
    - id: redis-enterprise-pricing
      type: pricing
      patterns: ["Redis Enterprise", "RE pricing"]

causal_graph:
  relationships:
    - from: redis-enterprise-pricing
      to: roi-calculation
      weight: 0.9

scoring:
  time_decay:
    function: exponential
    half_life_hours: 24
  signal_pressure:
    severity_weights:
      low: 0.1
      medium: 0.3
      high: 0.5
      critical: 0.8
```

---

## Architecture

```
External signal
↓
Signal normalizer
↓
Entity resolver  ←  YAML entity registry
↓
Causal graph     ←  NetworkX BFS traversal
↓
Scoring engine   ←  Exponential decay × pressure
↓
State store      ←  SQLite WAL + in-memory hot layer
↓
Metadata writer  →  Confidence score in memory metadata
```

---

## Memory adapters

| Adapter | Status      |
|---------|-------------|
| Mem0    | ✅ v0.1.0   |
| Zep     | 🔜 v0.2.0   |
| Redis AMR | 🔜 v0.3.0 |

---

## Signal sources

| Source         | Tier       |
|----------------|------------|
| Webhooks       | OSS        |
| Redis Streams  | OSS        |
| Azure Event Hubs | Enterprise |
| AWS EventBridge | Enterprise |
| Google Pub/Sub | Enterprise |

---

## OSS limits

- Up to ~1,000 signals/minute
- Single process deployment
- Self-hosted only

Enterprise tier adds multi-tenancy, managed cloud signal 
sources, SSO/RBAC, audit logging, and a SaaS dashboard.

---

## License

AGPL v3. Enterprise licensing available — 
contact [your email].

---

## Status

`v0.1.0` — MVP. Mem0 adapter. Production use at your 
own risk. Feedback welcome.
