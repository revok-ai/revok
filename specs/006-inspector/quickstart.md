# Quickstart — Inspector (006)

**Branch**: feat/inspector
**Date**: 2026-06-23

---

## Prerequisites

Revok running locally with at least one entity in the state store and at least one causal relationship configured.

## Minimal Config

```yaml
# config/revok.example.yaml (additions for Inspector)
causal_graph:
  enabled: true
  relationships:
    - from: alice
      to: bob
      weight: 0.8
  propagation:
    max_hops: 3
    min_pressure: 0.05
    attenuation: 0.5

inspector:
  enabled: true
  signal_history_enabled: true
```

## Step 1 — Trigger a signal

```bash
curl -s -X POST http://localhost:4000/signals \
  -H "Content-Type: application/json" \
  -d '{"entity_refs": ["alice"], "severity": "high"}' | jq .
```

Expected: `202 Accepted`

## Step 2 — Inspect entity state

```bash
curl -s http://localhost:4000/v1/inspector/entities/alice | jq .
```

Expected: JSON with `score`, `downstream` containing `bob`.

## Step 3 — Check downstream blast radius

```bash
curl -s http://localhost:4000/v1/inspector/entities/alice/downstream | jq .
```

Expected: `downstream` list with `bob` and its computed pressure.

## Step 4 — Trace propagation paths to bob

```bash
curl -s http://localhost:4000/v1/inspector/entities/bob/paths | jq .
```

Expected: `paths` list with one path `["alice", "bob"]`, pressures, and `is_dominant: true`.

## Step 5 — Check signal history for bob

```bash
curl -s http://localhost:4000/v1/inspector/entities/bob/signals | jq .
```

Expected: `signals` list with one record showing `is_propagated: true`, `upstream_source: "alice"`.

## Step 6 — Check missing entity

```bash
curl -s -o /dev/null -w "%{http_code}" \
  http://localhost:4000/v1/inspector/entities/nonexistent
```

Expected: `404`

## Step 7 — Verify no latency regression

```bash
# Baseline before feature
ab -n 1000 -c 10 http://localhost:4000/v1/entities/alice

# After feature — same command, should show comparable p95
ab -n 1000 -c 10 http://localhost:4000/v1/entities/alice
```

Expected: p95 latency unchanged.

## Step 8 — Run tests

```bash
.venv/Scripts/python.exe -m pytest tests/ -q
```

Expected: All tests green, no new failures.
