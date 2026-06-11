# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Revok Contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""Tests for revok.entity_matcher.EntityMatcher."""

from __future__ import annotations

from revok.config import EntityDef, EntityMatcherConfig, PatternConfig
from revok.entity_matcher import EntityMatcher


def make_matcher(*patterns: tuple[str, str]) -> EntityMatcher:
    """Create an EntityMatcher from (name, regex) tuples."""
    config = EntityMatcherConfig(
        patterns=[PatternConfig(name=n, regex=r) for n, r in patterns]
    )
    return EntityMatcher(config)


def test_single_pattern_match():
    matcher = make_matcher(("person", r"\bAlice\b"))
    entities = matcher.match("Alice went to the market")
    assert len(entities) == 1
    assert entities[0].key == "alice"
    assert entities[0].raw_text == "Alice"
    assert entities[0].pattern_name == "person"


def test_multiple_pattern_matches():
    matcher = make_matcher(("person", r"\bAlice\b"), ("place", r"\bLondon\b"))
    entities = matcher.match("Alice visited London")
    keys = {e.key for e in entities}
    assert "alice" in keys
    assert "london" in keys
    assert len(entities) == 2


def test_no_match_returns_empty_list():
    matcher = make_matcher(("person", r"\bAlice\b"))
    entities = matcher.match("Nobody special here")
    assert entities == []


def test_overlapping_spans_returns_longest_match():
    """Regex alternation with the longer form first ensures longest match wins."""
    # "Alice Smith|Alice" — engine tries "Alice Smith" first at each position
    matcher = make_matcher(("person", r"Alice Smith|Alice"))
    entities = matcher.match("Alice Smith visited")
    assert len(entities) == 1
    assert entities[0].key == "alice smith"


def test_case_normalization_of_entity_keys():
    matcher = make_matcher(("person", r"\bALICE\b|\bAlice\b|\balice\b"))
    entities = matcher.match("ALICE and Alice and alice")
    # All three matches normalize to the same key "alice"; deduplication keeps first
    assert len(entities) == 1
    assert entities[0].key == "alice"


def test_pattern_with_zero_matches():
    matcher = make_matcher(("person", r"\bBob\b"))
    entities = matcher.match("Only Alice is here")
    assert entities == []


def test_deduplication_keeps_first_pattern_on_same_key():
    """Same text matched by two patterns → first pattern's entity is kept."""
    matcher = make_matcher(
        ("pattern_a", r"\bAlice\b"),
        ("pattern_b", r"\bAlice\b"),
    )
    entities = matcher.match("Alice was here")
    assert len(entities) == 1
    assert entities[0].pattern_name == "pattern_a"


def test_multiple_matches_same_pattern_deduplicates():
    matcher = make_matcher(("person", r"\bAlice\b"))
    entities = matcher.match("Alice said Alice would come")
    # "alice" appears twice but deduplication keeps only first
    assert len(entities) == 1
    assert entities[0].key == "alice"


# ---------------------------------------------------------------------------
# Alias catalog mode tests
# ---------------------------------------------------------------------------


def make_catalog_matcher(*defs: tuple[str, str, list[str]]) -> EntityMatcher:
    """Create an EntityMatcher from (id, display_name, aliases) tuples."""
    config = EntityMatcherConfig(
        entities=[
            EntityDef(id=eid, display_name=dname, aliases=aliases)
            for eid, dname, aliases in defs
        ]
    )
    return EntityMatcher(config)


def test_alias_catalog_key_is_canonical_id():
    """Matched alias text normalises to EntityDef.id, not raw_text.lower()."""
    matcher = make_catalog_matcher(("apex_hoodie", "Apex Hoodie", ["Apex Hoodie"]))
    entities = matcher.match("Customer asked about the Apex Hoodie today")
    assert len(entities) == 1
    assert entities[0].key == "apex_hoodie"
    assert entities[0].raw_text == "Apex Hoodie"


def test_alias_catalog_case_insensitive():
    matcher = make_catalog_matcher(("apex_hoodie", "Apex Hoodie", ["Apex Hoodie"]))
    entities = matcher.match("asked about the APEX HOODIE in blue")
    assert len(entities) == 1
    assert entities[0].key == "apex_hoodie"


