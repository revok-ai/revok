# Tasks — Contradiction Detection (003)
**Feature**: 003-contradiction-detection
**Branch**: feat/contradiction-detection
**Date**: 2026-06-09
**Total tasks**: 27 | **User stories**: 4 | **Parallel opportunities**: 10

---

## User Stories

| ID | Story | Priority | Independent Test Criterion |
|----|-------|----------|---------------------------|
| US1 | Scoring engine detects contradictions and applies penalty | P1 | `ScoringEngine.score(existing, now, is_contradiction=True)` returns a lower score than `is_contradiction=False` given identical inputs; `_extract_fingerprint` normalizes numeric values; `detect_contradiction` returns `True` within window |
| US2 | State store persists contradiction fields for every entity | P1 | `put(record)` with `contradiction_count=2` → `get()` returns `contradiction_count=2`; old-schema records load with `contradiction_count=0` |
| US3 | Enrichment pipeline wires detection end-to-end on write | P1 | Two `enrich()` calls with conflicting content within window produce `contradiction_count=1` and a lower score than two agreeing signals |
| US4 | GET /v1/entities/{key} exposes contradiction state | P1 | Response body contains `contradiction_count` and `last_contradiction_time`; does **not** contain `last_value_fingerprint` |

---

## Phase 1 — Setup

> Verify baseline before any changes.

- [X] T001 Confirm 195 tests pass on feat/contradiction-detection: `python -m pytest --tb=short -q`
- [X] T002 Confirm ruff and mypy are clean on develop baseline: `ruff check revok/ tests/ && mypy revok/`

---

## Phase 2 — Foundational (blocking all user stories)

> Config and model changes block all downstream phases. Must be complete before US1–US4.

- [X] T003 Add `contradiction_window_seconds: float = 300.0` and `contradiction_penalty: float = 0.15` fields to `ScoringConfig` in `revok/config.py` after `score_cap`
- [X] T004 Add `contradiction_count: int = 0`, `last_contradiction_time: float | None = None`, and `last_value_fingerprint: str | None = None` fields to `EntityRecord` in `revok/models.py` after `pattern_name`
- [X] T005 [P] Update `VALID_YAML` fixture in `tests/test_config.py` and add test `test_scoring_config_contradiction_defaults` asserting `ScoringConfig` defaults and `test_scoring_config_contradiction_explicit` asserting explicit YAML values load correctly
- [X] T006 [P] Add `test_entity_record_contradiction_defaults` in `tests/test_models.py` asserting `EntityRecord(entity_key="x", score=0.5, valid_time=1.0, transaction_time=1.0, signal_count=1, pattern_name="p").contradiction_count == 0` and both new fields default to `None`

---

## Phase 3 — User Story 1: Scoring engine contradiction detection

> **Goal**: `ScoringEngine` can extract a canonical fingerprint from signal content, detect whether two consecutive signals contradict within the time window, and apply an additional penalty when they do.

**Independent test criteria**: `ScoringEngine._extract_fingerprint("$500/month") == "500.0"`; `detect_contradiction(existing, "450.0", existing.valid_time + 60) == True`; `score(existing, now, is_contradiction=True) < score(existing, now, is_contradiction=False)`

- [X] T007 [US1] Add `import hashlib` and `import re` to `revok/scoring.py` imports section
- [X] T008 [US1] Add `@staticmethod _extract_fingerprint(raw_content: str) -> str | None` to `ScoringEngine` in `revok/scoring.py`: regex `r'\$?\s*(\d+(?:\.\d+)?)'` → `str(float(group))`; fallback to `hashlib.sha256(raw_content.lower().encode()).hexdigest()`; return `None` if `raw_content` is empty
- [X] T009 [US1] Store `self._contradiction_window_seconds = config.contradiction_window_seconds` and `self._contradiction_penalty = config.contradiction_penalty` in `ScoringEngine.__init__` in `revok/scoring.py`
- [X] T010 [US1] Add `detect_contradiction(self, existing: EntityRecord | None, new_fingerprint: str | None, new_valid_time: float) -> bool` to `ScoringEngine` in `revok/scoring.py`: return `True` iff `existing is not None`, both fingerprints not `None`, fingerprints differ, and `(new_valid_time - existing.valid_time) < self._contradiction_window_seconds`
- [X] T011 [US1] Add keyword-only `*, is_contradiction: bool = False` parameter to `ScoringEngine.score()` in `revok/scoring.py`; when `True`, apply `score_new -= self._contradiction_penalty` before the `max(0.0, ...)` floor
- [X] T012 [P] [US1] Add `test_extract_fingerprint_dollar_price`, `test_extract_fingerprint_plain_decimal`, `test_extract_fingerprint_no_numeric_uses_hash`, `test_extract_fingerprint_empty_returns_none`, and `test_extract_fingerprint_normalizes_formatting` tests to `tests/test_scoring.py`
- [X] T013 [P] [US1] Add `test_detect_contradiction_within_window`, `test_detect_contradiction_at_window_boundary_no_contradiction`, `test_detect_contradiction_outside_window`, `test_detect_contradiction_agreeing_fingerprints`, and `test_detect_contradiction_no_existing_record` tests to `tests/test_scoring.py`
- [X] T014 [P] [US1] Add `test_score_with_contradiction_penalty_lower_than_without` and `test_score_contradiction_floors_at_zero` tests to `tests/test_scoring.py`; update `engine` fixture to include `contradiction_window_seconds=300.0` and `contradiction_penalty=0.15` in `ScoringConfig`

