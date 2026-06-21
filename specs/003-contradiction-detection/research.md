# Research — Contradiction Detection (003)
**Feature**: 003-contradiction-detection
**Date**: 2026-06-09

---

## 1. Fingerprint Extraction Regex

**Decision**: `re.search(r'\$?\s*(\d+(?:\.\d+)?)', raw_content)`

**Rationale**: Captures an optional `$`, optional whitespace, then an integer or decimal
literal. Group 1 is passed to `float()` then `str()` for canonical form. This handles
`"$500"`, `"$500.00"`, `"500"`, `"500.0"`, `"price is 450 USD"`.

**Alternatives considered**:
- `r'\$?(\d[\d,]*(?:\.\d+)?)'` — handles commas in numbers like `$1,500`; deferred to
  v0.3.0 to keep the regex simple for MVP.
- Full NLP value extraction — explicitly out of scope per spec Non-Goals.

**Fallback**: `hashlib.sha256(raw_content.lower().encode()).hexdigest()` — deterministic,
collision-resistant, constant length. Used when no numeric value is found. Lowercased first
so `"User is Authenticated"` == `"user is authenticated"` (same fingerprint).

**Returns `None`**: when `raw_content` is empty or unparseable (guards against `float()` on
pathological inputs).

---

## 2. `score()` API Extension Strategy

**Decision**: Add keyword-only `is_contradiction: bool = False` parameter to
`ScoringEngine.score()`.

**Rationale**: Fully backward-compatible — all existing call sites pass no keyword arg and
get old behavior. The contradiction penalty is applied inside `score()` so the penalty
formula stays co-located with all other scoring logic. Easy to test in isolation.

**Alternatives considered**:
- New `score_signal()` method returning `(float, bool)` tuple — breaks the clean
  one-method scoring interface; overkill for MVP.
- Separate `apply_contradiction_penalty(score)` method — splits logically related code
  across two calls; callers must remember to chain them.
- Passing `raw_content` directly to `score()` — couples scorer to string parsing; harder
  to test penalty logic independently of regex.

---

## 3. Contradiction Detection Placement

**Decision**: Two-step split:
1. `ScoringEngine._extract_fingerprint(raw_content)` — `@staticmethod`, scorer module.
2. `ScoringEngine.detect_contradiction(existing, new_fingerprint, new_valid_time) → bool`
   — instance method (needs `_contradiction_window_seconds`).
3. `metadata_writer.enrich()` calls both, builds contradiction state, then calls
   `scorer.score(existing, now, is_contradiction=...)`.

**Rationale**: Fingerprint extraction has no instance state → static. Window comparison
needs config → instance. The writer already orchestrates fetching `existing`, computing
`new_score`, building and storing the updated `EntityRecord`; adding contradiction fields
here is the natural extension of that orchestration.

**Alternatives considered**:
- Full detection inside `score()` — `score()` would need both `raw_content` and
  `new_valid_time`; currently `now` (transaction time) is used for decay, `raw_valid_time`
  (event time) is used for window — conflating the two parameters is confusing.
- Detection as a standalone module-level function — works but adds indirection with no
  benefit; scorer already holds the config.

---

## 4. `last_value_fingerprint` in GET Response

**Decision**: Pop from response dict in `proxy._handle_get_entity` before serializing.

**Rationale**: FR-009 specifies exactly which fields appear in the `GET /v1/entities/{key}`
response; `last_value_fingerprint` is an internal implementation detail (for comparison
only) and must not leak to callers. `dataclasses.asdict(record)` serializes all dataclass
fields, so the proxy must explicitly remove the key.

**Implementation**: `data.pop("last_value_fingerprint", None)` after `asdict`.

---

## 5. SQLite Migration Strategy — 3 New Columns

**Decision**: Follow the existing ALTER TABLE IF NOT EXISTS pattern already used for
`valid_time` and `transaction_time` in `SqliteStateStore.open()`.

Add to the loop inside `open()`:
```python
_NEW_COLS = [
    ("valid_time", "REAL"),
    ("transaction_time", "REAL"),
    ("contradiction_count", "INTEGER NOT NULL DEFAULT 0"),
    ("last_contradiction_time", "REAL"),
    ("last_value_fingerprint", "TEXT"),
]
```

**Rationale**: SQLite supports `ADD COLUMN … DEFAULT` for constant defaults (docs §3.37+
and earlier). Existing rows automatically receive `contradiction_count = 0` and
`NULL` for the nullable columns. No data migration script needed (FR-008).

**Backward compat in Python**: When loading rows, `contradiction_count` is guaranteed to
be an integer after migration; Python-side `row[7] if row[7] is not None else 0` is a
safety belt for any edge case.

---

## 6. `EntityRecord` Field Ordering

**Decision**: Append the three new fields at the end of the dataclass with default values:
```python
contradiction_count: int = 0
last_contradiction_time: float | None = None
last_value_fingerprint: str | None = None
```

**Rationale**: Python dataclass rules require fields with defaults to follow fields without
defaults. Appending at the end preserves all existing positional construction. All existing
test fixtures that construct `EntityRecord(entity_key=…, score=…, …)` using keyword args
continue to work unchanged; positional callers also work because the new fields have
defaults.

---

## 7. `ScoringConfig` Extension

**Decision**: Add two optional fields with defaults to `ScoringConfig` (frozen dataclass):
```python
contradiction_window_seconds: float = 300.0
contradiction_penalty: float = 0.15
```

**Rationale**: Frozen dataclass — fields with defaults must follow required fields. Both
new fields come after `score_cap`. All existing `ScoringConfig(half_life_seconds=…, …)`
constructions that don't pass the new kwargs continue to work.

YAML loader already handles extra keys via the `_build_scoring_config` helper — the fields
will be populated from `revok.yaml` if present, or default to the above values if absent.
Existing `revok.yaml` files that omit both keys load without error (FR-010).

---

## 8. Test Strategy

- **`tests/test_contradiction_detection.py`** (new): 7 integration scenarios from spec,
  using real `ScoringEngine` + in-memory `SqliteStateStore` (`:memory:`).
- **`tests/test_scoring.py`** additions: unit tests for `_extract_fingerprint` (numeric,
  non-numeric, empty, edge cases) and `detect_contradiction` (within window, at boundary,
  outside window, agreeing fingerprints, None existing).
- **`tests/test_state_store.py`** additions: verify new columns round-trip through
  `put` → `get`; verify backward-compat load (old schema without new columns).
- **`tests/test_models.py`** additions: verify default field values on `EntityRecord`.
- **Existing 195 tests**: no changes required to pass — all construction sites use keyword
  args or positional args where the new fields have defaults.
