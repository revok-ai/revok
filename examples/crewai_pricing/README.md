# Revok Pricing Demo — Stale Memory Simulation

A production-realistic simulation showing how [Revok](https://github.com/your-org/revok)
prevents AI agents from quoting stale pricing data.

**Scenario:** A sales agent reads the current price from a SQLite product database and
stores it as long-term memory in [Mem0](https://mem0.ai) via the Revok proxy.
Later, the price changes in the database.
An Azure Function detects the change and fires a signal to Revok, which degrades the
agent's confidence in that memory.

- **WITHOUT Revok** — agent trusts memory blindly, keeps quoting the old price.
- **WITH Revok** — agent checks confidence; when degraded/stale it re-queries the live
  database before answering.

---

## Setup

### 1 — Configure `.env`

```bash
cp .env.example .env
```

Edit `.env` and fill in your credentials.
Either **Option A** (Azure OpenAI) or **Option B** (plain OpenAI) is required.
All other values have sensible defaults.

### 2 — Start the Docker services

Starts Qdrant (vector store), Mem0 (memory layer), and the Revok proxy.

```bash
docker compose up -d
```

Default ports (override in `.env`):

| Service | Port |
|---------|------|
| Qdrant  | 6333 |
| Mem0    | 7770 |
| Revok   | 7771 |

### 3 — Start the dashboard server

```bash
cd examples/crewai_pricing
python server.py
```

Open **http://localhost:8080** (or `DEMO_PORT` if overridden).

---

## 5-step demo flow

Work through the Control Panel buttons in order:

| Step | Button | What it does |
|------|--------|--------------|
| 1 | **Load Memory** | Both agents query the database and store the current price in their Mem0 memories via Revok |
| 2 | **Change Price** | Updates the price in the SQLite `products` table — agents' memories are now stale |
| 3 | **Trigger Signal** | Simulates an Azure Function POSTing to Revok, which matches the `Redis Enterprise` entity pattern and degrades confidence |
| 4 | **Ask Agent** | Both agents answer a budget question — compare the responses side by side |
| 5 | **Reset Demo** | Clears Mem0 memories, resets the database price to seed, and clears all state |

---

## How it works

```
┌─────────────────────────────────────────────────────────────┐
│                      PRODUCTION ANALOGY                      │
│                                                              │
│  Azure Function (price-change trigger)                       │
│       │                                                      │
│       ▼  POST /memories  { "pricing has changed…" }         │
│  ┌─────────┐                                                 │
│  │  Revok  │ ← matches \bRedis Enterprise\b pattern         │
│  │  Proxy  │   degrades confidence score                     │
│  └─────────┘                                                 │
│       │                                                      │
│       ▼                                                      │
│  Agent (WITH Revok) checks confidence before answering       │
│    • fresh   → trusts memory, answers from it                │
│    • degraded/stale → calls get_current_price_tool()         │
│                       re-verifies from live DB               │
└─────────────────────────────────────────────────────────────┘
```

**Revok signal mechanism:** Revok has no dedicated `/signals` endpoint.
The "Azure Function" button simulates what an actual Azure Function would do in
production: it detects a row update in the `products` table and POSTs a message
to `{REVOK_URL}/memories` with `user_id: "azure-function-signal"`.
Revok intercepts the write, matches the `Redis Enterprise` entity pattern, and
records a signal that decays the confidence score of any memory about that entity.

---

## Dashboard layout

| Column | Content |
|--------|---------|
| 🗄 SQL Database | Current price in the DB, last-updated timestamp, memory-alignment badge |
| 🧠 What the Agent Believes | Mem0 memory content, half-circle confidence gauge (green → amber → red), signal count |
| 🤖 Sales Agent Response | Side-by-side agent answers with verification banners |

The dashboard polls `/state` every 2 seconds — no page refresh needed.

---

## Python dependencies

```bash
pip install fastapi uvicorn aiohttp python-dotenv aiosqlite
```

(`crewai` is not required — the agents are implemented directly with `aiohttp` calls to Mem0 and Revok.)