---

## Phase 4 — User Story 2: Persistence layer stores contradiction fields

> **Goal**: `SqliteStateStore` persists and loads the three new `EntityRecord` fields. Existing databases auto-migrate on `open()`. Old records load with safe defaults.

**Independent test criteria**: `put(record_with_contradiction_count_2)` → `get()` returns `contradiction_count == 2`; `list_all()` includes contradiction fields; a store opened on a DB without the new columns loads records with `contradiction_count == 0`

- [X] T015 [US2] Extend the `ALTER TABLE` migration loop in `SqliteStateStore.open()` in `revok/state_store.py` to also add `contradiction_count INTEGER NOT NULL DEFAULT 0`, `last_contradiction_time REAL`, and `last_value_fingerprint TEXT` columns
- [X] T016 [US2] Update the `SELECT` statement and `EntityRecord` construction in `SqliteStateStore.get()` in `revok/state_store.py` to include columns at positions 7, 8, 9; use `row[7] if row[7] is not None else 0` for `contradiction_count`
- [X] T017 [US2] Update the `SELECT` statement and `EntityRecord` construction in `SqliteStateStore.list_all()` in `revok/state_store.py` to include the same three new columns
- [X] T018 [US2] Extend the `INSERT … ON CONFLICT … DO UPDATE` in `SqliteStateStore.put()` in `revok/state_store.py` to include `contradiction_count`, `last_contradiction_time`, and `last_value_fingerprint` in both the column list and `UPDATE SET` clause
- [X] T019 [P] [US2] Add `test_contradiction_fields_round_trip`, `test_contradiction_fields_default_to_zero_on_new_record`, and `test_list_all_includes_contradiction_fields` tests to `tests/test_state_store.py`; update `make_record` helper to accept optional contradiction kwargs
- [X] T020 [P] [US2] Add `test_backward_compat_old_schema_loads_with_zero_count` test to `tests/test_state_store.py` that creates a raw SQLite DB without the new columns, opens a `SqliteStateStore` on it, inserts a legacy row directly via `aiosqlite`, then calls `get()` and asserts `contradiction_count == 0`

---

## Phase 5 — User Story 3: Enrichment pipeline wires detection end-to-end

> **Goal**: `metadata_writer.enrich()` extracts a fingerprint, calls `scorer.detect_contradiction()`, passes `is_contradiction` to `scorer.score()`, and stores the resulting contradiction fields on the `EntityRecord`.

**Independent test criteria**: Two `enrich()` calls with `"price is $500"` then `"price is $450"` within 60s produce `contradiction_count == 1`, `last_contradiction_time` set, and a lower score than two identical signals; `last_value_fingerprint` is stored on the record but not leaked in the payload

- [X] T021 [US3] Update the `for entity in entities:` loop in `revok/metadata_writer.py`: (1) call `ScoringEngine._extract_fingerprint(signal.raw_content)` for `new_fingerprint`; (2) call `scorer.detect_contradiction(existing, new_fingerprint, raw_valid_time)` for `is_contradiction`; (3) pass `is_contradiction=is_contradiction` to `scorer.score()`; (4) compute `contradiction_count` and `last_contradiction_time` from `existing` and `is_contradiction`; (5) pass all three new fields to the `EntityRecord(...)` constructor
- [X] T022 [P] [US3] Add `test_enrich_contradiction_detected_increments_count`, `test_enrich_agreeing_signals_no_penalty`, and `test_enrich_first_signal_sets_fingerprint_no_contradiction` tests to `tests/test_metadata_writer.py`; update `make_signal` helper to accept a `valid_time` param so test can control `signal.valid_time`

