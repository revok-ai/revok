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

"""Tests for revok.scoring.ScoringEngine (confidence / pressure model).

Confidence DEGRADES when a signal arrives; it RECOVERS toward score_cap when
no new signals arrive.
"""

import pytest

from revok.config import ScoringConfig, SignalPressureConfig
from revok.models import EntityRecord
from revok.scoring import ScoringEngine

HALF_LIFE = 86400.0  # 1 day
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
        valid_time=last_seen,
        transaction_time=last_seen,
        signal_count=1,
        pattern_name="person",
    )


# ---------------------------------------------------------------------------
# Core formula — score() degradation
# ---------------------------------------------------------------------------


def test_first_signal_equals_score_cap_minus_signal_strength(engine):
    """No prior record → score = score_cap - signal_strength."""
    score = engine.score(None, now=1000.0)
    assert score == pytest.approx(SCORE_CAP - SIGNAL_STRENGTH)


def test_score_at_zero_elapsed_subtracts_signal_strength(engine):
    """At Δt=0 no recovery occurs, so new score = existing - signal_strength."""
    existing_score = 0.8
    now = 5000.0
    record = make_record(score=existing_score, last_seen=now)
    result = engine.score(record, now=now)
    assert result == pytest.approx(existing_score - SIGNAL_STRENGTH)


def test_successive_rapid_signals_degrade_score(engine):
    """Each successive signal at Δt≈0 lowers the score further."""
    now = 1000.0
    # Start just after the first signal
    record = make_record(score=SCORE_CAP - SIGNAL_STRENGTH, last_seen=now)
    score2 = engine.score(record, now=now)
    assert score2 < SCORE_CAP - SIGNAL_STRENGTH


def test_score_never_goes_below_zero(engine):
    """Score is floored at 0.0 even when signal_strength exceeds current score."""
    record = make_record(score=0.1, last_seen=1000.0)
    result = engine.score(record, now=1000.0)
    assert result >= 0.0


def test_first_signal_with_very_high_strength_floors_at_zero():
    """signal_strength > score_cap still produces 0.0, not a negative score."""
    high_strength_engine = ScoringEngine(
        ScoringConfig(half_life_seconds=HALF_LIFE, signal_strength=5.0, score_cap=1.0)
    )
    score = high_strength_engine.score(None, now=0.0)
    assert score == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Core formula — decay_at() recovery
# ---------------------------------------------------------------------------


def test_decay_at_recovers_toward_score_cap_over_time(engine):
    """decay_at() returns a higher value as Δt increases (confidence recovers)."""
    record = make_record(score=0.2, last_seen=0.0)
    score_t1 = engine.decay_at(record, now=1000.0)
    score_t2 = engine.decay_at(record, now=2000.0)
    assert score_t2 > score_t1


def test_decay_at_half_life_recovers_half_the_gap(engine):
    """After exactly one half-life the gap to score_cap is halved."""
    stored_score = 0.2
    record = make_record(score=stored_score, last_seen=0.0)
    recovered = engine.decay_at(record, now=HALF_LIFE)
    expected = SCORE_CAP - (SCORE_CAP - stored_score) / 2.0
    assert recovered == pytest.approx(expected, rel=1e-6)


def test_decay_at_zero_elapsed_returns_stored_score(engine):
    """At Δt=0 there is no recovery; decay_at returns the stored score unchanged."""
    record = make_record(score=0.4, last_seen=1000.0)
    result = engine.decay_at(record, now=1000.0)
    assert result == pytest.approx(0.4)


def test_fully_confident_record_stays_at_score_cap(engine):
    """A record already at score_cap does not change over time."""
    record = make_record(score=SCORE_CAP, last_seen=0.0)
    assert engine.decay_at(record, now=HALF_LIFE) == pytest.approx(SCORE_CAP)


# ---------------------------------------------------------------------------
# Severity-aware degradation and recovery (user-facing requirements)
# ---------------------------------------------------------------------------


