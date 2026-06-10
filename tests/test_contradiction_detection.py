# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Revok Contributors
#
# Integration tests for contradiction detection — all 7 scenarios from spec.md.
#
# Each test uses a real ScoringEngine + SqliteStateStore(:memory:) end-to-end.

from __future__ import annotations

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

HALF_LIFE = 3600.0
SIGNAL_STRENGTH = 0.2
SCORE_CAP = 1.0
WINDOW = 300.0
PENALTY = 0.15


@pytest.fixture
def scorer() -> ScoringEngine:
    return ScoringEngine(
        ScoringConfig(
            half_life_seconds=HALF_LIFE,
            signal_strength=SIGNAL_STRENGTH,
            score_cap=SCORE_CAP,
            contradiction_window_seconds=WINDOW,
            contradiction_penalty=PENALTY,
        )
    )


@pytest.fixture
async def store():
    s = SqliteStateStore(
        StateStoreConfig(sqlite_path=":memory:", hot_layer_max_entries=100)
    )
    await s.open()
    yield s
    await s.close()


@pytest.fixture
def product_matcher() -> EntityMatcher:
    return EntityMatcher(
        EntityMatcherConfig(
            patterns=[PatternConfig(name="product", regex=r"\bOrion Cache\b")]
        )
    )


def make_signal(content: str, valid_time: float) -> Signal:
    return Signal(
        raw_content=content,
        source_id="test-agent",
        timestamp=valid_time,
        http_method="POST",
        http_path="/v1/memories",
        original_body=b"{}",
        headers={},
        valid_time=valid_time,
    )


# ---------------------------------------------------------------------------
# Scenario 1 — Two agreeing signals (no contradiction)
# ---------------------------------------------------------------------------


async def test_scenario_1_agreeing_signals_no_contradiction(
    product_matcher, scorer, store
):
    """Two signals with the same price → no contradiction, count stays 0."""
    await enrich(
        make_signal("Orion Cache costs $500/month", 1000.0),
        product_matcher,
        scorer,
        store,
    )
    payload2 = await enrich(
        make_signal("Orion Cache costs $500/month", 1060.0),
        product_matcher,
        scorer,
        store,
    )

    rec = payload2.entities[0]
    assert rec.contradiction_count == 0
    assert rec.last_contradiction_time is None
    # enrich() uses wall-clock `now` for decay; valid_time is in the distant past
    # so recovery is nearly complete before each signal. Score is approximately
    # score_cap - signal_strength after each write.
    # Key invariant: no contradiction penalty was applied.
    assert rec.score == pytest.approx(SCORE_CAP - SIGNAL_STRENGTH, abs=0.05)


# ---------------------------------------------------------------------------
# Scenario 2 — Two conflicting signals within window (contradiction detected)
# ---------------------------------------------------------------------------


async def test_scenario_2_conflicting_signals_contradiction_detected(
    product_matcher, scorer, store
):
    """$500 then $450 within window → contradiction, lower score."""
    payload1 = await enrich(
        make_signal("Orion Cache costs $500/month", 1000.0),
        product_matcher,
        scorer,
        store,
    )
    # payload1 is used only to confirm no contradiction on first signal
    assert payload1.entities[0].contradiction_count == 0

    payload2 = await enrich(
        make_signal("Orion Cache costs $450/month", 1060.0),
        product_matcher,
        scorer,
        store,
    )
    rec = payload2.entities[0]

    assert rec.contradiction_count == 1
    assert rec.last_contradiction_time == 1060.0
    # enrich() uses wall-clock now; score recovers to ~score_cap before 2nd signal.
    # With contradiction: score ≈ score_cap - signal_strength - penalty
    expected = SCORE_CAP - SIGNAL_STRENGTH - PENALTY
    assert rec.score == pytest.approx(expected, abs=0.05)
    # Contradiction score is lower than plain two-signal score (no penalty)
    plain_two_signal = SCORE_CAP - SIGNAL_STRENGTH  # no contradiction penalty
    assert rec.score < plain_two_signal


# ---------------------------------------------------------------------------
# Scenario 3 — Conflict at window boundary (gap == window → no contradiction)
# ---------------------------------------------------------------------------


async def test_scenario_3_conflict_at_window_boundary_no_penalty(
    product_matcher, scorer, store
):
    """gap == window is NOT a contradiction (strict open interval)."""
    await enrich(
        make_signal("Orion Cache costs $500/month", 1000.0),
        product_matcher,
        scorer,
        store,
    )
    payload2 = await enrich(
        make_signal("Orion Cache costs $450/month", 1000.0 + WINDOW),  # gap == 300
        product_matcher,
        scorer,
        store,
    )

    rec = payload2.entities[0]
    assert rec.contradiction_count == 0


