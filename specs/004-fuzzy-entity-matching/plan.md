# Implementation Plan — Fuzzy Entity Matching (004)
**Feature**: 004-fuzzy-entity-matching
**Branch**: feat/fuzzy-entity-matching
**Date**: 2026-06-10
**Status**: Planning complete

---

## Technical Context

- **Python**: 3.11+, `from __future__ import annotations` throughout
- **Test runner**: pytest + pytest-asyncio, `asyncio_mode = "auto"`, 230 passing tests on develop
- **Type checker**: mypy strict (0 errors required)
- **Linter**: ruff (0 errors required)
- **New production dependency**: `rapidfuzz>=3.0,<4` (already installed in venv)
- **Scorer**: `rapidfuzz.fuzz.partial_ratio` — see `research.md` for scorer selection rationale
- **Constraint**: All 230 existing tests must continue passing; 0 ruff/mypy errors throughout

## Constitution Check

| Gate | Status | Notes |
|------|--------|-------|
| All tests green before merging | PASS | 230 tests on develop |
| ruff clean | PASS | enforced at merge |
| mypy strict clean | PASS | enforced at merge |
| No new HTTP endpoints | PASS | no proxy/routes changes |
| Backward compat preserved | PASS | `fuzzy_match_threshold` defaults to `None`; all existing configs unaffected |
| No breaking API changes | PASS | no response schema changes |
| No spaCy / NLTK | PASS | rapidfuzz only; no model downloads |

---

## Implementation Phases

### T001 — Add `fuzzy_match_threshold` to `EntityMatcherConfig`
**File**: `revok/config.py`

- Add `fuzzy_match_threshold: float | None = None` field to `EntityMatcherConfig`
  dataclass, after `patterns`.
- **Tests affected**: `tests/test_config.py` — add tests for:
  - Default is `None` when key absent from YAML
  - Explicit `fuzzy_match_threshold: 80` parses to `80.0`
  - `fuzzy_match_threshold: 0` is valid
  - `fuzzy_match_threshold: 100` is valid
  - `fuzzy_match_threshold: -1` raises `ConfigError`
  - `fuzzy_match_threshold: 101` raises `ConfigError`

### T002 — Wire `fuzzy_match_threshold` in `load_config()`
**File**: `revok/config.py`

Inside the `if em_raw is not None:` block, after the existing `pattern_cfgs` parsing:

```python
fuzzy_raw = em_raw.get("fuzzy_match_threshold")
fuzzy_threshold: float | None = None
if fuzzy_raw is not None:
    if not isinstance(fuzzy_raw, (int, float)) or not (0 <= float(fuzzy_raw) <= 100):
        raise ConfigError(
            f"entity_matcher.fuzzy_match_threshold must be between 0 and 100, "
            f"got {fuzzy_raw}"
        )
    fuzzy_threshold = float(fuzzy_raw)
```

Update `EntityMatcherConfig(...)` construction to pass `fuzzy_match_threshold=fuzzy_threshold`.

- **Tests affected**: `tests/test_config.py` (same as T001 — test both dataclass default
  and YAML round-trip)

### T003 — Extend `_alias_patterns` to store original alias string
**File**: `revok/entity_matcher.py`

Change `_alias_patterns` type from `list[tuple[re.Pattern[str], str]]` to
`list[tuple[re.Pattern[str], str, str]]` — adding the original alias string as the
third element.

In `__init__`, change the append from:
```python
self._alias_patterns.append((compiled, entity_def.id))
```
to:
```python
self._alias_patterns.append((compiled, entity_def.id, alias))
```

Update the exact-match loop to unpack 3-tuple:
```python
for pattern, canonical_id, _alias in self._alias_patterns:
```

- **Tests affected**: all existing alias catalog tests — no behavior change, just
  internal representation; they should continue to pass unchanged.

### T004 — Implement fuzzy fallback in `EntityMatcher.match()`
**File**: `revok/entity_matcher.py`

Add `from rapidfuzz import fuzz` import at module level.

Store threshold: `self._fuzzy_threshold = config.fuzzy_match_threshold`

Modify `match()`:

1. After exact alias scan (step 1), if `entities` is non-empty → return immediately
   (unchanged behavior).
2. If `entities` is empty and `self._fuzzy_threshold is not None`:
   - Scan `_alias_patterns` for best `(score, canonical_id, alias_str)` via
     `fuzz.partial_ratio(alias_str, text)`.
   - If `best_score >= self._fuzzy_threshold`:
     - Log at DEBUG: `"Fuzzy match: text=%r → entity=%r alias=%r score=%.1f"`
     - Return `[Entity(key=best_id, raw_text=text, pattern_name="fuzzy")]`
   - Else:
     - Log at DEBUG: `"Fuzzy match: no match for text=%r (best score=%.1f < threshold=%.1f)"`
     - Return `[]`
3. If `self._fuzzy_threshold is None` → run legacy regex patterns (unchanged step 2).

- **Tests affected**: `tests/test_entity_matcher.py` — add fuzzy-specific test section

### T005 — Add `rapidfuzz` to `pyproject.toml`
**File**: `pyproject.toml`

Add `"rapidfuzz>=3.0,<4"` to the `dependencies` list (alphabetical order:
after `pyyaml`, before nothing → append after `pyyaml`).

- **Tests affected**: none; CI will install from updated `pyproject.toml`

### T006 — Update example configs
**Files**: `config/revok.example.yaml`, `config/revok-zep.example.yaml`

Add commented-out example under `entity_matcher:` section in both files:

```yaml
  # Enable fuzzy matching for near-miss alias references (typos, abbreviations).
  # Score is 0–100; omit or set to null to disable. Default: null (disabled).
  # fuzzy_match_threshold: 80
```

- **Tests affected**: none

### T007 — Write fuzzy matching tests
**File**: `tests/test_entity_matcher.py`

Add a new `# Fuzzy matching tests` section with at minimum:

1. `test_fuzzy_match_single_char_typo` — "apex hodie" matches `apex_hoodie` at threshold 80
2. `test_fuzzy_match_in_longer_text` — "Customer asked about apex hodie" matches at 80
3. `test_fuzzy_no_match_below_threshold` — "totally unrelated" returns `[]` at threshold 80
4. `test_fuzzy_not_entered_when_exact_matches` — exact match present → fuzzy not entered
   (verified by ensuring pattern_name ≠ "fuzzy")
5. `test_fuzzy_disabled_when_threshold_none` — threshold=None, near-miss returns `[]`
6. `test_fuzzy_threshold_zero_always_matches` — threshold=0, any non-empty text matches
7. `test_fuzzy_tie_break_first_entity_wins` — two entities, equal score → first in list
8. `test_fuzzy_entity_key_is_canonical_id` — fuzzy result uses `EntityDef.id`, not raw text
9. `test_fuzzy_pattern_name_is_fuzzy` — `pattern_name == "fuzzy"`
10. `test_fuzzy_raw_text_is_full_input` — `raw_text` equals full `text` argument

### T008 — Final validation
- Run `pytest -q` — all 230 + new tests must pass
- Run `.venv\Scripts\ruff check .` — 0 errors
- Run `.venv\Scripts\mypy revok` — 0 errors

---

## Execution Order

```
T005 (pyproject) → T001+T002 (config) → T003+T004 (entity_matcher) → T006 (examples) → T007 (tests) → T008 (validate)
```

T001 and T002 are in the same file — implement together.
T003 and T004 are in the same file — implement together.
T005 can be done at any time before T004.
