# Implementation Plan — Contradiction Detection (003)
**Feature**: 003-contradiction-detection
**Branch**: feat/contradiction-detection
**Date**: 2026-06-09
**Status**: Planning complete

---

## Technical Context

- **Python**: 3.11+, `from __future__ import annotations` throughout
- **Test runner**: pytest + pytest-asyncio, `asyncio_mode = "auto"`, 195 passing tests on develop
- **Type checker**: mypy strict (0 errors required)
- **Linter**: ruff (0 errors required)
- **Database**: aiosqlite 0.20, SQLite WAL mode
- **Constraint**: All 195 existing tests must continue passing; 0 ruff/mypy errors throughout

## Constitution Check

| Gate | Status | Notes |
|------|--------|-------|
| All tests green before merging | PASS | 195 tests on develop |
| ruff clean | PASS | enforced at merge |
| mypy strict clean | PASS | enforced at merge |
| No new endpoints added | PASS | GET /v1/entities/{key} is additive only |
| Backward compat preserved | PASS | all new fields have defaults; ALTER TABLE auto-migration |
| No breaking API changes | PASS | response is additive |

---

## Implementation Phases

### T001 — Extend `ScoringConfig` with contradiction parameters
**File**: `revok/config.py`
- Add `contradiction_window_seconds: float = 300.0` after `score_cap`
- Add `contradiction_penalty: float = 0.15` after `contradiction_window_seconds`
- Verify YAML loader passes through (or add to `_build_scoring_config` helper)
- **Tests affected**: `tests/test_config.py` — add tests for defaults and explicit values

### T002 — Add new fields to `EntityRecord`
**File**: `revok/models.py`
- Append `contradiction_count: int = 0` after `pattern_name`
- Append `last_contradiction_time: float | None = None`
- Append `last_value_fingerprint: str | None = None`
- **Tests affected**: `tests/test_models.py` — verify default field values

### T003 — Add `_extract_fingerprint` static method to `ScoringEngine`
**File**: `revok/scoring.py`
- Import `hashlib` and `re` at module level
- Add `@staticmethod _extract_fingerprint(raw_content: str) -> str | None`
- Algorithm: regex match → `str(float(group))` → sha256 fallback → `None` if empty
- **Tests affected**: `tests/test_scoring.py` — add unit tests for all branches

### T004 — Add `detect_contradiction` instance method to `ScoringEngine`
**File**: `revok/scoring.py`
- Store `_contradiction_window_seconds` and `_contradiction_penalty` in `__init__`
- Add `detect_contradiction(existing, new_fingerprint, new_valid_time) -> bool`
- Conditions: existing not None, both fingerprints not None, differ, gap < window
- **Tests affected**: `tests/test_scoring.py` — add unit tests for boundary conditions

### T005 — Extend `ScoringEngine.score()` with `is_contradiction` parameter
**File**: `revok/scoring.py`
- Add keyword-only `is_contradiction: bool = False` to `score()` signature
- When `True`: `score_new -= contradiction_penalty` before floor
- All existing call sites unchanged (keyword-only default)
- **Tests affected**: `tests/test_scoring.py` — add tests for penalty branch

### T006 — Extend SQLite schema in `SqliteStateStore.open()`
**File**: `revok/state_store.py`
- Extend the ALTER TABLE loop to include the three new columns
- Column specs: `contradiction_count INTEGER NOT NULL DEFAULT 0`, 
  `last_contradiction_time REAL`, `last_value_fingerprint TEXT`
- **Tests affected**: `tests/test_state_store.py` — verify new columns round-trip

### T007 — Update `SqliteStateStore.get()` for new columns
**File**: `revok/state_store.py`
- Extend SELECT to include `contradiction_count`, `last_contradiction_time`,
  `last_value_fingerprint` (positions 7, 8, 9)
- Update `EntityRecord` construction with new fields
- Backward compat: `row[7] if row[7] is not None else 0`
- **Tests affected**: `tests/test_state_store.py`

### T008 — Update `SqliteStateStore.list_all()` for new columns
**File**: `revok/state_store.py`
- Extend SELECT and `EntityRecord` construction same as T007
- **Tests affected**: `tests/test_state_store.py`

### T009 — Update `SqliteStateStore.put()` for new columns
**File**: `revok/state_store.py`
- Extend INSERT column list and VALUES tuple
- Extend ON CONFLICT UPDATE SET for the three new columns
- **Tests affected**: `tests/test_state_store.py`

### T010 — Update `metadata_writer.enrich()` to detect and record contradictions
**File**: `revok/metadata_writer.py`
- In the `for entity in entities:` loop:
  1. Extract fingerprint: `ScoringEngine._extract_fingerprint(signal.raw_content)`
  2. Detect: `scorer.detect_contradiction(existing, new_fingerprint, raw_valid_time)`
  3. Score: `scorer.score(existing, now, is_contradiction=is_contradiction)`
  4. Build contradiction state fields conditionally
  5. Pass all 3 new fields to `EntityRecord(...)` constructor
- **Tests affected**: `tests/test_metadata_writer.py`

### T011 — Update `proxy._handle_get_entity` to strip `last_value_fingerprint`
**File**: `revok/proxy.py`
- After `data = dataclasses.asdict(record)`, add:
  `data.pop("last_value_fingerprint", None)`
- **Tests affected**: `tests/test_proxy.py` — verify field absent from response,
  verify `contradiction_count` and `last_contradiction_time` present

### T012 — Write `tests/test_contradiction_detection.py`
**File**: `tests/test_contradiction_detection.py` (new)
- 7 integration scenarios from spec, using real `ScoringEngine` + SQLite `:memory:`
- Each scenario tests both the final score AND the contradiction_count/last_contradiction_time

### T013 — Final validation
- Run `pytest` — all 195 + new tests must pass
- Run `ruff check revok/ tests/` — 0 errors
- Run `mypy revok/` — 0 errors

---

## File Change Summary

| File | Change Type | Tasks |
|------|------------|-------|
| `revok/config.py` | Modify | T001 |
| `revok/models.py` | Modify | T002 |
| `revok/scoring.py` | Modify | T003, T004, T005 |
| `revok/state_store.py` | Modify | T006, T007, T008, T009 |
| `revok/metadata_writer.py` | Modify | T010 |
| `revok/proxy.py` | Modify | T011 |
| `tests/test_contradiction_detection.py` | Create | T012 |
| `tests/test_config.py` | Modify | T001 |
| `tests/test_models.py` | Modify | T002 |
| `tests/test_scoring.py` | Modify | T003, T004, T005 |
| `tests/test_state_store.py` | Modify | T006–T009 |
| `tests/test_metadata_writer.py` | Modify | T010 |
| `tests/test_proxy.py` | Modify | T011 |

---

## Design Artifacts

- [spec.md](spec.md) — full feature specification
- [research.md](research.md) — technical decisions and rationale
- [data-model.md](data-model.md) — entity/field model and state transitions
- [contracts/api.md](contracts/api.md) — API contract for GET /v1/entities/{key}
- [quickstart.md](quickstart.md) — user-facing usage guide
