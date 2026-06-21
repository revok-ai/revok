# Quickstart — Contradiction Detection (003)
**Feature**: 003-contradiction-detection
**Date**: 2026-06-09

---

## What This Feature Does

Revok now detects when two signals for the same entity carry conflicting values within a
configurable time window and applies an **additional penalty** to the entity's confidence
score. This makes Revok more sensitive to environmental instability (e.g., a price rapidly
flip-flopping between $500 and $450).

---

## Configuration

Add two optional keys to the `scoring` section of `revok.yaml`:

```yaml
scoring:
  half_life_seconds: 3600
  signal_strength: 0.2
  score_cap: 1.0
  contradiction_window_seconds: 300.0   # default: 5 minutes
  contradiction_penalty: 0.15           # default; applied on top of signal_strength
```

Existing configs without these keys continue to work — defaults are applied automatically.

---

## Reading Contradiction State

The `GET /v1/entities/{entity_key}` response now includes two new fields:

```bash
curl http://localhost:8080/v1/entities/orion_cache
```

```json
{
  "entity_key": "orion_cache",
  "score": 0.43,
  "signal_count": 6,
  "valid_time": 1749480000.0,
  "transaction_time": 1749480000.1,
  "pattern_name": "product",
  "contradiction_count": 2,
  "last_contradiction_time": 1749479800.0
}
```

- **`contradiction_count`**: total contradictions ever detected for this entity
- **`last_contradiction_time`**: epoch of the most recent contradiction (`null` if none)

---

## How Scoring Changes

| Scenario | Score formula |
|----------|---------------|
| Normal signal | `score = recovered - signal_strength` |
| Contradicting signal (within window) | `score = recovered - signal_strength - contradiction_penalty` |
| Recovery (no new signals) | `score → score_cap` via exponential decay (unchanged) |

---

## Example: Price Flip Detected

```
T+0s:   "Orion Cache costs $500/month"  → fingerprint "500.0"  → score = 0.80
T+60s:  "Orion Cache costs $450/month"  → fingerprint "450.0"  → contradiction!
        gap (60s) < window (300s), fingerprints differ
        score = recovered(0.80) - 0.20 - 0.15 = ~0.45
        contradiction_count = 1, last_contradiction_time = T+60s

T+120s: "Orion Cache costs $500/month"  → fingerprint "500.0"  → contradiction again!
        score = recovered(0.45) - 0.20 - 0.15 = ~0.10
        contradiction_count = 2
```

---

## Upgrading from a Prior Version

No manual migration needed. On first startup after upgrade, Revok automatically runs
`ALTER TABLE … ADD COLUMN` for the three new columns. Existing entities default to
`contradiction_count = 0` and `null` for the other two fields.
