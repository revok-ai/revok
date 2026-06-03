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

import pytest

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
        entities=[EntityDef(id="apex_hoodie", display_name="Apex Hoodie", aliases=["Apex Hoodie"])],
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
