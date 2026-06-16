# Revok + AgentFramework + Redis AMS Demo

Shows Revok's stale-memory detection running with **Microsoft AgentFramework**
and **Redis Agent Memory Server (AMS)** backed by **Azure Managed Redis (AMR)**.

Same Redis Enterprise pricing scenario as the other demos — but the entire stack
is Microsoft/Azure:

| Component | Technology |
|-----------|------------|
| Agent framework | Microsoft AgentFramework (explicit tool-use loop) |
| Memory store | Redis Agent Memory Server (AMS) |
| Memory backend | Azure Managed Redis (AMR) — TLS required |
| LLM | Azure OpenAI (or plain OpenAI) |
| Revok proxy | Intercepts `/v1/long-term-memory` writes |
| Dashboard | Next.js (identical to other demos) |

---

## The Story

A sales agent stores a memory that Redis Enterprise costs **$500/month**
via Redis AMS backed by Azure Managed Redis.

1. The price changes in the database.
2. An external CDC signal fires to Revok.
3. Revok **degrades confidence** on the stored memory.
4. The AgentFramework agent runs two tools:
   - `check_memory` — reads from Redis AMS, sees memory is stale
   - `get_current_price` — fetches the live price from SQLite
5. The **WITH REVOK** agent catches the drift and re-verifies.
6. The **WITHOUT REVOK** agent answers with the wrong stale price.

---

## Prerequisites

- **Azure Managed Redis (AMR)** instance — a `rediss://` TLS connection string
  is required. Redis AMS needs an in-memory backend; AMR is the Azure-native
  choice. Free-tier or Balanced tier both work for the demo.
- Docker + Docker Compose
- Azure OpenAI resource (or OpenAI API key)

---

## Setup

### 1. Copy and fill in your environment variables

```bash
cp .env.example .env
```

Edit `.env` and set:

```bash
# Required — your Azure Managed Redis TLS connection string
AMR_URL=rediss://:<password>@<hostname>.redis.cache.windows.net:6380

# Required — Azure OpenAI (Option A, recommended)
AZURE_OPENAI_API_KEY=your-key
AZURE_OPENAI_ENDPOINT=https://your-resource.cognitiveservices.azure.com
AZURE_OPENAI_LLM_DEPLOYMENT=gpt-4o-mini
AZURE_OPENAI_EMBEDDER_DEPLOYMENT=text-embedding-ada-002
AZURE_OPENAI_API_VERSION=2025-04-01-preview

# Or plain OpenAI (Option B)
# OPENAI_API_KEY=sk-...
```

> **Note:** `AMR_URL` must be a `rediss://` (TLS) connection string.
> Plain `redis://` connections are rejected by Azure Managed Redis.

### 2. Start the stack

```bash
docker compose up --build
```

Services:
- **redis-ams** → `localhost:8000` — Redis Agent Memory Server
- **revok** → `localhost:7771` — Revok proxy
- **api** → `localhost:8081` — FastAPI server
- **dashboard** → `localhost:3001` — Next.js dashboard

### 3. Run the demo

Open `http://localhost:3001` in your browser and follow the 6-step walkthrough:

1. **Load Memory** — stores pricing data in Redis AMS for both agents
2. **Change Price** — updates the price in SQLite (simulates a DB change)
3. **Trigger Signal** — fires a CDC event to Revok, degrading memory confidence
4. **Ask Agent** — runs both agents; WITH REVOK re-verifies, WITHOUT REVOK answers stale
5. **Signal Pressure** — fires 3 signals to push confidence from degraded → stale
6. **Reset** — clears everything and returns to step 1

---

## How It Works

### Architecture

```
Customer question
       │
       ▼
AgentFramework tool-use loop
       │
       ├── Tool: check_memory
       │       └── POST /v1/long-term-memory/search
       │               └── [WITHOUT REVOK] → Redis AMS directly
       │               └── [WITH REVOK]    → Revok proxy → Redis AMS
       │
       ├── [WITH REVOK only] GET /v1/entities/{key}
       │       └── Revok returns confidence score + status
       │
       ├── [stale only] Tool: get_current_price
       │       └── SELECT from SQLite
       │
       └── LLM generates answer
```

### Confidence degradation

Revok intercepts every **write** to `/v1/long-term-memory` and records a
signal for the entity identified by the `X-Revok-Entity` header (or text
matching). Each signal reduces the confidence score:

```
fresh (1.0) → 1 signal → 0.6 (degraded) → 2 signals → 0.2 (stale)
```

When confidence drops below 0.3 (stale), the WITH-REVOK agent automatically
calls `get_current_price` to fetch the live value before answering.
Confidence recovers over time (90-second half-life in demo mode).

### AgentFramework vs LangGraph

Unlike the LangGraph demo which uses a compiled `StateGraph`, the
AgentFramework demo uses an explicit tool-use loop:

```python
# Step 1 — Tool: check_memory
memories = await agent.check_memory(product_name)

# Step 2 — Revok confidence check (WITH REVOK only)
score, count, status = await agent._get_revok_confidence(entity_key)

# Step 3 — Tool: get_current_price (stale path only)
if status == "stale":
    live_price = await agent.get_current_price(product_name)

# Step 4 — LLM generates the answer
answer = await llm.ainvoke([system, user])
```

This pattern is idiomatic for Microsoft AgentFramework's declarative tool
registration — tools are first-class objects called directly by the loop,
not wired through a graph compiler.

---

## Azure Managed Redis Notes

- AMR replaces the Redis Enterprise / Qdrant setup used in the other demos.
- Redis AMS requires Redis with vector search (RediSearch module).
  AMR Balanced and Enterprise tiers include this.
- The `REDIS_URL` passed to Redis AMS must use the `rediss://` scheme
  (TLS) as required by Azure Managed Redis.
- For the demo, a **Balanced C1** (1 GB) AMR instance is sufficient.

---

## File Structure

```
agentframework_pricing/
├── agent.py          # AgentFramework sales agent (tool-use loop)
├── server.py         # FastAPI API server
├── database.py       # SQLite product pricing DB
├── demo_state.py     # Demo state persistence
├── revok.yaml        # Revok proxy config (upstream → redis-ams)
├── docker-compose.yml
├── Dockerfile.api
├── .env.example
└── dashboard/        # Next.js dashboard (identical to other demos)
```
