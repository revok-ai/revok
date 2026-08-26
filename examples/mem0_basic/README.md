# Revok + Mem0 — end-to-end demo

Demonstrates Revok's transparent proxy sitting in front of [Mem0](https://mem0.ai).
The scenario: an AI agent stores a memory about Redis Enterprise pricing, a pricing
change fires an invalidation signal, and Revok shows the confidence score has degraded.

## Prerequisites

- Docker Desktop (or Docker Engine + Compose plugin)
- An Azure OpenAI resource (or Azure AI Foundry project) with two deployed models:
  - A chat model, e.g. `gpt-5-mini`
  - An embeddings model, e.g. `text-embedding-3-small`

## Step 1 — Configure your Azure OpenAI credentials

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env   # macOS / Linux
```
```powershell
Copy-Item .env.example .env   # Windows PowerShell
```

Then edit `.env` — find the values in **Azure AI Foundry → your project → Settings → API keys**.

Optional backend toggle for Revok in this demo:

- Default (NetworkX): leave `REVOK_CONFIG_FILE` unset (uses `revok.yaml`)
- FalkorDB Lite: set `REVOK_CONFIG_FILE=revok.falkordb.yaml`

## Step 2 — Start services

```bash
docker compose up --build -d
```

The default flow pulls Revok from GHCR (`ghcr.io/revok-ai/revok:latest`).

Local Revok source build override:

```bash
docker compose -f compose.yaml -f compose.local.yaml up --build -d
```

This starts three containers:

| Container | Host port | Role |
|-----------|-----------|------|
| `qdrant`  | 6333      | Vector store for Mem0 |
| `mem0`    | **7770**  | Mem0 memory server |
| `revok`   | **7771**  | Revok transparent proxy |

Wait until all three are healthy (≈ 60 s on first run while images build and
pip packages install):

```bash
docker compose ps   # all three should show "healthy"
```

## Step 3 — Run the demo

```bash
pip install requests   # one-time; skip if already installed
python demo.py
```

Expected output:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Step 1 — Store memory: Redis Enterprise is $500 / month
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Mem0 response: { ... }
Revok entity  : 'redis enterprise'
Score at t=0  : 0.8000  (signal_strength = 0.80)
Signal count  : 1

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Step 2 — Waiting 10s (one half-life) to simulate time passing …
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Score at t=10s : 0.4000
Decay          : 0.8000 → 0.4000  (50% drop)

⚠  The '$500/month' memory is losing confidence — it may be stale.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Step 3 — Send pricing-change invalidation signal
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
...
Signal count  : 2

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Summary
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  t=  0s  score=0.8000  memory written with high confidence
  t= 10s  score=0.4000  confidence decayed — memory is getting stale
  t= 10s+ score=…       pricing-change signal fired  (signal_count=2)

Any agent reading the original memory should inspect
x_revok.entities for 'redis enterprise' and see
signal_count=2 — the $500/month figure needs re-verification.
```

## How it works

```
demo.py  ──POST /memories──►  Revok :7771
                                │  extracts "Redis Enterprise" entity
                                │  scores it (signal_strength=0.80)
                                │  injects x_revok metadata into body
                                └──POST /memories──►  Mem0 :7770
                                                        │  stores enriched memory
                                                        └──► Qdrant :6333

demo.py  ──GET /v1/entities/redis%20enterprise──►  Revok :7771
                                                     │  applies live exponential decay
                                                     └──► {"score": 0.40, "signal_count": 1}
```

Revok's entity API (`GET /v1/entities/{key}`) returns the **live-decayed** score — the
stored score is multiplied by `exp(-λ·Δt)` at read time, so confidence naturally falls
between writes.  A second write about the same entity bumps `signal_count`, flagging the
memory for re-verification.

## Tear-down

```bash
docker compose down -v   # -v removes the revok_data volume
```