def test_alias_catalog_multiple_aliases_same_entity():
    """Any alias produces the same canonical id; deduplication fires after first."""
    matcher = make_catalog_matcher(
        ("apex_hoodie", "Apex Hoodie", ["Apex Hoodie", "apex fleece", "SKU-1042"])
    )
    entities = matcher.match("The apex fleece and Apex Hoodie are the same product")
    # Both aliases match the same entity — deduplication keeps first occurrence
    assert len(entities) == 1
    assert entities[0].key == "apex_hoodie"


def test_alias_catalog_multiple_entities():
    matcher = make_catalog_matcher(
        ("apex_hoodie", "Apex Hoodie", ["Apex Hoodie"]),
        ("solar_backpack", "Solar Backpack", ["Solar Backpack"]),
    )
    entities = matcher.match("Looking at the Apex Hoodie and the Solar Backpack")
    keys = {e.key for e in entities}
    assert keys == {"apex_hoodie", "solar_backpack"}


def test_alias_catalog_sku_alias():
    """SKU strings (with hyphens) are matched correctly at word boundaries."""
    matcher = make_catalog_matcher(("apex_hoodie", "Apex Hoodie", ["SKU-1042"]))
    entities = matcher.match("Please check price for SKU-1042 in warehouse")
    assert len(entities) == 1
    assert entities[0].key == "apex_hoodie"


def test_alias_catalog_no_match_returns_empty():
    matcher = make_catalog_matcher(("apex_hoodie", "Apex Hoodie", ["Apex Hoodie"]))
    entities = matcher.match("Nothing about that product here")
    assert entities == []


def test_alias_catalog_and_legacy_patterns_coexist():
    """Both modes active simultaneously; alias catalog entries checked first."""
    config = EntityMatcherConfig(
        entities=[
            EntityDef(
                id="apex_hoodie", display_name="Apex Hoodie", aliases=["Apex Hoodie"]
            )
        ],
        patterns=[PatternConfig(name="person", regex=r"\bAlice\b")],
    )
    matcher = EntityMatcher(config)
    entities = matcher.match("Alice asked about the Apex Hoodie")
    keys = {e.key for e in entities}
    assert "apex_hoodie" in keys
    assert "alice" in keys


def test_empty_config_returns_empty_list():
    """No entities or patterns configured — match() always returns []."""
    matcher = EntityMatcher(EntityMatcherConfig())
    assert matcher.match("Apex Hoodie Alice anything") == []


# ---------------------------------------------------------------------------
# T008: Fuzzy disabled when threshold is None
# ---------------------------------------------------------------------------


def make_fuzzy_matcher(
    threshold: float | None,
    *entity_tuples: tuple[str, str, list[str]],
) -> EntityMatcher:
    """Build an EntityMatcher with fuzzy_match_threshold set."""
    entities = [
        EntityDef(id=eid, display_name=dname, aliases=aliases)
        for eid, dname, aliases in entity_tuples
    ]
    config = EntityMatcherConfig(entities=entities, fuzzy_match_threshold=threshold)
    return EntityMatcher(config)


def test_fuzzy_disabled_when_threshold_none():
    """When fuzzy_match_threshold is None, near-miss text returns no entities."""
    matcher = make_fuzzy_matcher(None, ("apex_hoodie", "Apex Hoodie", ["Apex Hoodie"]))
    # "apex hodie" is a typo — exact regex won't match; fuzzy should be skipped
    entities = matcher.match("apex hodie")
    assert entities == []


# ---------------------------------------------------------------------------
# T010: Exact match skips fuzzy path
# ---------------------------------------------------------------------------


def test_fuzzy_not_entered_when_exact_matches():
    """Exact alias match returns entity without entering fuzzy path."""
    matcher = make_fuzzy_matcher(80.0, ("apex_hoodie", "Apex Hoodie", ["Apex Hoodie"]))
    entities = matcher.match("Customer bought the Apex Hoodie today")
    assert len(entities) == 1
    assert entities[0].key == "apex_hoodie"
    # pattern_name for exact match is the canonical id, NOT "fuzzy"
    assert entities[0].pattern_name == "apex_hoodie"


# ---------------------------------------------------------------------------
# T012-T015: Near-miss match tests (Phase 5)
# ---------------------------------------------------------------------------


