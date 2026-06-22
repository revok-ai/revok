# Implementation Plan: Bitemporal Scoring
**Feature**: 002-bitemporal-scoring
**Branch**: feat/bitemporal-scoring
**Plan created**: 2026-06-09
**Spec**: [spec.md](spec.md)

---

## Technical Context

### Technology Stack
- Python 3.11+, `from __future__ import annotations` throughout
- `@dataclass` (not frozen) for `EntityRecord`; `@dataclass(frozen=True)` for `Signal`
- `aiosqlite` for async SQLite; WAL mode; schema via `CREATE TABLE IF NOT EXISTS`
- `pytest` + `pytest-asyncio` (asyncio_mode = "auto"); 184 tests currently passing
- `ruff` (target py311, line-length 88) + `mypy` strict — both must stay clean

### Files in Scope

| File | Change type |
|------|-------------|
| `revok/models.py` | Add `valid_time`, `transaction_time` to `EntityRecord`; `last_seen` → `@property`; add `valid_time` to `Signal` |
| `revok/scoring.py` | Switch `delta_t` from `last_seen` to `valid_time` in `score()` and `decay_at()` |
| `revok/metadata_writer.py` | Pass `valid_time` and `transaction_time` when constructing `EntityRecord`; add future-date clamp + WARNING |
| `revok/state_store.py` | DDL: add two columns; SQL reads/writes: add both fields; `_apply_decay`: pass `valid_time`; backward-compat on load |
| `revok/proxy.py` | `_handle_get_entity`: `dataclasses.asdict` now includes `valid_time` and `transaction_time` automatically (no change needed — FR-008 is free once model is updated) |
| `tests/test_scoring.py` | Update any tests that construct `EntityRecord` with positional `last_seen`; add Scenario 1/4/5/6 tests |
| `tests/test_state_store.py` | Update record construction; add backward-compat column test |
| `tests/test_metadata_writer.py` | Update record construction |
| `tests/test_proxy.py` | Update response assertions to include `valid_time`, `transaction_time` |

### Key Design Decisions (from clarifications)
- `Signal.valid_time: float | None = None` — always `None` in v0.2.0 (transport deferred)
- `EntityRecord.last_seen` → `@property` returning `valid_time` (no stored duplicate)
- Future-dated `valid_time`: clamp to `min(valid_time, transaction_time)` + WARNING log in `metadata_writer.py`
- `transaction_time ≥ valid_time`: caller contract, documented in docstring, no `__post_init__`
- `GET /v1/entities/{key}`: both fields appear automatically via `dataclasses.asdict` (no proxy change needed)

### Backward Compatibility
- `EntityRecord` is not frozen → `last_seen` property works cleanly
- SQLite: add `valid_time` and `transaction_time` columns with `ALTER TABLE … ADD COLUMN IF NOT EXISTS` fallback at `open()` time
- Records loaded without these columns → default `valid_time = last_seen_value`, `transaction_time = last_seen_value`

---

## Constitution Check

No constitution file found — proceeding with standard quality gates:
- [x] All changes confined to spec scope (no scope creep)
- [x] Backward compatibility guaranteed (FR-006)
- [x] All new behaviour covered by tests
- [x] ruff + mypy must pass
- [x] No new external dependencies introduced

---

## Phase 0: Research

All NEEDS CLARIFICATION items were resolved during specification. No external research required.

**Resolved decisions:**

| Decision | Resolution |
|----------|------------|
| valid_time transport | Deferred — `Signal.valid_time` always `None` in v0.2.0 |
| `last_seen` alias mechanism | `@property` on `EntityRecord` returning `valid_time` |
| Future-dated `valid_time` | Clamp to `transaction_time` + WARNING log |
| API response exposure | Additive — both fields in `GET /v1/entities/{key}` response |
| Invariant enforcement | Caller contract + Scenario 6 test; no `__post_init__` |

---

