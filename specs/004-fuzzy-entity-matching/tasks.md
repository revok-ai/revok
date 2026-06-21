# Tasks — Fuzzy Entity Matching (004)
**Feature**: 004-fuzzy-entity-matching
**Branch**: feat/fuzzy-entity-matching
**Date**: 2026-06-10
**Total tasks**: 20 | **User stories**: 4 | **Parallel opportunities**: 7

---

## User Stories

| ID | Story | Priority | Independent Test Criterion |
|----|-------|----------|---------------------------|
| US1 | Near-miss alias match returns correct canonical entity | P1 | `EntityMatcher.match("apex hodie")` with `fuzzy_match_threshold=80` returns `[Entity(key="apex_hoodie", pattern_name="fuzzy")]` |
| US2 | Threshold controls match sensitivity | P1 | Same text scores 85 → match at threshold 80, no match at threshold 90; threshold 0 always matches |
| US3 | Exact match takes priority over fuzzy | P1 | When exact alias match exists, `pattern_name != "fuzzy"` and fuzzy path never entered |
| US4 | Feature disabled by default (opt-in) | P1 | `fuzzy_match_threshold=None` (default) → near-miss returns `[]`; existing 230 tests pass unmodified |

---

## Phase 1 — Setup

> Verify baseline before any changes.

- [X] T001 Confirm 230 tests pass on feat/fuzzy-entity-matching: `python -m pytest --tb=short -q`
- [X] T002 Confirm ruff and mypy are clean on baseline: `ruff check revok/ tests/ && mypy revok/`

---

## Phase 2 — Foundational (blocking all user stories)

> Config changes and the `pyproject.toml` dependency must land before any `EntityMatcher` changes.

- [X] T003 Add `"rapidfuzz>=3.0,<4"` to `dependencies` list in `pyproject.toml` after the `pyyaml` entry
- [X] T004 Add `fuzzy_match_threshold: float | None = None` field to `EntityMatcherConfig` dataclass in `revok/config.py` after the `patterns` field
- [X] T005 [P] Add fuzzy threshold parsing to `load_config()` in `revok/config.py` inside the `if em_raw is not None:` block after the existing pattern parsing: read `em_raw.get("fuzzy_match_threshold")`; if not `None`, validate `0 <= value <= 100` else raise `ConfigError("entity_matcher.fuzzy_match_threshold must be between 0 and 100, got {value}")`; pass `fuzzy_match_threshold=fuzzy_threshold` to `EntityMatcherConfig(...)` constructor
- [X] T006 [P] Add config tests to `tests/test_config.py`: `test_entity_matcher_fuzzy_threshold_default_is_none`, `test_entity_matcher_fuzzy_threshold_explicit_80`, `test_entity_matcher_fuzzy_threshold_zero_valid`, `test_entity_matcher_fuzzy_threshold_100_valid`, `test_entity_matcher_fuzzy_threshold_negative_raises`, `test_entity_matcher_fuzzy_threshold_above_100_raises`

---

## Phase 3 — User Story 4: Disabled by default

> **Goal**: Confirm the feature is opt-in. No threshold = no fuzzy attempt. Existing test suite unaffected.

**Independent test criteria**: `EntityMatcherConfig()` has `fuzzy_match_threshold=None`; `EntityMatcher(config_with_no_threshold).match("apex hodie")` returns `[]` even when catalog contains "Apex Hoodie"

- [X] T007 [US4] Extend `_alias_patterns` internal storage in `EntityMatcher.__init__` in `revok/entity_matcher.py` from `list[tuple[re.Pattern[str], str]]` to `list[tuple[re.Pattern[str], str, str]]` — add the original alias string as the third element in the `self._alias_patterns.append(...)` call; update the exact-match loop unpacking to `for pattern, canonical_id, _alias in self._alias_patterns:`; store `self._fuzzy_threshold = config.fuzzy_match_threshold`
- [X] T008 [P] [US4] Add `test_fuzzy_disabled_when_threshold_none` test to `tests/test_entity_matcher.py`: build catalog matcher with `fuzzy_match_threshold=None` and alias "Apex Hoodie", call `match("apex hodie")`, assert result is `[]`

---

## Phase 4 — User Story 3: Exact match takes priority

> **Goal**: When exact alias matching produces a result, the fuzzy path is never entered and the returned entity carries no `pattern_name="fuzzy"` marker.

**Independent test criteria**: `EntityMatcher.match("Apex Hoodie")` with `fuzzy_match_threshold=80` returns `[Entity(key="apex_hoodie")]` where `pattern_name != "fuzzy"`

- [X] T009 [US3] Add fuzzy fallback logic to `EntityMatcher.match()` in `revok/entity_matcher.py`: after the existing exact-alias loop, if `entities` is non-empty return immediately (no change); if `entities` is empty and `self._fuzzy_threshold is not None`, run the fuzzy scan loop — for each `(pattern, canonical_id, alias_str)` in `_alias_patterns` compute `fuzz.partial_ratio(alias_str, text)`, track best `(score, canonical_id, alias_str)`; add `from rapidfuzz import fuzz` import at module level; when `_fuzzy_threshold is None`, fall through to legacy regex patterns (unchanged)
- [X] T010 [P] [US3] Add `test_fuzzy_not_entered_when_exact_matches` test to `tests/test_entity_matcher.py`: build catalog matcher with `fuzzy_match_threshold=80` and alias "Apex Hoodie", call `match("Apex Hoodie")` (exact), assert one result and `entities[0].pattern_name != "fuzzy"`

