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

"""Tests for revok.scoring.ScoringEngine."""

import math

import pytest

from revok.config import ScoringConfig
from revok.models import EntityRecord
from revok.scoring import ScoringEngine

HALF_LIFE = 86400.0   # 1 day
SIGNAL_STRENGTH = 0.3
SCORE_CAP = 1.0


@pytest.fixture
def engine() -> ScoringEngine:
    return ScoringEngine(
        ScoringConfig(
            half_life_seconds=HALF_LIFE,
            signal_strength=SIGNAL_STRENGTH,
            score_cap=SCORE_CAP,
        )
    )


def make_record(score: float, last_seen: float) -> EntityRecord:
    return EntityRecord(
        entity_key="alice",
        score=score,
        last_seen=last_seen,
        signal_count=1,
        pattern_name="person",
    )


def test_first_signal_score_equals_signal_strength(engine):
    """No prior record → score equals signal_strength."""
    score = engine.score(None, now=1000.0)
    assert score == pytest.approx(SIGNAL_STRENGTH)


def test_second_signal_accumulates_higher_score(engine):
    """Successive signals for the same entity raise the score."""
    now = 1000.0
    record = make_record(score=SIGNAL_STRENGTH, last_seen=now)
    score2 = engine.score(record, now=now)  # zero elapsed time
    assert score2 > SIGNAL_STRENGTH


def test_score_after_zero_elapsed_equals_prev_plus_signal_strength(engine):
    """At Δt=0 decay is 1.0, so new score = existing + signal_strength."""
    existing_score = 0.4
    now = 5000.0
    record = make_record(score=existing_score, last_seen=now)
    result = engine.score(record, now=now)
    assert result == pytest.approx(existing_score + SIGNAL_STRENGTH)


def test_score_decreases_monotonically_between_signals(engine):
    """decay_at() returns a lower value for larger Δt (SC-007)."""
    record = make_record(score=0.9, last_seen=0.0)
    score_t1 = engine.decay_at(record, now=1000.0)
    score_t2 = engine.decay_at(record, now=2000.0)
    assert score_t2 < score_t1


def test_score_never_exceeds_score_cap(engine):
    """Repeated signals cannot push the score above score_cap."""
    record = make_record(score=SCORE_CAP - 0.01, last_seen=1000.0)
    result = engine.score(record, now=1000.0)
    assert result <= SCORE_CAP


def test_score_cap_exactly_applied():
    """signal_strength alone at cap produces exactly score_cap."""
    high_strength_engine = ScoringEngine(
        ScoringConfig(half_life_seconds=HALF_LIFE, signal_strength=5.0, score_cap=1.0)
    )
    score = high_strength_engine.score(None, now=0.0)
    assert score == pytest.approx(1.0)


def test_decay_at_half_life_halves_score(engine):
    """After exactly one half-life the decayed score is half the original."""
    record = make_record(score=0.8, last_seen=0.0)
    decayed = engine.decay_at(record, now=HALF_LIFE)
    assert decayed == pytest.approx(0.4, rel=1e-6)


# ---------------------------------------------------------------------------
# T039: ScoringEngine.__init__ raises ValueError on bad half_life
# ---------------------------------------------------------------------------


def test_zero_half_life_raises_value_error():
    """half_life_seconds=0 must raise ValueError at construction time."""
    with pytest.raises(ValueError, match="half_life_seconds must be > 0"):
        ScoringEngine(
            ScoringConfig(half_life_seconds=0.0, signal_strength=0.3, score_cap=1.0)
        )


def test_negative_half_life_raises_value_error():
    """Negative half_life_seconds must raise ValueError at construction time."""
    with pytest.raises(ValueError, match="half_life_seconds must be > 0"):
        ScoringEngine(
            ScoringConfig(half_life_seconds=-1.0, signal_strength=0.3, score_cap=1.0)
        )