## Phase 1: Design & Contracts

### Data Model

#### `Signal` (revok/models.py)
```
Signal (frozen dataclass)
├── raw_content: str
├── source_id: str
├── timestamp: float
├── http_method: str
├── http_path: str
├── original_body: bytes
├── headers: dict[str, str]
└── valid_time: float | None = None   ← NEW (always None in v0.2.0)
```

#### `EntityRecord` (revok/models.py)
```
EntityRecord (mutable dataclass)
├── entity_key: str
├── score: float
├── valid_time: float              ← NEW stored field (replaces last_seen storage)
├── transaction_time: float        ← NEW stored field
├── signal_count: int
├── pattern_name: str
└── last_seen: float               ← @property → returns self.valid_time
                                      (read-only alias; not a stored field)
```

**Caller contract** (documented in docstring):
> `transaction_time ≥ valid_time` must hold. Structurally guaranteed by the
> write-time clamp in `metadata_writer.py`. Not enforced via `__post_init__`.

#### SQLite schema (revok/state_store.py)
```sql
CREATE TABLE IF NOT EXISTS entity_records (
    entity_key       TEXT    PRIMARY KEY,
    score            REAL    NOT NULL DEFAULT 0.0,
    last_seen        REAL    NOT NULL,          -- kept for backward compat; read as valid_time
    valid_time       REAL,                      -- NULL on old rows → filled from last_seen on load
    transaction_time REAL,                      -- NULL on old rows → filled from last_seen on load
    signal_count     INTEGER NOT NULL DEFAULT 1,
    pattern_name     TEXT    NOT NULL DEFAULT ''
);
```

Migration strategy: `ALTER TABLE entity_records ADD COLUMN valid_time REAL` and
`ALTER TABLE entity_records ADD COLUMN transaction_time REAL` run at `open()` time,
wrapped in try/except for `OperationalError: duplicate column name` (idempotent).

On load: `valid_time = row["valid_time"] if row["valid_time"] is not None else row["last_seen"]`

#### `GET /v1/entities/{key}` response (no proxy change)
`dataclasses.asdict(record)` already serializes all fields. Once `EntityRecord` gains
`valid_time` and `transaction_time`, they appear in the response automatically.
The `last_seen` property is **not** a dataclass field, so it won't appear — which
is the correct outcome (callers use `valid_time` directly).

### Contracts / Interface Changes

#### `ScoringEngine.score(existing, now)` — no signature change
Internal: replace `existing.last_seen` with `existing.valid_time`.

#### `ScoringEngine.decay_at(record, now)` — no signature change
Internal: replace `record.last_seen` with `record.valid_time`.

#### `metadata_writer.enrich(...)` — no signature change
Internal: pass `valid_time` and `transaction_time` when constructing `EntityRecord`.
Clamp logic:
```python
raw_valid_time = signal.valid_time if signal.valid_time is not None else now
if raw_valid_time > now:
    logger.warning(
        "valid_time %s is in the future (transaction_time=%s); clamping",
        raw_valid_time, now,
    )
    raw_valid_time = now
```

---

## Implementation Phases

### Phase 1A — models.py (foundation, no logic change)

1. Add `valid_time: float` and `transaction_time: float` to `EntityRecord` (before `signal_count`)
2. Remove `last_seen: float` stored field
3. Add `@property def last_seen(self) -> float: return self.valid_time`
4. Update `EntityRecord` docstring with caller contract
5. Add `valid_time: float | None = None` to `Signal` (last field, has default)

**Risk**: `EntityRecord` is a mutable dataclass — adding fields changes positional constructor arg order. All construction sites must be updated.

**Construction sites to update**:
- `revok/metadata_writer.py` line ~115
- `revok/state_store.py` lines ~135, ~193, ~230
- All test files constructing `EntityRecord`

### Phase 1B — scoring.py (mechanical substitution)

