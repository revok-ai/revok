# Bitemporal Scoring
**Feature ID**: 002-bitemporal-scoring
**Branch**: feat/bitemporal-scoring
**Created**: 2026-06-09
**Status**: Draft

---

## Overview

Revok currently scores entity confidence using a single timestamp (`last_seen`) that conflates two distinct moments: when the real-world fact was true, and when Revok first observed it. This spec adds **bitemporal tracking** to the scoring engine by separating those two timestamps.

- **valid_time** — when the fact described in the signal was actually true in the real world (carried by the signal itself, defaulting to signal arrival time if not provided)
- **transaction_time** — when Revok recorded the signal (always wall-clock time at ingestion)

Score decay is driven exclusively by `valid_time` age, not `transaction_time`. This allows Revok to correctly handle late-arriving signals, backdated memories, and time-aware re-scoring without corrupting the audit trail of when facts were observed.

---

## Problem Statement

Today, if an agent writes a memory about an event that happened 3 days ago, Revok treats the signal as if the event just occurred. Decay starts from now, not from when the fact was true. This causes:

1. **Stale facts appear fresh** — a backdated signal resets decay as if the entity was just seen
2. **No audit trail** — there is no way to distinguish "recorded now, happened now" from "recorded now, happened last week"
3. **Future enrichment is blocked** — contradiction detection (v0.2.0) and read-path enrichment need valid_time to reason about temporal overlap

---

## Goals

- Track both `valid_time` and `transaction_time` on every entity record
- Decay confidence based on `valid_time` age only
- Default `valid_time` to signal arrival time when not explicitly provided
- Preserve backward compatibility — existing records without `valid_time` continue to function

---

## Non-Goals

- This spec does not implement contradiction detection (separate feature)
- This spec does not change request paths, HTTP methods, or introduce new endpoints (response body additions in FR-008 are additive)
- This spec does not add UI or dashboard changes
- This spec does not add valid_time extraction from signal content (that is part of read-path enrichment)
- This spec does not add a transport mechanism for callers to supply `valid_time` — `Signal.valid_time` is always `None` in v0.2.0; the field exists for future use only

---

## Functional Requirements

### FR-001 — EntityRecord stores both timestamps
`EntityRecord` must have two timestamp fields:
- `valid_time: float` — Unix epoch seconds; when the fact was true
- `transaction_time: float` — Unix epoch seconds; when Revok recorded it

Both fields must be persisted to and loaded from the SQLite state store.

### FR-002 — Decay is anchored to valid_time
`ScoringEngine.score()` and `ScoringEngine.decay_at()` must compute elapsed time using `valid_time`, not `transaction_time` (which was previously `last_seen`).

### FR-003 — valid_time defaults to signal arrival time
When a signal does not carry an explicit valid_time, `valid_time` is set equal to `Signal.timestamp` (the wall-clock time the proxy received the request). This preserves current behaviour for all existing integrations.

### FR-004 — transaction_time is always wall-clock
`transaction_time` is always set to the time Revok processed the signal, regardless of what valid_time claims. It is never overridden by signal content.

### FR-005 — Signal carries optional valid_time (reserved field)
`Signal` gains an optional `valid_time: float | None = None` field. In v0.2.0 this field is always `None` — no transport mechanism (header, body parsing) sets it yet. The pipeline always falls back to FR-003. The field exists so future features (read-path enrichment) can populate it without a breaking model change.

### FR-006 — Backward compatibility for existing records
Records loaded from SQLite that lack `valid_time` (i.e., written by a prior version) must default `valid_time = last_seen` so decay continues working without a migration step.

### FR-007 — last_seen is a property alias for valid_time
`EntityRecord.last_seen` is replaced with a `@property` that returns `valid_time`. It is not stored as a separate field. All existing callers that read `last_seen` continue to work without modification. The property is read-only; callers that previously set `last_seen` must be updated to set `valid_time` instead.

### FR-008 — Expose timestamps in GET /v1/entities/{key} response
The JSON response for `GET /v1/entities/{key}` must include `valid_time` and `transaction_time` alongside the existing `score` and `signal_count` fields. This is an additive, non-breaking change. Field names use snake_case Unix epoch floats consistent with the rest of the API.

```json
{
  "entity_key": "alice",
  "score": 0.72,
  "signal_count": 4,
  "valid_time": 1749480000.0,
  "transaction_time": 1749480000.0
}
```

---

## User Scenarios & Testing

### Scenario 1 — Normal signal (no explicit valid_time)
1. An agent writes a memory at time T
2. Revok receives the signal; `valid_time = T`, `transaction_time = T`
3. At time T+1h, `decay_at()` returns score based on 1h of elapsed valid_time
4. **Expected**: behaviour identical to current system