def test_high_severity_signal_drops_score_below_0_7():
    """A single high-severity signal immediately drops confidence below 0.7."""
    high_engine = ScoringEngine(
        ScoringConfig(half_life_seconds=HALF_LIFE, signal_strength=0.4, score_cap=1.0)
    )
    score = high_engine.score(None, now=0.0)  # fresh entity, first signal
    assert score < 0.7


def test_critical_severity_signal_drops_score_below_0_3():
    """A single critical-severity signal immediately drops confidence below 0.3."""
    critical_engine = ScoringEngine(
        ScoringConfig(half_life_seconds=HALF_LIFE, signal_strength=0.8, score_cap=1.0)
    )
    score = critical_engine.score(None, now=0.0)
    assert score < 0.3


def test_score_recovers_toward_one_over_time():
    """After a signal degrades confidence, the score grows back toward 1.0."""
    engine = ScoringEngine(
        ScoringConfig(half_life_seconds=HALF_LIFE, signal_strength=0.8, score_cap=1.0)
    )
    # Critical signal fires: score drops to 0.2
    initial_score = engine.score(None, now=0.0)
    assert initial_score < 0.3

    record = make_record(score=initial_score, last_seen=0.0)

    # After one half-life (no new signal), gap to 1.0 is halved
    score_after_half_life = engine.decay_at(record, now=HALF_LIFE)
    assert score_after_half_life > initial_score

    # After three half-lives the score should be above 0.7
    score_after_3hl = engine.decay_at(record, now=3 * HALF_LIFE)
    assert score_after_3hl > 0.7


def test_multiple_signals_keep_score_low():
    """Rapid successive signals accumulate pressure and keep confidence well below 0.7."""
    engine = ScoringEngine(
        ScoringConfig(half_life_seconds=HALF_LIFE, signal_strength=0.4, score_cap=1.0)
    )
    # First signal
    score = engine.score(None, now=0.0)
    # Four more signals arriving 1 second apart (half-life is 1 day — no meaningful recovery)
    for i in range(1, 5):
        record = make_record(score=score, last_seen=float(i - 1))
        score = engine.score(record, now=float(i))
    assert score < 0.3


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


# ---------------------------------------------------------------------------
# Contradiction detection: _extract_fingerprint (T012)
# ---------------------------------------------------------------------------

CONTRADICTION_CONFIG = ScoringConfig(
    half_life_seconds=HALF_LIFE,
    signal_strength=SIGNAL_STRENGTH,
    score_cap=SCORE_CAP,
    contradiction_window_seconds=300.0,
    contradiction_penalty=0.15,
)


def test_extract_fingerprint_dollar_price():
    assert ScoringEngine.extract_fingerprint("$500/month") == "500.0"


def test_extract_fingerprint_plain_decimal():
    assert ScoringEngine.extract_fingerprint("price is 450.50 USD") == "450.5"


def test_extract_fingerprint_no_numeric_uses_hash():
    import hashlib

    content = "user is authenticated"
    expected = hashlib.sha256(content.lower().encode()).hexdigest()
    assert ScoringEngine.extract_fingerprint(content) == expected


def test_extract_fingerprint_empty_returns_none():
    assert ScoringEngine.extract_fingerprint("") is None


def test_extract_fingerprint_normalizes_formatting():
    """$500, 500, 500.0, $500.00 all produce the same fingerprint."""
    fp = ScoringEngine.extract_fingerprint
    assert fp("$500") == fp("500") == fp("500.0") == fp("$500.00") == "500.0"


# ---------------------------------------------------------------------------
# Contradiction detection: detect_contradiction (T013)
# ---------------------------------------------------------------------------


@pytest.fixture
def c_engine() -> ScoringEngine:
    return ScoringEngine(CONTRADICTION_CONFIG)


def make_contradictable_record(
    fingerprint: str | None, valid_time: float = 1000.0
) -> EntityRecord:
    return EntityRecord(
        entity_key="orion_cache",
        score=0.8,
        valid_time=valid_time,
        transaction_time=valid_time,
        signal_count=1,
        pattern_name="product",
        last_value_fingerprint=fingerprint,
    )