---

## Phase 6 — User Story 4: API exposes contradiction state

> **Goal**: `GET /v1/entities/{key}` includes `contradiction_count` and `last_contradiction_time` in the JSON response. The internal `last_value_fingerprint` field is never returned to callers.

**Independent test criteria**: GET response JSON keys include `contradiction_count` (int ≥ 0) and `last_contradiction_time` (float or null); key `last_value_fingerprint` is absent from the response

- [X] T023 [US4] Add `data.pop("last_value_fingerprint", None)` after `data = dataclasses.asdict(record)` in `proxy._handle_get_entity` in `revok/proxy.py`
- [X] T024 [P] [US4] Add `test_get_entity_response_includes_contradiction_fields` and `test_get_entity_response_excludes_last_value_fingerprint` tests to `tests/test_proxy.py`; store a record with `contradiction_count=3, last_contradiction_time=9999.0` then assert the GET response contains those values and no `last_value_fingerprint` key

---

## Final Phase — Polish & Cross-Cutting Concerns

- [X] T025 Create `tests/test_contradiction_detection.py` implementing all 7 integration scenarios from spec using real `ScoringEngine` + `SqliteStateStore(":memory:")`: Scenario 1 (agreeing signals, count=0), Scenario 2 (conflicting signals, count=1, lower score), Scenario 3 (at window boundary, no penalty), Scenario 4 (score recovery after contradiction unchanged), Scenario 5 (rapid flips count=2), Scenario 6 (backward-compat load), Scenario 7 (non-numeric content contradiction)
- [X] T026 Run full test suite and confirm all 195 + new tests pass: `python -m pytest --tb=short -q`
- [X] T027 Run ruff and mypy and confirm zero errors: `ruff check revok/ tests/ && mypy revok/`

---

## Dependency Graph

```
T001 ──► T002
         │
T003 ──► T004 ──► T005 ──► T006  (Foundational — must complete before Phase 3+)
                             │
              ┌──────────────┴─────────────────────┐
              ▼                                     ▼
       Phase 3 [US1]                         Phase 3 [US1]
       T007 → T008 → T009 → T010 → T011     T012, T013, T014 [P]
              │
    ┌─────────┴──────────────────────┐
    ▼                                ▼
Phase 4 [US2]                 Phase 4 [US2]
T015 → T016 → T017 → T018    T019, T020 [P]
              │
    ┌─────────┘
    ▼
Phase 5 [US3]: T021 → T022 [P]
              │
    ┌─────────┘
    ▼
Phase 6 [US4]: T023 → T024 [P]
              │
    ┌─────────┘
    ▼
Final: T025 → T026 → T027
```

---

## Parallel Execution Examples

### Phase 2 (after T003, T004)
```
T005 (test_config.py)  ──┐
T006 (test_models.py)  ──┴── both can run simultaneously
```

### Phase 3 (after T011)
```
T012 (_extract_fingerprint tests)  ──┐
T013 (detect_contradiction tests)  ──┤── all three can run simultaneously
T014 (score penalty tests)         ──┘
```

### Phase 4 (after T018)
```
T019 (contradiction fields round-trip)  ──┐
T020 (backward compat load)             ──┴── both can run simultaneously
```

### Phase 5 (after T021)
```
T022 (metadata_writer tests)  ── can start immediately after T021
```

### Phase 6 (after T023)
```
T024 (proxy tests)  ── can start immediately after T023
```

---

## Implementation Strategy

**MVP Scope** (US1 alone delivers testable value):
- T001–T002 (setup), T003–T006 (foundational), T007–T014 (US1 scoring engine)
- At this point: `ScoringEngine` fully implements contradiction detection with unit tests

**Increment 2** adds persistence (T015–T020): contradiction fields survive restarts  
**Increment 3** adds pipeline wiring (T021–T022): enrichment triggers detection automatically  
**Increment 4** adds API exposure (T023–T024): agents can query contradiction state  
**Final** ties all 7 spec scenarios together (T025–T027)
