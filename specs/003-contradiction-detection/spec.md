# Contradiction Detection
**Feature ID**: 003-contradiction-detection
**Branch**: feat/contradiction-detection
**Created**: 2026-06-09
**Status**: Draft

---

## Overview

Revok currently treats every incoming signal as evidence of change and applies a uniform confidence penalty (signal_strength). This spec adds **contradiction detection**: when two or more signals for the same entity key carry conflicting values within a configurable time window, Revok applies an **additional contradiction penalty** on top of the normal signal penalty.

Contradicting signals are a stronger indicator of environmental instability than a single change signal. A contradicted entity converges back toward a normal score only once subsequent signals agree — i.e., when the contradiction window passes with no further conflicting values observed.

---

## Problem Statement

Today, two consecutive signals both degrade confidence by exactly `signal_strength`, regardless of whether they agree or disagree on the underlying value. An agent that observes `price = $500` followed immediately by `price = $450` looks the same to Revok as an agent that observes `price = $500` twice. This leads to:

1. **Under-penalizing instability** — rapid value flip-flops signal environmental chaos, but Revok treats them as routine updates
2. **No memory of conflict** — Revok cannot tell an agent "this entity is actively in conflict" vs "this entity was last updated recently"
3. **Missing signal for downstream features** — contradiction state is a first-class input for future source credibility weighting and multi-signal composite scoring (v0.3.0)

---

## Goals

- Detect conflicting values in incoming signals for the same entity key within a configurable time window
- Apply an additional contradiction penalty when conflict is detected
- Expose contradiction state in `EntityRecord` and in the `GET /v1/entities/{key}` response
- Persist contradiction state alongside existing entity fields
- Allow the system to recover from a contradicted state when subsequent signals agree

---

## Non-Goals

- This spec does not implement NLP-based semantic contradiction detection — only structural/numeric value extraction
- This spec does not add a separate contradiction history log or audit table
- This spec does not change HTTP endpoints, routes, or add new endpoints
- This spec does not implement source credibility weighting (v0.3.0)
- This spec does not change the demo examples (examples/ is out of scope)

---

## Functional Requirements

### FR-001 — EntityRecord carries contradiction state
`EntityRecord` gains two new fields:
- `contradiction_count: int` — number of contradictions detected over the entity's lifetime
- `last_contradiction_time: float | None` — Unix epoch of the most recent detected contradiction, or `None` if never contradicted

Both fields must be persisted to and loaded from the SQLite state store.

### FR-002 — Value extraction from signal content
Before scoring, Revok extracts a **canonical value fingerprint** from the signal's `raw_content`. The extractor must:
- Parse the first numeric value (integer or decimal, optionally preceded by `$`) from the content
- Normalize to a canonical float string: strip `$`, parse as `float`, format as `str(float(value))` — so `"$500"`, `"500"`, `"500.0"`, and `"$500.00"` all produce fingerprint `"500.0"`
- Fall back to a normalized lowercased hash of the entire `raw_content` string if no numeric value is found
- Return `None` if `raw_content` is empty or unparseable

The extracted fingerprint is compared against `EntityRecord.last_value_fingerprint` (the fingerprint from the prior signal) and then stored as the new `last_value_fingerprint` on the updated record.

### FR-003 — Contradiction detection window
A contradiction is detected when a new signal's value fingerprint differs from the immediately preceding signal's fingerprint **and** the `valid_time` gap between the two signals is strictly less than `contradiction_window_seconds`:

```
gap = new_signal.valid_time - existing.valid_time
contradiction_detected = (gap < contradiction_window_seconds) AND (fingerprints differ)
```

A gap equal to the window does **not** trigger contradiction (strict open interval). Configuration key: `scoring.contradiction_window_seconds` (default: `300` — 5 minutes).

### FR-004 — Contradiction penalty applied on detection
When a contradiction is detected, the scoring engine applies an **additional** `contradiction_penalty` on top of the normal `signal_strength` degradation:

```
score_new = score_recovered - signal_strength - contradiction_penalty
score_new = max(0.0, score_new)
```

The floor at `0.0` is the only bound — no additional per-signal cap is applied. If the score is already `0.0`, further contradictions still update `contradiction_count` and `last_contradiction_time`. Configuration key: `scoring.contradiction_penalty` (default: `0.15`).

### FR-005 — contradiction_count and last_contradiction_time updated on detection
When a contradiction is detected:
- `contradiction_count` is incremented by 1
- `last_contradiction_time` is set to the current signal's `valid_time`

These fields are updated in the same write transaction as the score update.

### FR-006 — No contradiction penalty when signals agree
When a new signal's value fingerprint matches the immediately preceding signal's fingerprint, no contradiction penalty is applied. The normal `signal_strength` degradation applies as before.

### FR-007 — Recovery: contradiction state does not block score recovery
Score recovery (time-based exponential decay toward `score_cap`) is unaffected by contradiction state. Contradiction state is a one-time additional penalty at write time; it does not alter the recovery formula.

### FR-008 — Backward compatibility for existing records
Records loaded from SQLite that lack `contradiction_count` or `last_contradiction_time` columns must default to `contradiction_count = 0` and `last_contradiction_time = None`. No migration step required.

### FR-009 — Expose contradiction fields in GET /v1/entities/{key}
The JSON response for `GET /v1/entities/{key}` must include `contradiction_count` and `last_contradiction_time` as additive fields:

```json
{
  "entity_key": "orion_cache",
  "score": 0.43,
  "signal_count": 6,
  "valid_time": 1749480000.0,
  "transaction_time": 1749480000.0,
  "contradiction_count": 2,
  "last_contradiction_time": 1749479800.0
}
```