### Scenario 2 — Backdated signal (valid_time in the past) *(future — requires transport)*
> Transport deferred to read-path enrichment. Covered here for design completeness only; not tested in v0.2.0.
1. An agent writes a memory at time T about an event from T-3d
2. Signal carries `valid_time = T - 3 days`
3. Revok records `valid_time = T-3d`, `transaction_time = T`
4. `score()` decays based on 3 days of elapsed time, not 0
5. **Expected**: score is lower than a fresh signal; reflects the actual age of the fact

### Scenario 3 — Late-arriving signal (transaction_time > valid_time) *(future — requires transport)*
> Transport deferred to read-path enrichment. Covered here for design completeness only; not tested in v0.2.0.
1. An agent was offline for 2 days; it writes a memory now about something observed 2 days ago
2. `valid_time = now - 2d`, `transaction_time = now`
3. **Expected**: score computed as if the signal arrived 2 days ago; audit trail shows it was actually recorded today

### Scenario 4 — Existing records loaded from old schema
1. State store contains records with no `valid_time` column
2. On load, `valid_time` defaults to `last_seen`
3. **Expected**: no crash, no migration needed, decay continues as before

### Scenario 6 — Future-dated valid_time (edge case)
1. A signal arrives with `valid_time` set to a timestamp in the future
2. Revok clamps `valid_time` to `min(valid_time, transaction_time)` before persisting
3. A `WARNING` log entry is emitted containing the original and clamped values
4. **Expected**: no crash; score behaves as if the fact arrived now; the anomaly is visible in logs

### Scenario 5 — score_cap recovery still works
1. Entity has a low score from 10 signals
2. No new signals arrive for 7 days (valid_time stays fixed at last signal's valid_time)
3. `decay_at()` returns a score approaching `score_cap`
4. **Expected**: recovery is unaffected by the bitemporal change

---

## Success Criteria

- All existing 184 tests continue to pass without modification
- New unit tests cover all 5 scenarios above (at minimum 10 new tests)
- A backdated signal with `valid_time = now - 3 days` produces a lower score than an identical signal with `valid_time = now` *(deferred to read-path enrichment feature — requires valid_time transport; not tested in v0.2.0)*
- `transaction_time` is always ≥ `valid_time` is enforced or documented as a caller contract
- ruff and mypy report zero errors after the change
- Schema migration is not required — old records load without error

---

## Key Entities

| Entity | Change |
|--------|--------|
| `Signal` | Add `valid_time: float \| None = None` |
| `EntityRecord` | Add `valid_time: float`, `transaction_time: float`; keep `last_seen` as alias |
| `ScoringEngine.score()` | Use `valid_time` for delta_t, accept it as a parameter |
| `ScoringEngine.decay_at()` | Use `valid_time` for delta_t |
| `StateStore` | Persist and load both new fields; handle missing columns gracefully |

---

## Clarifications

### Session 2026-06-09
- Q: How is `valid_time` transported into `Signal` in v0.2.0? → A: `Signal.valid_time` is always `None` in v0.2.0; the field is reserved but no transport mechanism (header, body parsing) is added. Transport deferred to read-path enrichment feature.
- Q: How is `last_seen` handled on `EntityRecord`? → A: Replace the stored field with a `@property` returning `valid_time` — one source of truth, no duplicate storage, no caller changes needed for reads.
- Q: What happens when `valid_time` is in the future (greater than `transaction_time`)? → A: Clamp `valid_time` to `min(valid_time, transaction_time)` at write time and emit a `WARNING` log with the original and clamped values. No crash, no silent wrong score.
- Q: Should `valid_time` and `transaction_time` be exposed in the `GET /v1/entities/{key}` response? → A: Yes — add both fields to the response body as additive non-breaking keys (FR-008).
- Q: How is the `transaction_time ≥ valid_time` invariant enforced? → A: Document as a caller contract in `EntityRecord` docstring; rely on the write-time clamp (FR-003/Scenario 6) to structurally maintain it; verify via the Scenario 6 test. No `__post_init__` assertion.

---

## Assumptions

1. `valid_time` is expressed as a Unix epoch float (seconds), consistent with all existing timestamps in the codebase
2. Callers that need to pass a backdated valid_time will do so explicitly; the proxy defaults to `Signal.timestamp` when the field is absent
3. No existing test hard-codes a specific score value that would change under this refactor — if any do, they will be updated as part of this feature
4. The SQLite schema change (adding two columns) does not require a formal migration tool; `IF NOT EXISTS` on new columns is sufficient for the backward-compatibility requirement
5. **Caller contract**: `transaction_time ≥ valid_time` must hold for every `EntityRecord`. This is structurally guaranteed by the write-time clamp in Scenario 6. It is documented in the `EntityRecord` docstring but not enforced via `__post_init__`.
