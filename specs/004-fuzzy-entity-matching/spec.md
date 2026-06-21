# Feature Specification: Fuzzy Entity Matching

**Feature ID**: 004  
**Short Name**: fuzzy-entity-matching  
**Status**: Draft  
**Created**: 2026-06-10  
**Last Updated**: 2026-06-10

---

## Overview

Revok's entity matcher today uses exact alias matching (word-boundary, case-insensitive
regex). When an incoming signal contains a name that is close but not identical to a
catalog alias — a misspelling, abbreviation, or OCR artifact — no entity is matched and
the signal is silently dropped from entity tracking.

This feature adds **fuzzy string matching** as a fallback tier in `EntityMatcher`.
When exact matching finds no entity, Revok applies rapidfuzz similarity scoring against
all configured aliases. If the best match exceeds a configurable threshold, Revok returns
that entity's canonical ID. If the threshold is not met, Revok returns no match —
preserving the existing behavior for truly unrecognized text.

---

## Goals

- Catch near-miss entity references that exact matching misses (typos, OCR noise,
  abbreviations, minor re-phrasings).
- Keep the canonical entity key stable — fuzzy matching resolves to the same
  `EntityDef.id` as an exact match for the same entity.
- Make the similarity threshold configurable per deployment.
- Add no mandatory runtime dependency beyond `rapidfuzz`; do not require spaCy, NLTK,
  or any model download.
- Keep exact matching fast — fuzzy path is only entered when exact matching returns
  no results.

---

## Non-Goals

- Semantic / embedding-based similarity (v0.3.0 scope — spaCy entity auto-extraction).
- Fuzzy matching on legacy regex pattern mode — only the alias catalog benefits from
  this feature.
- Matching across multiple entities in a single token (no multi-span fuzzy match).
- Changing the behavior of the `X-Revok-Entity` header path (header always bypasses
  both exact and fuzzy matching).
- Exposing the similarity score in the `Entity` model or downstream pipeline.
- Modifying examples/, demos, or any file outside `revok/`.

---

## User Stories

### US-FM1 — Near-miss alias match

> As an operator running a catalog-based deployment, I want Revok to match entity
> references that are slightly misspelled or abbreviated so that signals are not lost
> due to minor text variation in agent outputs.

**Acceptance Criteria**:
- Given a catalog entity `apex_hoodie` with alias `"Apex Hoodie"`, when a signal
  contains the text `"apex hodie"` and the fuzzy threshold is 80, then
  `EntityMatcher.match()` returns `[Entity(key="apex_hoodie", ...)]`.
- Given the same setup but the text is `"totally unrelated item"`, then
  `EntityMatcher.match()` returns an empty list.

### US-FM2 — Threshold controls sensitivity

> As an operator, I want to set the minimum similarity score required for a fuzzy
> match so that I can tune precision vs. recall for my specific entity catalog.

**Acceptance Criteria**:
- Given a threshold of 90, when a signal contains text that scores 85 against the
  best alias, then no entity is returned.
- Given a threshold of 80, when the same text scores 85, the entity is returned.
- Given a threshold of 0, any non-empty text matches the closest alias.

### US-FM3 — Exact match takes priority

> As an operator, I want exact alias matches to always win over fuzzy matches so that
> unambiguous signals are not rerouted to a different entity by the fuzzy scorer.

**Acceptance Criteria**:
- Given two catalog entities where one alias exactly matches the signal text, the exact
  match entity is returned even if the fuzzy scorer would assign a higher score to
  a different entity's alias.
- The fuzzy code path is never entered when exact matching produces at least one result.

### US-FM4 — Disabled by default (opt-in)

> As an operator who did not configure fuzzy matching, I want Revok's behavior to be
> unchanged so that existing deployments are not affected by this feature.

**Acceptance Criteria**:
- Given a configuration with no `fuzzy_match_threshold` set, when a signal contains
  a near-miss text, `EntityMatcher.match()` returns an empty list (no fuzzy attempt).
- Given `fuzzy_match_threshold: 0`, fuzzy matching is active at maximum recall.

---

## Functional Requirements