def test_detect_contradiction_within_window(c_engine):
    existing = make_contradictable_record("500.0", valid_time=1000.0)
    assert c_engine.detect_contradiction(existing, "450.0", 1060.0) is True


def test_detect_contradiction_at_window_boundary_no_contradiction(c_engine):
    """gap == window is NOT a contradiction (strict open interval)."""
    existing = make_contradictable_record("500.0", valid_time=1000.0)
    assert c_engine.detect_contradiction(existing, "450.0", 1300.0) is False  # gap=300


def test_detect_contradiction_outside_window(c_engine):
    existing = make_contradictable_record("500.0", valid_time=1000.0)
    assert c_engine.detect_contradiction(existing, "450.0", 1400.0) is False  # gap=400


def test_detect_contradiction_agreeing_fingerprints(c_engine):
    existing = make_contradictable_record("500.0", valid_time=1000.0)
    assert c_engine.detect_contradiction(existing, "500.0", 1060.0) is False


def test_detect_contradiction_no_existing_record(c_engine):
    assert c_engine.detect_contradiction(None, "500.0", 1060.0) is False


def test_detect_contradiction_none_new_fingerprint(c_engine):
    existing = make_contradictable_record("500.0", valid_time=1000.0)
    assert c_engine.detect_contradiction(existing, None, 1060.0) is False


def test_detect_contradiction_none_existing_fingerprint(c_engine):
    existing = make_contradictable_record(None, valid_time=1000.0)
    assert c_engine.detect_contradiction(existing, "500.0", 1060.0) is False


# ---------------------------------------------------------------------------
# Contradiction penalty in score() (T014)
# ---------------------------------------------------------------------------


def test_score_with_contradiction_penalty_lower_than_without(c_engine):
    existing = make_contradictable_record("500.0", valid_time=1000.0)
    score_normal = c_engine.score(existing, now=1060.0, is_contradiction=False)
    score_contradiction = c_engine.score(existing, now=1060.0, is_contradiction=True)
    assert score_contradiction < score_normal
    assert score_contradiction == pytest.approx(score_normal - 0.15, abs=1e-9)


def test_score_contradiction_floors_at_zero(c_engine):
    """Even with large penalty, score never goes below 0.0."""
    existing = make_contradictable_record("500.0", valid_time=1000.0)
    existing.score = 0.01  # type: ignore[assignment]
    result = c_engine.score(existing, now=1000.0, is_contradiction=True)
    assert result == pytest.approx(0.0)


def test_score_existing_calls_unchanged_without_keyword(c_engine):
    """Existing call sites that pass no keyword work as before."""
    existing = make_contradictable_record("500.0", valid_time=1000.0)
    result = c_engine.score(existing, now=1000.0)
    assert result == pytest.approx(existing.score - SIGNAL_STRENGTH)


def test_score_with_pressure_scales_signal_strength(engine):
    score = engine.score_with_pressure(None, now=0.0, pressure=0.5)
    # score_cap - (signal_strength * pressure) = 1.0 - 0.15
    assert score == pytest.approx(0.85)


def test_score_with_pressure_clamps_out_of_range(engine):
    score_low = engine.score_with_pressure(None, now=0.0, pressure=-1.0)
    score_high = engine.score_with_pressure(None, now=0.0, pressure=5.0)
    assert score_low == pytest.approx(1.0)
    assert score_high == pytest.approx(1.0 - SIGNAL_STRENGTH)


def test_pressure_for_severity_uses_mapping_and_default():
    engine = ScoringEngine(
        ScoringConfig(
            half_life_seconds=HALF_LIFE,
            signal_strength=SIGNAL_STRENGTH,
            score_cap=SCORE_CAP,
            signal_pressure=SignalPressureConfig(
                severity_weights={"low": 0.2, "medium": 0.4, "high": 0.7},
                default_severity="medium",
            ),
        )
    )
    assert engine.pressure_for_severity("high") == pytest.approx(0.7)
    assert engine.pressure_for_severity("unknown") == pytest.approx(0.4)
