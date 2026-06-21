# Data Model — Fuzzy Entity Matching (004)
**Feature**: 004-fuzzy-entity-matching
**Date**: 2026-06-10

---

## EntityMatcherConfig (modified)

**Module**: `revok/config.py`

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `entities` | `list[EntityDef]` | `[]` | Alias catalog entries |
| `patterns` | `list[PatternConfig]` | `[]` | Legacy named regex patterns |
| `fuzzy_match_threshold` ⭐ | `float \| None` | `None` | Similarity score 0–100; `None` = disabled |

⭐ = new in this feature

### Validation Rules
- `fuzzy_match_threshold`, if not `None`, must be in `[0.0, 100.0]` (inclusive).
- Values outside this range raise `ConfigError` at startup with message:
  `"entity_matcher.fuzzy_match_threshold must be between 0 and 100, got {value}"`
- `fuzzy_match_threshold: 0` is valid — means match any non-empty text to the closest
  alias (maximum recall).
- `fuzzy_match_threshold: 100` is valid — means only a perfect case-insensitive match
  qualifies (equivalent to exact matching; not useful in practice).

---

## EntityMatcher (modified)

**Module**: `revok/entity_matcher.py`

### Constructor changes
- Accept and store `config.fuzzy_match_threshold` as `self._fuzzy_threshold: float | None`
- No other constructor changes

### `match(text: str) -> list[Entity]` — modified control flow

```
Step 1: Exact alias matching (existing — unchanged)
        → if results: return results immediately

Step 2: Fuzzy alias matching (NEW — only if _fuzzy_threshold is not None)
        For each (compiled_alias_pattern, canonical_id) in _alias_patterns:
          alias_str = original alias string (must be stored at init time)
          score = fuzz.partial_ratio(alias_str, text)
          track (score, canonical_id, alias_str) if score > best_so_far
        If best_score >= _fuzzy_threshold:
          return [Entity(key=canonical_id, raw_text=text, pattern_name="fuzzy")]
        else:
          return []

Step 3: Legacy regex pattern matching (existing — only if _fuzzy_threshold is None)
        (unchanged)
```

### Internal storage change
The `_alias_patterns` list currently stores `(compiled_pattern, canonical_id)`. This
feature requires also storing the original alias string for scoring:

```python
# Before (004):
_alias_patterns: list[tuple[re.Pattern[str], str]]

# After (004):
_alias_patterns: list[tuple[re.Pattern[str], str, str]]
#                                             ^id    ^original alias string
```

### Entity model for fuzzy matches

Fuzzy matches produce an `Entity` with:
- `key`: canonical `EntityDef.id` (same as exact match)
- `raw_text`: the full `text` argument passed to `match()` (not the alias)
- `pattern_name`: `"fuzzy"`

---

## No changes to

- `Signal`, `EntityRecord`, `Entity` — no new fields
- `ScoringEngine` — no changes
- `SqliteStateStore` — no schema changes
- `proxy.py`, `metadata_writer.py`, `interfaces.py` — no changes
- Any HTTP endpoints — no new endpoints, no response schema changes
