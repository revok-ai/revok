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

"""Tests for revok.metadata_writer.enrich()."""

import pytest

from revok.config import (
    EntityMatcherConfig,
    PatternConfig,
    ScoringConfig,
    StateStoreConfig,
)
from revok.entity_matcher import EntityMatcher
from revok.metadata_writer import enrich
from revok.models import Signal
from revok.scoring import ScoringEngine
from revok.state_store import SqliteStateStore


def make_signal(
    content: str = "Alice was here",
    body: bytes = b'{"text": "test"}',
    source_id: str = "test-agent",
    valid_time: float | None = None,
) -> Signal:
    return Signal(
        raw_content=content,
        source_id=source_id,
        timestamp=1000.0,
        http_method="POST",
        http_path="/v1/memories",
        original_body=body,
        headers={},
        valid_time=valid_time,
    )


@pytest.fixture
def matcher() -> EntityMatcher:
    config = EntityMatcherConfig(
        patterns=[PatternConfig(name="person", regex=r"\bAlice\b|\bBob\b")]
    )
    return EntityMatcher(config)


@pytest.fixture
def scorer() -> ScoringEngine:
    return ScoringEngine(
        ScoringConfig(
            half_life_seconds=86400.0,
            signal_strength=0.3,
            score_cap=1.0,
            contradiction_window_seconds=300.0,
            contradiction_penalty=0.15,
        )
    )


@pytest.fixture
async def store(tmp_path):
    cfg = StateStoreConfig(
        sqlite_path=str(tmp_path / "enrich_test.db"),
        hot_layer_max_entries=100,
    )
    s = SqliteStateStore(cfg)
    await s.open()
    yield s
    await s.close()


async def test_signal_with_match_produces_enriched_payload(matcher, scorer, store):
    signal = make_signal("Alice was here")
    payload = await enrich(signal, matcher, scorer, store)

    assert len(payload.entities) == 1
    assert payload.entities[0].entity_key == "alice"
    assert payload.entities[0].score > 0.0
    assert payload.revok_version != ""


async def test_signal_with_no_match_produces_empty_entities(matcher, scorer, store):
    signal = make_signal("nobody special", body=b'{"text": "test"}')
    payload = await enrich(signal, matcher, scorer, store)

    assert payload.entities == []
    assert payload.original_body == {"text": "test"}


async def test_original_body_is_preserved_in_payload(matcher, scorer, store):
    body = b'{"content": "Alice was here", "metadata": {"user": "u1"}}'
    signal = make_signal("Alice was here", body=body)
    payload = await enrich(signal, matcher, scorer, store)

    assert payload.original_body["content"] == "Alice was here"
    assert payload.original_body["metadata"] == {"user": "u1"}


async def test_enrichment_failure_does_not_raise(scorer, store):
    """A broken matcher must not propagate the exception (acceptance scenario 1.4)."""

    class BrokenMatcher:
        def match(self, text: str):
            raise RuntimeError("simulated matcher failure")

    signal = make_signal("Alice was here")
    payload = await enrich(signal, BrokenMatcher(), scorer, store)
    assert payload.entities == []


async def test_second_signal_produces_lower_score(matcher, scorer, store):
    """Two successive signals for the same entity degrade confidence further."""
    signal = make_signal("Alice was here")

    payload1 = await enrich(signal, matcher, scorer, store)
    score1 = payload1.entities[0].score

    payload2 = await enrich(signal, matcher, scorer, store)
    score2 = payload2.entities[0].score

    assert score2 < score1


async def test_non_json_body_falls_back_to_empty_dict(matcher, scorer, store):
    signal = make_signal("Alice", body=b"not json at all")
    payload = await enrich(signal, matcher, scorer, store)
    assert payload.original_body == {}
    # Entity extraction from raw_content still works
    assert len(payload.entities) == 1


async def test_enriched_payload_upstream_dict_contains_x_revok(matcher, scorer, store):
    signal = make_signal("Alice was here", body=b'{"msg": "hi"}')
    payload = await enrich(signal, matcher, scorer, store)
    upstream = payload.to_upstream_dict()

    assert "x_revok" in upstream
    assert upstream["msg"] == "hi"
    assert len(upstream["x_revok"]["entities"]) == 1


