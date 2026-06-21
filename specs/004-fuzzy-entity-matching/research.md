# Research — Fuzzy Entity Matching (004)
**Feature**: 004-fuzzy-entity-matching
**Date**: 2026-06-10

---

## 1. Scorer Selection: `partial_ratio` vs `WRatio`

**Decision**: Use `rapidfuzz.fuzz.partial_ratio` as the primary scorer.

**Rationale**: The spec draft referenced `fuzz.WRatio`, but empirical testing against the
spec's own acceptance scenarios reveals a mismatch:

| Input text | Alias | WRatio | partial_ratio |
|------------|-------|--------|---------------|
| `"apex hodie"` | `"Apex Hoodie"` | 76.2 | **80.0** |
| `"Customer asked about apex hodie"` | `"Apex Hoodie"` | 71.6 | **80.0** |
| `"Apex Hoodei available in blue"` | `"Apex Hoodie"` | 86.4 | **95.2** |
| `"totally unrelated content"` | `"Apex Hoodie"` | 34.0 | 38.0 |

`WRatio` is sensitive to string length differences: when a short alias (e.g. 11 chars)
is compared against a long signal body (e.g. 50 chars), the length penalty brings the
score well below any useful threshold. `partial_ratio` finds the best matching substring
of the same length as the alias within the text — which is exactly the right semantic
for "does this alias appear somewhere in this content, perhaps misspelled?".

**Conclusion**: `partial_ratio` delivers the acceptance behavior described in the spec
at threshold 80. `WRatio` does not. The plan uses `partial_ratio`.

**Alternatives considered**:
- `WRatio` — spec's original choice; fails spec acceptance criteria empirically.
- `token_set_ratio` — handles word reordering but scores unrelated text too high for
  short aliases; not needed for entity names which are typically 1–3 tokens.
- `partial_token_sort_ratio` — useful for multi-word entities in different order; adds
  complexity without benefit for the catalog sizes in scope (≤ 50 entities, ≤ 5 aliases).

---

## 2. Match Loop Design: Alias-level vs Entity-level Scan

**Decision**: Scan all aliases across all entities, track best `(score, entity_id,
alias)` tuple, accept if best score ≥ threshold.

**Rationale**: Aliases within the same entity may have different scores for the same
input text (e.g. `"Apex Hoodie"` and `"SKU-1042"` against `"apex hodie"`). Scanning at
the alias level and keeping the global best gives the most accurate result. The tie-break
rule (first `EntityDef` in config list wins on equal scores) is simple and deterministic.

**Alternatives considered**:
- Entity-level scan (max alias score per entity) then compare entities — equivalent
  result but more code for no benefit.
- Early exit at first alias ≥ threshold — saves CPU on large catalogs but breaks
  tie-breaking semantics; not needed for ≤ 50 entities.

---

## 3. Interaction with Legacy Regex Pattern Mode

**Decision**: When `fuzzy_match_threshold` is set AND exact matching returns no results,
run fuzzy matching only (skip legacy regex patterns). Legacy regex only runs when
`fuzzy_match_threshold` is `None`.

**Rationale**: The spec says fuzzy is a fallback tier for the alias catalog. Mixing fuzzy
results with regex results in the same response would be confusing. Operators choose one
mode or the other. Existing regex-only deployments are unaffected (threshold = None).

**Alternatives considered**:
- Run fuzzy, then regex if fuzzy also misses — adds complexity, two fallback tiers is
  not in spec, deferred if ever needed.
- Always run both and merge results — deduplicated by key, but confusing precedence.

---

## 4. Config Validation Approach

**Decision**: Validate `fuzzy_match_threshold` in `load_config()` using the existing
`ConfigError` pattern, same as `half_life_seconds`, `signal_strength`, `score_cap`.

**Rationale**: Consistent with all other numeric config fields in the codebase.
`ConfigError` is already defined and raised on invalid values. No new patterns needed.

**Range**: [0, 100] inclusive. 0 = match any non-empty text; 100 = perfect match only.
`None` = feature disabled (default).

---

## 5. `rapidfuzz` Version Constraint

**Decision**: `rapidfuzz>=3.0,<4`

**Rationale**: `fuzz.partial_ratio` has been stable API since v1.x. Version 3.0
introduced C extension speedups and is the current major. Capping at `<4` avoids
unreviewed breaking changes. The library is widely used and actively maintained.

**Verified**: `pip install "rapidfuzz>=3.0,<4"` installs cleanly in the project venv.
Installed version tested: 3.x.