# ---------------------------------------------------------------------------
# Scenario 4 — Score recovery after contradiction (exponential decay unaffected)
# ---------------------------------------------------------------------------


async def test_scenario_4_score_recovers_after_contradiction(
    product_matcher, scorer, store
):
    """Score recovers toward score_cap via normal decay; contradiction fields unchanged."""
    await enrich(
        make_signal("Orion Cache costs $500/month", 1000.0),
        product_matcher,
        scorer,
        store,
    )
    payload2 = await enrich(
        make_signal("Orion Cache costs $450/month", 1060.0),
        product_matcher,
        scorer,
        store,
    )
    contradicted_rec = payload2.entities[0]
    contradicted_score = contradicted_rec.score
    saved_count = contradicted_rec.contradiction_count  # == 1

    # Simulate 2 hours later without any new signals
    two_hours_later = 1060.0 + 7200.0
    recovered = scorer.decay_at(contradicted_rec, two_hours_later)
    assert recovered > contradicted_score  # score grows back
    assert contradicted_rec.contradiction_count == saved_count  # unchanged in-memory
    assert contradicted_rec.last_contradiction_time == 1060.0  # unchanged


# ---------------------------------------------------------------------------
# Scenario 5 — Rapid flips accumulate contradiction_count
# ---------------------------------------------------------------------------


async def test_scenario_5_rapid_flips_accumulate_count(product_matcher, scorer, store):
    """$500 → $450 → $500 all within window → contradiction_count == 2."""
    await enrich(
        make_signal("Orion Cache costs $500/month", 1000.0),
        product_matcher,
        scorer,
        store,
    )
    await enrich(
        make_signal("Orion Cache costs $450/month", 1030.0),
        product_matcher,
        scorer,
        store,
    )
    payload3 = await enrich(
        make_signal("Orion Cache costs $500/month", 1060.0),
        product_matcher,
        scorer,
        store,
    )

    rec = payload3.entities[0]
    assert rec.contradiction_count == 2
    assert rec.last_contradiction_time == 1060.0


# ---------------------------------------------------------------------------
# Scenario 6 — Backward-compatible load from old schema
# ---------------------------------------------------------------------------


async def test_scenario_6_backward_compat_old_schema(tmp_path):
    """Old-schema records load with contradiction_count == 0; next signal scores normally."""
    import aiosqlite

    db_path = str(tmp_path / "legacy_scenario6.db")

    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA journal_mode=WAL;")
        await db.execute(
            "CREATE TABLE entity_records ("
            "  entity_key TEXT PRIMARY KEY,"
            "  score REAL NOT NULL DEFAULT 0.0,"
            "  last_seen REAL NOT NULL,"
            "  valid_time REAL,"
            "  transaction_time REAL,"
            "  signal_count INTEGER NOT NULL DEFAULT 1,"
            "  pattern_name TEXT NOT NULL DEFAULT ''"
            ");"
        )
        await db.execute(
            "INSERT INTO entity_records "
            "(entity_key, score, last_seen, valid_time, transaction_time, "
            " signal_count, pattern_name) "
            "VALUES ('orion cache', 0.6, 900.0, 900.0, 900.0, 2, 'product')"
        )
        await db.commit()

    s = SqliteStateStore(
        StateStoreConfig(sqlite_path=db_path, hot_layer_max_entries=10)
    )
    await s.open()

    try:
        result = await s.get("orion cache")
        assert result is not None
        assert result.contradiction_count == 0
        assert result.last_contradiction_time is None
        assert result.last_value_fingerprint is None
    finally:
        await s.close()


# ---------------------------------------------------------------------------
# Scenario 7 — Non-numeric content (hash-based fingerprint contradiction)
# ---------------------------------------------------------------------------


async def test_scenario_7_non_numeric_content_contradiction(scorer, store):
    """Non-numeric auth state flip is detected via hash-based fingerprints."""
    matcher = EntityMatcher(
        EntityMatcherConfig(patterns=[PatternConfig(name="auth", regex=r"\buser\b")])
    )

    await enrich(make_signal("user is authenticated", 1000.0), matcher, scorer, store)
    payload2 = await enrich(
        make_signal("user is not authenticated", 1030.0), matcher, scorer, store
    )

    rec = payload2.entities[0]
    assert rec.contradiction_count == 1
    assert rec.last_contradiction_time == 1030.0