### FR-010 — Configurable defaults with backward-compatible YAML
`ScoringConfig` gains two new optional fields with defaults:
- `contradiction_window_seconds: float = 300.0`
- `contradiction_penalty: float = 0.15`

Existing `revok.yaml` files that omit these keys continue to load without error.

---

## User Scenarios & Testing

### Scenario 1 — Two agreeing signals (no contradiction)
1. Agent writes memory: "Orion Cache costs $500/month" at T
2. Agent writes memory: "Orion Cache costs $500/month" at T+60s
3. Both fingerprints are `500.0`
4. **Expected**: normal signal_strength penalty applied twice; `contradiction_count = 0`

### Scenario 2 — Two conflicting signals (contradiction detected)
1. Agent writes memory: "Orion Cache costs $500/month" at T
2. Agent writes memory: "Orion Cache costs $450/month" at T+60s (within window)
3. Fingerprints differ: `500.0` vs `450.0`
4. **Expected**: second signal applies `signal_strength + contradiction_penalty`; `contradiction_count = 1`; `last_contradiction_time = T+60s`

### Scenario 3 — Conflict outside window (no contradiction penalty)
1. Agent writes memory: "Orion Cache costs $500/month" at T
2. Agent writes memory: "Orion Cache costs $450/month" at T+300s (gap == window, not strictly less)
3. **Expected**: normal signal_strength penalty only; `contradiction_count = 0`

### Scenario 4 — Score recovery after contradiction
1. Contradiction detected at T; score drops to near 0
2. No further signals arrive
3. At T+2h, `decay_at()` returns recovered score
4. **Expected**: score recovers toward `score_cap` via normal formula; `contradiction_count` unchanged; `last_contradiction_time` unchanged

### Scenario 5 — Repeated contradictions accumulate count
1. Three signals arrive within the window: $500 at T, $450 at T+30s, $500 at T+60s
2. Signal 2 vs signal 1: fingerprints differ → contradiction, penalty applied, `contradiction_count = 1`
3. Signal 3 vs signal 2: fingerprints differ → contradiction, penalty applied, `contradiction_count = 2`
4. **Expected**: `contradiction_count = 2`; each flip independently applies the contradiction penalty

### Scenario 6 — Backward-compatible load from old schema
1. State store has records with no `contradiction_count` column
2. On load: `contradiction_count = 0`, `last_contradiction_time = None`
3. **Expected**: no crash; next signal scored without contradiction penalty

### Scenario 7 — Non-numeric content (boolean/text fingerprint)
1. Agent writes: "user is authenticated" at T
2. Agent writes: "user is not authenticated" at T+30s
3. Fingerprints differ (hash-based)
4. **Expected**: contradiction detected and penalty applied

---

## Key Entities

| Entity | Fields Added | Notes |
|--------|-------------|-------|
| `EntityRecord` | `contradiction_count: int`, `last_contradiction_time: float \| None`, `last_value_fingerprint: str \| None` | All persisted to SQLite |
| `ScoringConfig` | `contradiction_window_seconds: float`, `contradiction_penalty: float` | Defaults provided |
| `ScoringEngine` | `score()` updated, `_extract_fingerprint()` added | New internal method |

---

## Success Criteria

- A contradiction between two signals within the window produces a lower final score than two non-contradicting signals would produce
- `contradiction_count` increases by exactly 1 per detected contradiction event
- Signals outside the contradiction window do not trigger the penalty regardless of value difference
- All 195 existing tests continue to pass
- ruff and mypy report zero errors or warnings
- New tests cover all 7 scenarios above

---

## Assumptions

- **Single preceding signal comparison**: contradiction is detected by comparing the new signal against the immediately prior signal only (not all signals in the window). This is the simplest correct approach given the existing single-record state model.
- **Fingerprint stored on EntityRecord**: `last_value_fingerprint: str | None` is persisted to SQLite alongside existing fields. On each new signal, the incoming fingerprint is compared directly against the stored value — O(1), no re-parsing of prior content required. Existing records without this column default to `None` (no contradiction possible on first post-upgrade signal).
- **`contradiction_window_seconds` is measured in valid_time**: consistent with bitemporal scoring, the window comparison uses `valid_time` of both signals.
- **Defaults are permissive**: 5-minute window and 0.15 penalty are conservative defaults that can be tuned per deployment.

---

## Clarifications

### Session 2026-06-09
- Q: Should the last-seen value fingerprint be stored on EntityRecord or recomputed from stored raw_content? → A: Store `last_value_fingerprint: str | None` directly on `EntityRecord` (Option A) — O(1) comparison, one new SQLite column, no raw content storage needed.
- Q: Is `contradiction_window_seconds` a strict open interval or closed interval? → A: Strict open interval — `gap < window`; a gap exactly equal to the window does not trigger contradiction.
- Q: When multiple signals flip rapidly ($500→$450→$500), does each flip count independently or is it one event? → A: Each signal-to-signal flip is an independent contradiction — two flips produce `contradiction_count += 2`.
- Q: Should near-equal numeric fingerprints like "$500", "500.0", "$500.00" be treated as the same value? → A: Yes — numeric fingerprints are normalized to canonical float string before comparison; formatting differences do not count as contradictions.
- Q: Should the contradiction penalty be capped or just floor at 0.0? → A: Floor at `0.0` only (Option A) — `score_new = max(0.0, recovered - signal_strength - contradiction_penalty)`; no additional cap; metadata fields still updated even when score is already 0.
