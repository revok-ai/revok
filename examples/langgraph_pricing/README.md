# Revok + LangGraph Demo

Demonstrates how Revok's confidence scoring prevents stale-memory errors in
a LangGraph agent — comparing a naive agent (no Revok) against a
confidence-aware agent that routes itself through an explicit re-verification
step when memory is stale.

## What LangGraph adds vs. direct LLM calls

| Aspect | Direct LLM calls | LangGraph StateGraph |
|---|---|---|
| **State** | Ad-hoc variables scattered across the function | Explicit `AgentState` TypedDict; every field typed and visible |
| **Routing** | Buried `if/else` inside one big async function | Named `route_on_confidence` node with conditional edges |
| **Observability** | Hard to know which branch ran | `astream_events(version="v2")` surfaces every node transition and LLM token |
| **Checkpointing** | None | LangGraph persistence layer (add a checkpointer to replay or resume) |
| **Testability** | Must mock the whole function | Each node is a pure async function, testable in isolation |

## Graph topology

```
START
  │
  ▼
retrieve_memory        ← lists Mem0 memories + queries Revok confidence
  │
  ▼
route_on_confidence    ← pure routing node (no LLM call)
  │                 │
  │ stale/degraded  │ fresh / unknown / use_revok=False
  ▼                 ▼
verify_price    generate_answer  ← calls LLM via LangChain
  │                 ▲
  └─────────────────┘
                     │
                    END
```

**Routing rules** (inside `route_on_confidence`):

| `use_revok` | confidence status | next node |
|---|---|---|
| `False` | any | `generate_answer` |
| `True` | `fresh` or `unknown` | `generate_answer` |
| `True` | `degraded` | `generate_answer` (with caveat prompt) |
| `True` | `stale` | `verify_price` → `generate_answer` (uses live price) |

## Setup

### 1. Copy and fill in credentials

```bash
cd examples/langgraph_pricing
cp .env.example .env
# Edit .env — fill in AZURE_OPENAI_* or OPENAI_API_KEY
```

### 2. Start services

```bash
docker compose up --build
```

Services start on ports that avoid conflicts with the CrewAI demo:

| Service | Default port |
|---|---|
| Qdrant | 6334 |
| Mem0 | 7772 |
| Revok proxy | 7773 |
| API server | 8081 |
| Dashboard (Next.js) | 3001 |

### 3. Open the dashboard

Visit [http://localhost:3001](http://localhost:3001) and follow the demo steps:
1. **Load Memory** — seeds pricing for all 5 catalog products
2. **Change Price** — updates SQLite (simulates a database write)
3. **Trigger Signal** — sends a CDC event through Revok
4. **Ask Agent** — compare answers: the naive agent quotes stale memory; the
   Revok-aware agent detects low confidence and re-verifies from the live DB

## How the graph works at runtime

### Batch (`/actions/ask-agent`)

```python
result = await graph.ainvoke(initial_state)
```

All four nodes run sequentially; the final `AgentState` is returned.

### Streaming (`/stream/ask-agent`)

```python
async for event in graph.astream_events(initial_state, version="v2"):
    ...
```

LangGraph fires typed events for every node transition and LLM token:

| Event type | Source |
|---|---|
| `on_chain_start` / `on_chain_end` | Each node |
| `on_chat_model_stream` | LLM token stream inside `generate_answer` |

`server.py` maps these to SSE events consumed by the dashboard.

## Comparison with the CrewAI demo

See [examples/crewai_pricing](../crewai_pricing/) for the CrewAI version.
Both demos expose identical API endpoints and the same SSE event schema, so
the same dashboard drives both. The only difference is what runs the agent:
- CrewAI: `Agent`, `Task`, `Crew`, `BaseTool`, `Process.sequential`
- LangGraph: `StateGraph`, `TypedDict` state, conditional edges, `astream_events`
