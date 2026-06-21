# Tasks: Bitemporal Scoring
**Feature**: 002-bitemporal-scoring
**Branch**: feat/bitemporal-scoring
**Spec**: [spec.md](spec.md) · **Plan**: [plan.md](plan.md) · **Data model**: [data-model.md](data-model.md)

---

## Dependencies

```
Phase 1 (Setup)
    ↓
Phase 2 (Foundational — models.py)
    ↓
Phase 3 (US1 — Scoring + pipeline) ←→ Phase 4 (US2 — Persistence)
    ↓                                        ↓
Phase 5 (US3 — API response test)
    ↓
Phase 6 (Update existing tests + new bitemporal tests)
    ↓
Phase 7 (Polish — quality gates)
```

Phases 3 and 4 can proceed in parallel once Phase 2 is complete.

---

## Phase 1 — Setup

- [X] T001 Verify baseline: run `python -m pytest tests/ -q` and confirm 184 tests pass

---

## Phase 2 — Foundational: EntityRecord & Signal model changes

> **Story goal**: `EntityRecord` stores `valid_time` and `transaction_time` as authoritative fields; `last_seen` becomes a read-only property alias. `Signal` carries an optional `valid_time` reserved field.
>
> **FR-001, FR-005, FR-007**

- [X] T002 Remove `last_seen: float` stored field from `EntityRecord` in `revok/models.py`
- [X] T003 Add `valid_time: float` field to `EntityRecord` before `signal_count` in `revok/models.py`
- [X] T004 Add `transaction_time: float` field to `EntityRecord` before `signal_count` in `revok/models.py`
- [X] T005 Add `@property def last_seen(self) -> float: return self.valid_time` to `EntityRecord` in `revok/models.py`
- [X] T006 Update `EntityRecord` docstring with field descriptions and caller contract (`transaction_time >= valid_time`) in `revok/models.py`
- [X] T007 [P] Add `valid_time: float | None = None` as the last field of `Signal` (frozen dataclass, must have default) in `revok/models.py`

**Independent test**: `dataclasses.asdict(EntityRecord(...))` contains `valid_time` and `transaction_time` but NOT `last_seen`; `record.last_seen == record.valid_time`.

---

## Phase 3 — US1: Scoring engine and pipeline use valid_time

> **Story goal**: Decay is anchored to `valid_time`; future-dated `valid_time` is clamped at write time with a WARNING log; `transaction_time` is always wall-clock.
>
> **FR-002, FR-003, FR-004, Scenario 1, Scenario 6**

- [X] T008 [US1] Replace `existing.last_seen` with `existing.valid_time` in `ScoringEngine.score()` in `revok/scoring.py`
- [X] T009 [US1] Replace `record.last_seen` with `record.valid_time` in `ScoringEngine.decay_at()` in `revok/scoring.py`
- [X] T010 [US1] Update docstrings for `score()` and `decay_at()` to reference `valid_time` in `revok/scoring.py`
- [X] T011 [US1] Add clamp logic to `metadata_writer.enrich()`: resolve `raw_valid_time` from `signal.valid_time or now`; if `raw_valid_time > now`, clamp to `now` and emit `logger.warning(...)` in `revok/metadata_writer.py`
- [X] T012 [US1] Pass `valid_time=raw_valid_time` and `transaction_time=now` when constructing `EntityRecord` in `metadata_writer.enrich()` in `revok/metadata_writer.py`

**Independent test**: An entity written with `valid_time = now - 3*24*3600` produces a lower `decay_at(record, now)` score than one written with `valid_time = now`.

---

## Phase 4 — US2: Persistence reads and writes both timestamps

> **Story goal**: SQLite schema includes `valid_time` and `transaction_time`; existing rows without these columns load correctly by defaulting to `last_seen`.
>
> **FR-001, FR-006**

- [X] T013 [P] [US2] Update DDL constant `_DDL` to include `valid_time REAL` and `transaction_time REAL` columns in `revok/state_store.py`
- [X] T014 [US2] Add idempotent migration in `SqliteStateStore.open()` that runs `ALTER TABLE entity_records ADD COLUMN valid_time REAL` and `ALTER TABLE entity_records ADD COLUMN transaction_time REAL` (catch `aiosqlite.OperationalError` to skip if column already exists) in `revok/state_store.py`
- [X] T015 [US2] Update `get()` SELECT to include `valid_time, transaction_time`; use positional indices or named row factory; apply backward-compat default (`val if val is not None else last_seen_val`) when constructing `EntityRecord` in `revok/state_store.py`
- [X] T016 [US2] Update `put()` INSERT/UPSERT to include `valid_time` and `transaction_time` in column list, VALUES, and DO UPDATE SET clause in `revok/state_store.py`
- [X] T017 [US2] Update `_apply_decay()` to pass `valid_time=record.valid_time` and `transaction_time=record.transaction_time` when constructing the decayed `EntityRecord` copy in `revok/state_store.py`
- [X] T018 [P] [US2] Check for `list_all()` or any other SELECT in `revok/state_store.py` and update it the same way as `get()` if present