async def test_header_tagged_entity_bypasses_text_matching(scorer, store):
    """X-Revok-Entity header takes priority; text matching is skipped entirely."""
    # Matcher configured for "Alice" — the signal content also mentions Alice,
    # but the header should win and produce only one entity with the header id.
    config = EntityMatcherConfig(
        patterns=[PatternConfig(name="person", regex=r"\bAlice\b")]
    )
    header_matcher = EntityMatcher(config)
    signal = Signal(
        raw_content="Alice was here",
        source_id="test-agent",
        timestamp=1000.0,
        http_method="POST",
        http_path="/v1/memories",
        original_body=b'{"text": "test"}',
        headers={"X-Revok-Entity": "apex_hoodie"},
    )
    payload = await enrich(signal, header_matcher, scorer, store)

    assert len(payload.entities) == 1
    assert payload.entities[0].entity_key == "apex_hoodie"
    assert payload.entities[0].pattern_name == "header"


async def test_header_tagged_entity_key_is_lowercased(scorer, store):
    """Header value is normalised to lowercase for the store key."""
    config = EntityMatcherConfig()
    empty_matcher = EntityMatcher(config)
    signal = Signal(
        raw_content="",
        source_id="test-agent",
        timestamp=1000.0,
        http_method="POST",
        http_path="/v1/memories",
        original_body=b"{}",
        headers={"x-revok-entity": "  Apex_Hoodie  "},
    )
    payload = await enrich(signal, empty_matcher, scorer, store)

    assert len(payload.entities) == 1
    assert payload.entities[0].entity_key == "apex_hoodie"


async def test_header_case_insensitive_lookup(scorer, store):
    """X-Revok-Entity header is matched case-insensitively."""
    config = EntityMatcherConfig()
    empty_matcher = EntityMatcher(config)
    signal = Signal(
        raw_content="",
        source_id="test-agent",
        timestamp=1000.0,
        http_method="POST",
        http_path="/v1/memories",
        original_body=b"{}",
        headers={"x-REVOK-ENTITY": "solar_backpack"},
    )
    payload = await enrich(signal, empty_matcher, scorer, store)

    assert len(payload.entities) == 1
    assert payload.entities[0].entity_key == "solar_backpack"


# ---------------------------------------------------------------------------
# Contradiction pipeline integration (T022)
# ---------------------------------------------------------------------------


@pytest.fixture
def product_matcher() -> EntityMatcher:
    config = EntityMatcherConfig(
        patterns=[PatternConfig(name="product", regex=r"\bOrion Cache\b")]
    )
    return EntityMatcher(config)


async def test_enrich_contradiction_detected_increments_count(
    product_matcher, scorer, store
):
    """Two conflicting signals within window → contradiction_count == 1."""
    signal1 = make_signal("Orion Cache costs $500/month", valid_time=1000.0)
    signal2 = make_signal("Orion Cache costs $450/month", valid_time=1060.0)

    await enrich(signal1, product_matcher, scorer, store)
    payload2 = await enrich(signal2, product_matcher, scorer, store)

    rec = payload2.entities[0]
    assert rec.entity_key == "orion cache"
    assert rec.contradiction_count == 1
    assert rec.last_contradiction_time == 1060.0


async def test_enrich_agreeing_signals_no_penalty(product_matcher, scorer, store):
    """Two identical-value signals → contradiction_count stays 0."""
    signal1 = make_signal("Orion Cache costs $500/month", valid_time=1000.0)
    signal2 = make_signal("Orion Cache costs $500/month", valid_time=1060.0)

    await enrich(signal1, product_matcher, scorer, store)
    payload2 = await enrich(signal2, product_matcher, scorer, store)

    rec = payload2.entities[0]
    assert rec.contradiction_count == 0
    assert rec.last_contradiction_time is None


async def test_enrich_first_signal_sets_fingerprint_no_contradiction(
    product_matcher, scorer, store
):
    """First signal for an entity is never a contradiction."""
    signal = make_signal("Orion Cache costs $500/month", valid_time=1000.0)
    payload = await enrich(signal, product_matcher, scorer, store)

    rec = payload.entities[0]
    assert rec.contradiction_count == 0
    assert rec.last_value_fingerprint == "500.0"
    assert rec.last_contradiction_time is None