def test_fuzzy_match_single_char_typo():
    """Single-char typo 'apex hodie' → apex_hoodie at threshold 80."""
    matcher = make_fuzzy_matcher(80.0, ("apex_hoodie", "Apex Hoodie", ["Apex Hoodie"]))
    entities = matcher.match("apex hodie")
    assert len(entities) == 1
    assert entities[0].key == "apex_hoodie"


def test_fuzzy_match_in_longer_text():
    """Near-miss within a sentence is matched (partial_ratio finds substring)."""
    matcher = make_fuzzy_matcher(80.0, ("apex_hoodie", "Apex Hoodie", ["Apex Hoodie"]))
    # "get me apex hodie" scores 80.0 with partial_ratio("Apex Hoodie", ...)
    entities = matcher.match("get me apex hodie")
    assert len(entities) == 1
    assert entities[0].key == "apex_hoodie"


def test_fuzzy_no_match_below_threshold():
    """Completely unrelated text scores below threshold → []."""
    matcher = make_fuzzy_matcher(80.0, ("apex_hoodie", "Apex Hoodie", ["Apex Hoodie"]))
    entities = matcher.match("totally unrelated item here")
    assert entities == []


def test_fuzzy_entity_key_is_canonical_id():
    """Fuzzy match returns canonical entity id as the key."""
    matcher = make_fuzzy_matcher(80.0, ("apex_hoodie", "Apex Hoodie", ["Apex Hoodie"]))
    entities = matcher.match("apex hodie")
    assert entities[0].key == "apex_hoodie"


def test_fuzzy_pattern_name_is_fuzzy():
    """Fuzzy match sets pattern_name to 'fuzzy'."""
    matcher = make_fuzzy_matcher(80.0, ("apex_hoodie", "Apex Hoodie", ["Apex Hoodie"]))
    entities = matcher.match("apex hodie")
    assert entities[0].pattern_name == "fuzzy"


def test_fuzzy_raw_text_is_full_input():
    """Fuzzy match stores the full input text as raw_text."""
    matcher = make_fuzzy_matcher(80.0, ("apex_hoodie", "Apex Hoodie", ["Apex Hoodie"]))
    text = "apex hodie"
    entities = matcher.match(text)
    assert entities[0].raw_text == text


# ---------------------------------------------------------------------------
# T016-T017: Threshold sensitivity tests (Phase 6)
# ---------------------------------------------------------------------------


def test_fuzzy_threshold_boundary_accepts_at_threshold():
    """Text scoring exactly at the threshold IS accepted (>=, not >)."""
    # partial_ratio("Apex Hoodie", "apex hodie") == 80.0
    matcher = make_fuzzy_matcher(80.0, ("apex_hoodie", "Apex Hoodie", ["Apex Hoodie"]))
    entities = matcher.match("apex hodie")
    assert len(entities) == 1
    assert entities[0].key == "apex_hoodie"


def test_fuzzy_threshold_boundary_rejects_below():
    """Text scoring exactly 80.0 is rejected when threshold is 81."""
    # partial_ratio("Apex Hoodie", "apex hodie") == 80.0 < 81 → no match
    matcher = make_fuzzy_matcher(81.0, ("apex_hoodie", "Apex Hoodie", ["Apex Hoodie"]))
    entities = matcher.match("apex hodie")
    assert entities == []


def test_fuzzy_threshold_zero_always_matches():
    """Threshold 0 accepts any non-empty text that scores >= 0."""
    matcher = make_fuzzy_matcher(0.0, ("apex_hoodie", "Apex Hoodie", ["Apex Hoodie"]))
    # Any text will score >= 0 with partial_ratio
    entities = matcher.match("xyz")
    assert len(entities) == 1
    assert entities[0].key == "apex_hoodie"


def test_fuzzy_tie_break_first_entity_wins():
    """When two entity aliases score equally, the one with the higher score wins;
    when tied, the first entity in the config list wins (strictly-greater comparison)."""
    # partial_ratio("alpha corp", "alph corp") == 88.9  → entity_a wins
    # partial_ratio("beta corp",  "alph corp") == 80.0  → entity_b loses
    matcher = make_fuzzy_matcher(
        80.0,
        ("entity_a", "Alpha Corp", ["alpha corp"]),
        ("entity_b", "Beta Corp", ["beta corp"]),
    )
    entities = matcher.match("alph corp")
    assert len(entities) == 1
    assert entities[0].key == "entity_a"