### FR-FM01 — New config field: `entity_matcher.fuzzy_match_threshold`

`EntityMatcherConfig` gains one new optional field:

- `fuzzy_match_threshold: float | None` — similarity score (0–100 inclusive) required
  for a fuzzy match to be accepted. `None` (default) disables fuzzy matching entirely.
  Values outside 0–100 are rejected at config load time with a `ConfigError`.

### FR-FM02 — Fuzzy matching is a fallback tier

`EntityMatcher.match()` behavior:

1. Run existing exact alias matching (unchanged).
2. If exact matching returns one or more entities → return them immediately (no fuzzy
   attempt).
3. If exact matching returns no entities AND `fuzzy_match_threshold` is not `None`:
   a. For every alias in every catalog `EntityDef`, compute the rapidfuzz
      `fuzz.partial_ratio` score between the alias string and the full input `text`.
   b. Identify the alias with the highest score.
   c. If that score ≥ `fuzzy_match_threshold`, return a single `Entity` whose `key`
      is the canonical `EntityDef.id` of the winning alias, `raw_text` is the
      full input `text`, and `pattern_name` is `"fuzzy"`.
   d. If the highest score < `fuzzy_match_threshold`, return an empty list.
4. Legacy regex pattern matching (step 2 of existing code) runs only when
   `fuzzy_match_threshold` is `None` — preserving current behavior for pattern-mode
   deployments.

> **Rationale for `partial_ratio`**: `partial_ratio` finds the best matching substring
> of alias length within the signal text. This correctly handles both short exact-text
> inputs (e.g. `"apex hodie"`) and long signal bodies (e.g. `"Customer asked about
> apex hodie today"`), returning the same score in both cases. `WRatio` penalizes
> length differences between alias and full signal text and fails the spec's own
> acceptance criteria empirically — see `research.md` for benchmarks.

### FR-FM03 — Tie-breaking for equal fuzzy scores

When two aliases from different entities share the same highest score, the entity
whose alias appears first in the `entities` list in the configuration file wins.
This is deterministic and easy to reason about for operators.

### FR-FM04 — Config validation

At Revok startup, `load_config()` validates:
- `fuzzy_match_threshold`, if present, is a number in the range [0, 100].
- Values outside this range raise `ConfigError` with a descriptive message.

### FR-FM05 — Logging

When a fuzzy match is accepted, Revok logs at `DEBUG` level:
```
Fuzzy match: text="{text}" → entity="{id}" alias="{alias}" score={score:.1f}
```

When fuzzy matching is attempted but no alias meets the threshold, Revok logs at
`DEBUG` level:
```
Fuzzy match: no match for text="{text}" (best score={score:.1f} < threshold={threshold})
```

---

## Success Criteria

- Near-miss entity references (single character typo or one transposed word) are
  correctly matched at threshold 80 in ≥ 95% of test cases.
- Signal processing throughput for exact-match hits is unchanged (fuzzy path never
  entered for exact matches — verified by benchmark or profiling note in tests).
- Zero behavior change for deployments that do not set `fuzzy_match_threshold`
  (all existing tests continue to pass unmodified).
- `rapidfuzz` is the only new production dependency added.

---

## Assumptions

- Catalog sizes in typical Revok deployments are ≤ 50 entities with ≤ 5 aliases each
  (per Constitution § and existing config examples). At this scale, an O(n·aliases)
  linear scan over all aliases is fast enough that no index or early-exit optimization
  is required.
- `fuzz.partial_ratio` from rapidfuzz is available as a stable public API in `rapidfuzz>=3.0`.
- The `text` passed to `match()` may be arbitrarily long (full signal content); the
  scorer compares the entire string against each alias, which is the existing contract
  for exact matching.

---

## Dependencies

- **New production dependency**: `rapidfuzz>=3.0,<4` added to `pyproject.toml`.
- **Affected files**: `revok/entity_matcher.py`, `revok/config.py`,
  `config/revok.example.yaml`, `config/revok-zep.example.yaml`.
- **Test file**: `tests/test_entity_matcher.py` (extend existing file).