**Independent test**: Insert a row without `valid_time`/`transaction_time` columns via raw SQL, then call `store.get(key)` — must return a valid `EntityRecord` with `valid_time == legacy_last_seen`.

---

## Phase 5 — US3: API response exposes both timestamps

> **Story goal**: `GET /v1/entities/{key}` response JSON includes `valid_time` and `transaction_time`; `last_seen` is absent because it is a property (not a dataclass field).
>
> **FR-008**

- [X] T019 [P] [US3] Verify in `revok/proxy.py` that `_handle_get_entity` uses `dataclasses.asdict(record)` — no code change needed; document in a comment if desired

**Independent test**: HTTP GET response body contains `valid_time` and `transaction_time`; `last_seen` key is absent.

---

## Phase 6 — Update existing tests + new bitemporal tests

> Update all construction sites broken by the `EntityRecord` field order change, then add the 10 new bitemporal tests.

### 6a — Update existing tests (mechanical — construction sites)

- [X] T020 [P] Update all `EntityRecord(...)` constructions in `tests/test_scoring.py` to pass `valid_time=` and `transaction_time=` instead of `last_seen=`
- [X] T021 [P] Update all `EntityRecord(...)` constructions in `tests/test_state_store.py` to pass `valid_time=` and `transaction_time=` instead of `last_seen=`
- [X] T022 [P] Update all `EntityRecord(...)` constructions in `tests/test_metadata_writer.py` to pass `valid_time=` and `transaction_time=` instead of `last_seen=`
- [X] T023 [P] Update response body assertions in `tests/test_proxy.py` that check for `last_seen` — change to assert `valid_time` and `transaction_time` are present instead

### 6b — New test file

- [X] T024 Create `tests/test_bitemporal_scoring.py` with the following 11 tests:
  - `test_entity_record_has_valid_time_field` — `valid_time` is a stored dataclass field
  - `test_entity_record_has_transaction_time_field` — `transaction_time` is a stored dataclass field
  - `test_last_seen_property_returns_valid_time` — `record.last_seen == record.valid_time` (FR-007)
  - `test_asdict_excludes_last_seen` — `"last_seen"` not in `dataclasses.asdict(record)` (FR-007)
  - `test_decay_uses_valid_time_not_transaction_time` — two records with same `transaction_time` but different `valid_time` produce different `decay_at()` scores (FR-002)
  - `test_normal_signal_valid_time_equals_timestamp` — `record.valid_time == signal.timestamp` after `enrich()` with no explicit `valid_time` (Scenario 1)
  - `test_transaction_time_set_to_now` — `record.transaction_time` approximately equals `now` after `enrich()`; assert `abs(record.transaction_time - now) < 1.0` (Scenario 1)
  - `test_future_valid_time_is_clamped` — `record.valid_time <= record.transaction_time` when signal carries a future `valid_time` (Scenario 6)
  - `test_future_valid_time_emits_warning` — `logger.warning` is called when `valid_time > transaction_time` (Scenario 6)
  - `test_backward_compat_old_record_loads` — row inserted without `valid_time`/`transaction_time` columns loads with `valid_time == last_seen` (Scenario 4)
  - `test_score_cap_recovery_unaffected` — `decay_at(record, now + 7*86400)` on an entity with `valid_time = now` approaches `score_cap`; asserts decay behaviour is unchanged by the bitemporal refactor (Scenario 5)

---

## Phase 7 — Polish & quality gates

- [X] T025 Run `python -m ruff check revok/ tests/` and fix any issues
- [X] T026 Run `python -m mypy revok/ tests/` and fix any type errors
- [X] T027 Run `python -m pytest tests/ -q` and confirm ≥ 195 tests pass (184 existing + 11 new)
- [X] T028 [P] Update `TASKS.md` — mark "Bitemporal scoring" item `[x]` in the v0.2.0 section

---

## Parallel Execution Summary

Tasks marked `[P]` within the same phase can be executed simultaneously:

| Phase | Parallel group |
|-------|---------------|
| 2 | T007 (Signal field) alongside T002–T006 (EntityRecord changes) |
| 3+4 | Entire Phase 3 alongside entire Phase 4 (different files, no cross-deps) |
| 5 | T019 in parallel with Phase 6 |
| 6a | T020, T021, T022, T023 all independent — different test files |
| 7 | T025, T026, T028 can run after T027 confirms green |

---

## MVP Scope

**Phase 2 + Phase 3 (T001–T012)** is the minimum viable slice:
- Model carries both timestamps ✓
- Decay uses valid_time ✓
- Future-date clamp works ✓

Persistence (Phase 4) and API exposure (Phase 5) complete the feature; tests (Phase 6–7) validate everything.

---

## Acceptance Criteria

- [X] `python -m pytest tests/ -q` → ≥ 195 passing, 0 failed
- [X] `python -m ruff check revok/ tests/` → 0 errors
- [X] `python -m mypy revok/ tests/` → 0 errors
- [X] `dataclasses.asdict(EntityRecord(...))` contains `valid_time`, `transaction_time`; does NOT contain `last_seen`
- [X] `GET /v1/entities/{key}` response contains `valid_time`, `transaction_time`; does NOT contain `last_seen`
- [X] Old SQLite row without `valid_time` column loads without error