---

## Phase 5 — User Story 1: Near-miss alias match

> **Goal**: A signal text that is close but not identical to a catalog alias is correctly mapped to the canonical entity ID when the fuzzy score meets the threshold.

**Independent test criteria**: `match("apex hodie")` at threshold 80 → `[Entity(key="apex_hoodie", pattern_name="fuzzy")]`; `match("totally unrelated item")` at threshold 80 → `[]`; `match("Customer asked about apex hodie")` at threshold 80 → `[Entity(key="apex_hoodie", pattern_name="fuzzy")]`

- [X] T011 [US1] Complete fuzzy acceptance branch in `EntityMatcher.match()` in `revok/entity_matcher.py`: after computing best score, if `best_score >= self._fuzzy_threshold` log DEBUG `"Fuzzy match: text=%r → entity=%r alias=%r score=%.1f"` and return `[Entity(key=best_canonical_id, raw_text=text, pattern_name="fuzzy")]`; else log DEBUG `"Fuzzy match: no match for text=%r (best score=%.1f < threshold=%.1f)"` and return `[]`
- [X] T012 [P] [US1] Add `test_fuzzy_match_single_char_typo` to `tests/test_entity_matcher.py`: alias "Apex Hoodie", text "apex hodie", threshold 80 → key "apex_hoodie"
- [X] T013 [P] [US1] Add `test_fuzzy_match_in_longer_text` to `tests/test_entity_matcher.py`: alias "Apex Hoodie", text "Customer asked about apex hodie today", threshold 80 → key "apex_hoodie"
- [X] T014 [P] [US1] Add `test_fuzzy_no_match_below_threshold` to `tests/test_entity_matcher.py`: alias "Apex Hoodie", text "totally unrelated item", threshold 80 → `[]`
- [X] T015 [P] [US1] Add `test_fuzzy_entity_key_is_canonical_id`, `test_fuzzy_pattern_name_is_fuzzy`, and `test_fuzzy_raw_text_is_full_input` tests to `tests/test_entity_matcher.py`

---

## Phase 6 — User Story 2: Threshold controls sensitivity

> **Goal**: The threshold value precisely controls the boundary between match and no-match. Threshold 0 always matches; threshold 100 requires a perfect score.

**Independent test criteria**: text scoring 85 → match at threshold 80, no match at threshold 90; threshold 0 → any non-empty text matches closest alias; tie-break returns first entity in config list

- [X] T016 [P] [US2] Add `test_fuzzy_threshold_boundary_accepts_at_threshold` and `test_fuzzy_threshold_boundary_rejects_below` to `tests/test_entity_matcher.py` using a `MockFuzzy` approach or a known-score alias/text pair (e.g. "Apex Hoodie" vs "apex hodie" scores ~80 at partial_ratio — verify and use exact score)
- [X] T017 [P] [US2] Add `test_fuzzy_threshold_zero_always_matches` and `test_fuzzy_tie_break_first_entity_wins` tests to `tests/test_entity_matcher.py`: for tie-break, two entities with equal-scoring aliases; assert key matches the first entity in the list

---

## Final Phase — Polish & Cross-Cutting Concerns

- [X] T018 Add commented-out `fuzzy_match_threshold` example to `entity_matcher:` section in both `config/revok.example.yaml` and `config/revok-zep.example.yaml`: `# fuzzy_match_threshold: 80  # similarity score 0–100; omit to disable`
- [X] T019 Run full test suite and confirm all 230 + new tests pass: `python -m pytest --tb=short -q`
- [X] T020 Run ruff and mypy and confirm zero errors: `ruff check revok/ tests/ && mypy revok/`

---

## Dependency Graph

```
T001 ──► T002
         │
T003 ──► T004 ──► T005 ──► T006  (Foundational — complete before Phase 3+)
                             │
              ┌──────────────┤
              ▼              ▼
          Phase 3 [US4]   Phase 3 [US4]
          T007             T008 [P]
              │
    ┌─────────┘
    ▼
Phase 4 [US3]
T009 → T010 [P]
    │
    ▼
Phase 5 [US1]
T011 → T012 [P], T013 [P], T014 [P], T015 [P]
    │
    ▼
Phase 6 [US2]
T016 [P], T017 [P]
    │
    ▼
Final Phase
T018 → T019 → T020
```

---

## Parallel Execution Examples

**Within Phase 2** (after T003+T004):
- T005 (load_config wiring) and T006 (config tests) can be written in parallel — different targets in the same file

**Within Phase 5** (after T011):
- T012, T013, T014, T015 are all independent test additions — can be added in a single edit pass

**Within Phase 6** (after Phase 5):
- T016 and T017 are independent test additions — can be added together

---

## Implementation Strategy

**MVP = Phase 1 + Phase 2 + Phase 3 + Phase 4** — delivers opt-in flag, config validation, internal storage change, and confirms exact matching still wins. Fuzzy behavior is added in Phase 5.

**Full delivery = all phases** — adds fuzzy match logic (Phase 5), threshold sensitivity (Phase 6), and example config docs (Final Phase).

Each phase is independently testable: run `pytest tests/test_entity_matcher.py tests/test_config.py -q` after each phase to verify progress.

---

## Format Validation

All tasks follow the required checklist format:
- ✅ Every task starts with `- [ ]`
- ✅ Every task has a sequential ID (T001–T020)
- ✅ `[P]` marker present on all parallelizable tasks
- ✅ `[USn]` label present on all user story phase tasks
- ✅ Every task includes a file path
