# Quickstart — Fuzzy Entity Matching (004)
**Feature**: 004-fuzzy-entity-matching
**Date**: 2026-06-10

---

## What This Feature Does

Revok's entity matcher now falls back to **fuzzy string matching** when an exact alias
match fails. If an incoming signal contains a near-miss reference to a known entity (a
typo, OCR artifact, or abbreviation), Revok still maps it to the correct canonical entity
ID rather than silently dropping the signal.

---

## Configuration

Add one optional key to the `entity_matcher` section of `revok.yaml`:

```yaml
entity_matcher:
  fuzzy_match_threshold: 80   # similarity score 0–100; omit to disable fuzzy matching

  entities:
    - id: "apex_hoodie"
      display_name: "Apex Fleece Hoodie"
      aliases:
        - "Apex Hoodie"
        - "apex fleece"
        - "SKU-1042"
```

- **Disabled by default** — existing configs without `fuzzy_match_threshold` are
  unaffected. No behavior change unless you opt in.
- **Threshold range**: 0–100. A value of 80 is a good starting point for short product
  names; increase to 90+ for tighter matching in high-precision deployments.
- **Threshold of 0**: any non-empty text matches the closest alias (maximum recall).

---

## How It Works

Matching follows a three-tier priority:

| Priority | Mode | Condition |
|----------|------|-----------|
| 1 (highest) | Exact alias match | Always active when `entities` configured |
| 2 | Fuzzy alias match | Only when `fuzzy_match_threshold` is set AND exact match returns nothing |
| 3 | Legacy regex patterns | Only when `fuzzy_match_threshold` is **not** set |

The fuzzy scorer (`rapidfuzz.fuzz.partial_ratio`) finds the best matching substring
of alias length within the signal text, so it works correctly whether the input is a
short near-miss string or a long sentence containing the near-miss.

---

## Example: Typo in Signal Content

```
Config:  fuzzy_match_threshold: 80
         entity: apex_hoodie  aliases: ["Apex Hoodie"]

Signal:  "Customer returned the apex hodie — wants refund"
         └─ exact match: no match
         └─ fuzzy:  partial_ratio("Apex Hoodie", "...apex hodie...") = 80 ≥ 80 ✓
         └─ result: Entity(key="apex_hoodie", pattern_name="fuzzy")

Signal:  "totally unrelated item purchased"
         └─ exact match: no match
         └─ fuzzy:  best score = 38 < 80 ✗
         └─ result: [] (no entity)
```

---

## Logging

Fuzzy match accepted (DEBUG):
```
Fuzzy match: text="apex hodie" → entity="apex_hoodie" alias="Apex Hoodie" score=80.0
```

Fuzzy match attempted but below threshold (DEBUG):
```
Fuzzy match: no match for text="totally unrelated item" (best score=38.0 < threshold=80)
```

---

## Validation Error

If `fuzzy_match_threshold` is outside 0–100, Revok refuses to start:

```
revok.config.ConfigError: entity_matcher.fuzzy_match_threshold must be between 0 and 100, got 150
```