Replace `existing.last_seen` → `existing.valid_time` in `score()`.
Replace `record.last_seen` → `record.valid_time` in `decay_at()`.

No signature changes. Docstrings updated to reference `valid_time`.

### Phase 1C — state_store.py (persistence)

1. Add migration at `open()`:
   ```python
   for col in ("valid_time", "transaction_time"):
       try:
           await self._db.execute(
               f"ALTER TABLE entity_records ADD COLUMN {col} REAL"
           )
           await self._db.commit()
       except aiosqlite.OperationalError:
           pass  # column already exists
   ```

2. Update DDL to include both new columns (for new databases).

3. Update `get()` SELECT: add `valid_time, transaction_time` to SELECT.
   On load: use a named row factory (`conn.row_factory = aiosqlite.Row`) or resolve by column name to avoid fragile positional indices. Apply backward-compat default: `valid_time = row["valid_time"] if row["valid_time"] is not None else row["last_seen"]`.

4. Update `put()` INSERT/UPDATE: include `valid_time`, `transaction_time`.

5. Update `_apply_decay()`: construct new `EntityRecord` with `valid_time` and `transaction_time`.

6. Update `list_all()` (if present) similarly.

### Phase 1D — metadata_writer.py (pipeline wiring)

1. Capture `transaction_time = now` (already in scope as `now`).
2. Resolve `valid_time` from signal with clamp logic (above).
3. Pass both to `EntityRecord(...)` constructor.

### Phase 1E — tests (update + new)

**Update existing** (mechanical — construction sites):
- `tests/test_scoring.py`: all `EntityRecord(...)` constructions
- `tests/test_state_store.py`: all `EntityRecord(...)` constructions
- `tests/test_metadata_writer.py`: all `EntityRecord(...)` constructions
- `tests/test_proxy.py`: response body assertions (add `valid_time`, `transaction_time`)

**New tests** (`tests/test_bitemporal_scoring.py`):

| Test | Scenario | Assertion |
|------|----------|-----------|
| `test_valid_time_defaults_to_signal_timestamp` | S1 | `record.valid_time == signal.timestamp` |
| `test_transaction_time_set_to_now` | S1 | `record.transaction_time == now` |
| `test_last_seen_property_returns_valid_time` | FR-007 | `record.last_seen == record.valid_time` |
| `test_decay_uses_valid_time_not_transaction_time` | FR-002 | score differs when valid_time differs |
| `test_score_cap_recovery_unaffected` | S5 | recovery still works |
| `test_backward_compat_old_record_loads` | S4 | old row without valid_time loads correctly |
| `test_future_valid_time_is_clamped` | S6 | `record.valid_time <= record.transaction_time` |
| `test_future_valid_time_emits_warning` | S6 | WARNING log emitted |
| `test_api_response_includes_timestamps` | FR-008 | both fields in GET response JSON |
| `test_entity_record_asdict_no_last_seen_field` | FR-007 | `"last_seen"` absent from `dataclasses.asdict()` |

---

## Dependency Order

```
Phase 1A (models.py)
    ↓
Phase 1B (scoring.py)  ←→  Phase 1C (state_store.py)  ←→  Phase 1D (metadata_writer.py)
    ↓                               ↓                               ↓
Phase 1E (tests — update existing + add new)
    ↓
ruff check + mypy check
    ↓
pytest (must be ≥ 184 + 10 new = 194 passing)
```

Phases 1B, 1C, 1D can proceed in parallel once 1A is complete.

---

## Quickstart for Implementation

```bash
# Activate venv
. .venv/Scripts/activate   # or source on Linux/macOS

# Run existing tests as baseline
python -m pytest tests/ -q

# After each phase, run:
python -m ruff check revok/ tests/
python -m mypy revok/ tests/
python -m pytest tests/ -q
```

### Acceptance gate
```
184 + ≥10 new tests passing
0 ruff errors
0 mypy errors
```
