# Data Model — Contradiction Detection (003)
**Feature**: 003-contradiction-detection
**Date**: 2026-06-09

---

## EntityRecord (modified)

**Module**: `revok/models.py`

| Field | Type | Default | Persisted | Notes |
|-------|------|---------|-----------|-------|
| `entity_key` | `str` | — | ✓ | PK, normalized |
| `score` | `float` | — | ✓ | `[0.0, score_cap]` |
| `valid_time` | `float` | — | ✓ | Event time (Unix epoch) |
| `transaction_time` | `float` | — | ✓ | Write time (Unix epoch) |
| `signal_count` | `int` | — | ✓ | Cumulative signal count |
| `pattern_name` | `str` | — | ✓ | Last matched pattern name |
| `contradiction_count` ⭐ | `int` | `0` | ✓ | Lifetime contradiction events |
| `last_contradiction_time` ⭐ | `float \| None` | `None` | ✓ | Epoch of last contradiction |
| `last_value_fingerprint` ⭐ | `str \| None` | `None` | ✓ | Internal; not in API response |
| `last_seen` | `float` (property) | — | ✗ | Alias for `valid_time`; compat only |

⭐ = new in this feature

### Validation Rules
- `score` must be in `[0.0, score_cap]` — enforced by `ScoringEngine.score()`
- `transaction_time >= valid_time` — enforced by clamp in `metadata_writer.enrich()`
- `contradiction_count >= 0` — always incremented, never decremented
- `last_contradiction_time` is `None` until first contradiction; thereafter always set

### State Transitions (contradiction fields only)
```
Initial write (no prior record):
  contradiction_count = 0
  last_contradiction_time = None
  last_value_fingerprint = _extract_fingerprint(raw_content)

Subsequent write — fingerprints agree OR outside window:
  contradiction_count = existing.contradiction_count  (unchanged)
  last_contradiction_time = existing.last_contradiction_time  (unchanged)
  last_value_fingerprint = new_fingerprint  (always updated)

Subsequent write — fingerprints differ AND within window:
  contradiction_count = existing.contradiction_count + 1
  last_contradiction_time = new signal's valid_time
  last_value_fingerprint = new_fingerprint  (always updated)
```

---

## ScoringConfig (modified)

**Module**: `revok/config.py`

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `half_life_seconds` | `float` | — | Required; must be > 0 |
| `signal_strength` | `float` | — | Required; degradation per signal |
| `score_cap` | `float` | — | Required; max score |
| `contradiction_window_seconds` ⭐ | `float` | `300.0` | 5-minute window; strict open |
| `contradiction_penalty` ⭐ | `float` | `0.15` | Extra penalty per contradiction |

⭐ = new in this feature

---

## ScoringEngine (modified)

**Module**: `revok/scoring.py`

### New/Modified Methods

#### `_extract_fingerprint(raw_content: str) -> str | None`
- **Type**: `@staticmethod`
- **Input**: raw signal content string
- **Returns**: canonical fingerprint string, or `None` if empty/unparseable
- **Algorithm**:
  1. If `raw_content` is empty → `None`
  2. Try `re.search(r'\$?\s*(\d+(?:\.\d+)?)', raw_content)` → `str(float(match.group(1)))`
  3. Fallback: `hashlib.sha256(raw_content.lower().encode()).hexdigest()`

#### `detect_contradiction(existing: EntityRecord | None, new_fingerprint: str | None, new_valid_time: float) -> bool`
- **Type**: instance method
- **Returns `True`** iff all of:
  - `existing is not None`
  - `existing.last_value_fingerprint is not None`
  - `new_fingerprint is not None`
  - `new_fingerprint != existing.last_value_fingerprint`
  - `(new_valid_time - existing.valid_time) < self._contradiction_window_seconds` (strict open)
- **Returns `False`** in all other cases (first signal, agreeing fingerprints, outside window,
  or unparseable content)

#### `score(existing: EntityRecord | None, now: float, *, is_contradiction: bool = False) -> float`
- **Signature change**: adds `*, is_contradiction: bool = False` (keyword-only, default `False`)
- **Behavior when `is_contradiction=True`**:
  ```
  score_new = score_recovered - signal_strength - contradiction_penalty
  score_new = max(0.0, score_new)
  ```
- **Behavior when `is_contradiction=False`** (unchanged):
  ```
  score_new = score_recovered - signal_strength
  score_new = max(0.0, score_new)
  ```

---

## SqliteStateStore (modified)

**Module**: `revok/state_store.py`

### Schema Changes

Three new columns added via `ALTER TABLE … ADD COLUMN` in `open()`:

| Column | SQLite Type | Default | Notes |
|--------|------------|---------|-------|
| `contradiction_count` | `INTEGER NOT NULL DEFAULT 0` | `0` | Existing rows get 0 |
| `last_contradiction_time` | `REAL` | `NULL` | NULL = never contradicted |
| `last_value_fingerprint` | `TEXT` | `NULL` | NULL = first signal pending |

### SELECT column positions (after migration)
```
0: entity_key
1: score
2: last_seen  (legacy alias)
3: valid_time
4: transaction_time
5: signal_count
6: pattern_name
7: contradiction_count
8: last_contradiction_time
9: last_value_fingerprint
```

### `put()` upsert columns (extended)
All 10 columns above included in INSERT / ON CONFLICT UPDATE.

---

## metadata_writer.enrich() (modified)

**Module**: `revok/metadata_writer.py`

The `for entity in entities:` loop is extended:

```
existing = store.get(entity.key)
new_fingerprint = ScoringEngine._extract_fingerprint(signal.raw_content)
is_contradiction = scorer.detect_contradiction(existing, new_fingerprint, raw_valid_time)
new_score = scorer.score(existing, now, is_contradiction=is_contradiction)

if is_contradiction:
    contradiction_count = (existing.contradiction_count + 1) if existing else 1
    last_contradiction_time = raw_valid_time
else:
    contradiction_count = existing.contradiction_count if existing else 0
    last_contradiction_time = existing.last_contradiction_time if existing else None

record = EntityRecord(
    ...,
    contradiction_count=contradiction_count,
    last_contradiction_time=last_contradiction_time,
    last_value_fingerprint=new_fingerprint,
)
```

---

## proxy._handle_get_entity (modified)

**Module**: `revok/proxy.py`

After `data = dataclasses.asdict(record)`, add:
```python
data.pop("last_value_fingerprint", None)
```
This ensures `last_value_fingerprint` is excluded from the API response per FR-009.
