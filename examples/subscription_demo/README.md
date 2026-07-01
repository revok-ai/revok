# Revok + AgentFramework + Redis AMS Demo

Shows Revok's stale-memory detection running with **Microsoft AgentFramework**
and **Redis Agent Memory Server (AMS)** backed by **Azure Managed Redis (AMR)**.

SaaS subscription/entitlement cascade demo — when billing downgrades a customer,
Revok propagates degraded confidence to all four dependent entitlement facts
simultaneously via BFS causal propagation.

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

A **customer success agent** answers the question *"What does this customer's
current plan include?"* by looking up the customer's subscription entitlements
from memory.

The customer starts on an **Enterprise** plan:

| Fact | Enterprise value |
|------|-----------------|
| Subscription tier | Enterprise |
| Seat limit | 50 seats |
| Feature entitlements | Advanced Analytics, SSO, Priority API Access, Custom Integrations |
| API rate limit | 1,000,000 requests/month |
| Billing terms | Annual contract, locked rate through 2027-06-01 |

The billing system **silently downgrades** the customer to **Starter** without
notifying the agent. Then a billing CDC signal fires.

**Without Revok:** the agent confidently reports Enterprise-tier entitlements
to the customer — wrong on all five facts.

**With Revok:** the BFS causal propagation degrades confidence on
`subscription-tier` (the root entity) AND simultaneously on all four dependent
entities (`seat-limit`, `feature-entitlements`, `api-rate-limit`,
`billing-terms`). The agent detects the stale signal, re-verifies all five
facts from the database, and answers correctly.

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

Optional backend toggle for Revok in this demo:

- Default (NetworkX): leave `REVOK_CONFIG_FILE` unset (uses `revok.yaml`)
- FalkorDB Lite: set `REVOK_CONFIG_FILE=revok.falkordb.yaml`

If you enable Falkor mode and your pulled image does not include Falkor extras,
run with `compose.local.yaml` so Revok is built locally.

> **Note:** `AMR_URL` must be a `rediss://` (TLS) connection string.
> Plain `redis://` connections are rejected by Azure Managed Redis.

### 2. Start the stack

```bash
docker compose up --build
```

Local Revok build (instead of published image):

```bash
docker compose -f compose.yaml -f compose.local.yaml up --build
```

Services:
- **redis-ams** → `localhost:8000` — Redis Agent Memory Server
- **revok** → `localhost:7771` — Revok proxy
- **api** → `localhost:8081` — FastAPI server
- **dashboard** → `localhost:3001` — Next.js dashboard

### 3. Run the demo

Open `http://localhost:3001` in your browser and follow the 4-step walkthrough:

1. **Load Customer Profile** — stores the Enterprise subscription memory in Redis AMS for both agents
2. **Simulate Plan Downgrade** — updates the DB to Starter (simulates a billing system change)
3. **Fire Billing Signal** — fires a CDC event to Revok, triggering BFS propagation across all 5 entities
4. **Ask Agent** — runs both agents; WITH REVOK re-verifies all entitlements, WITHOUT REVOK answers with stale Enterprise entitlements

**Reset** returns everything to the initial Enterprise state.

---

## How It Works

### Causal Graph

Revok tracks a 5-node causal graph for the subscription:

```
subscription-tier (root)
  ├── seat-limit          (weight 0.9)
  ├── feature-entitlements (weight 0.9)
  ├── api-rate-limit      (weight 0.8)
  └── billing-terms       (weight 0.7)
```

When a billing CDC signal fires on `subscription-tier`, Revok performs a BFS
propagation that simultaneously degrades confidence on all four dependents.
The agent sees all five facts as stale in a single round-trip.

### Architecture

```
Customer question
       │
       ▼
AgentFramework tool-use loop
       │
       ├── Tool: check_memory_with_revok
       │       └── POST /v1/long-term-memory/search
       │               └── Revok proxy → Redis AMS
       │
       ├── Tool: get_revok_confidence (entity: subscription-tier)
       │       └── GET /v1/entities/subscription-tier
       │               └── Returns score + BFS-propagated dependent scores
       │
       ├── [signal_count > 0] Tool: get_current_entitlements
       │       └── SELECT from SQLite subscription_state
       │
       ├── [signal_count > 0, values differ] Tool: store_corrected_memory
       │       ├── DELETE /v1/entities/subscription-tier  (reset stale record)
       │       └── POST /v1/long-term-memory              (write via Revok proxy)
       │
       └── LLM generates answer from verified entitlements
```

### Confidence degradation

When an external change signal has been received (`signal_count > 0`), the
WITH-REVOK agent automatically calls `get_current_entitlements` to re-verify
all five live values before answering, regardless of whether confidence is
fresh, degraded, or stale. Confidence recovers over time (90-second half-life
in demo mode).

### Memory self-correction write-back

When confidence is low, the agent re-verifies from the source of truth and writes
the corrected value back to memory. This closes the loop — confidence recovering
toward FRESH over time is only meaningful because the underlying memory was
actually corrected when staleness was detected. Without a write-back step like
this, a production agent must ensure something — itself or another system —
corrects the stored memory before confidence fully recovers, or trust will be
restored in stale data.

The `store_corrected_memory` tool handles this: it clears the stale Revok entity
record and writes the verified facts through the Revok proxy, resetting confidence
and ensuring the next query sees accurate data.

### AgentFramework vs LangGraph

Unlike the LangGraph demo which uses a compiled `StateGraph`, this demo uses
AgentFramework's declarative `@tool` decorator and explicit tool invocation.
The LLM decides which tools to call based on system instructions and tool
descriptions — standard OpenAI function calling.

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
subscription_demo/
├── agent.py          # AgentFramework customer success agent (tool-use loop)
├── server.py         # FastAPI API server
├── database.py       # SQLite subscription state DB
├── demo_state.py     # Demo state persistence
├── revok.yaml        # Revok proxy config (causal graph + entities)
├── docker-compose.yml
├── Dockerfile.api
├── .env.example
└── dashboard/        # Next.js dashboard
```


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

Optional backend toggle for Revok in this demo:

- Default (NetworkX): leave `REVOK_CONFIG_FILE` unset (uses `revok.yaml`)
- FalkorDB Lite: set `REVOK_CONFIG_FILE=revok.falkordb.yaml`

If you enable Falkor mode and your pulled image does not include Falkor extras,
run with `compose.local.yaml` so Revok is built locally.

> **Note:** `AMR_URL` must be a `rediss://` (TLS) connection string.
> Plain `redis://` connections are rejected by Azure Managed Redis.

### 2. Start the stack

```bash
docker compose up --build
```

Local Revok build (instead of published image):

```bash
docker compose -f compose.yaml -f compose.local.yaml up --build
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
subscription_demo/
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
