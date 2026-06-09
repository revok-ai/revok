# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Revok Contributors
#
# Tests for bitemporal scoring — FR-001 through FR-008 and Scenarios 1, 4, 5, 6.

from __future__ import annotations

import dataclasses
import logging
import time

import pytest

from revok.config import (
    EntityMatcherConfig,
    PatternConfig,
    ScoringConfig,
    StateStoreConfig,
)
from revok.entity_matcher import EntityMatcher
from revok.metadata_writer import enrich
from revok.models import EntityRecord, Signal
from revok.scoring import ScoringEngine
from revok.state_store import SqliteStateStore

HALF_LIFE = 86400.0
SIGNAL_STRENGTH = 0.3
SCORE_CAP = 1.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(
    valid_time: float,
    score: float = 0.5,
    transaction_time: float | None = None,
) -> EntityRecord:
    return EntityRecord(
        entity_key="test:entity",
        score=score,
        valid_time=valid_time,
        transaction_time=transaction_time if transaction_time is not None else valid_time,
        signal_count=1,
        pattern_name="person",
    )


def _make_signal(
    content: str = "Alice was here",
    valid_time: float | None = None,
) -> Signal:
    return Signal(
        raw_content=content,
        source_id="test-agent",
        timestamp=time.time(),
        http_method="POST",
        http_path="/v1/memories",
        original_body=b'{"text": "test"}',
        headers={},
        valid_time=valid_time,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def scorer() -> ScoringEngine:
    return ScoringEngine(
        ScoringConfig(
            half_life_seconds=HALF_LIFE,
            signal_strength=SIGNAL_STRENGTH,
            score_cap=SCORE_CAP,
        )
    )


@pytest.fixture
def matcher() -> EntityMatcher:
    config = EntityMatcherConfig(
        patterns=[PatternConfig(name="person", regex=r"\bAlice\b")]
    )
    return EntityMatcher(config)


@pytest.fixture
async def store(tmp_path):
    cfg = StateStoreConfig(
        sqlite_path=str(tmp_path / "bitemp.db"),
        hot_layer_max_entries=10,
    )
    s = SqliteStateStore(cfg)
    await s.open()
    yield s
    await s.close()


# ---------------------------------------------------------------------------
# FR-001: EntityRecord carries both timestamp fields
# ---------------------------------------------------------------------------


def test_entity_record_has_valid_time_field():
    """valid_time is a stored dataclass field (FR-001)."""
    assert "valid_time" in EntityRecord.__dataclass_fields__


def test_entity_record_has_transaction_time_field():
    """transaction_time is a stored dataclass field (FR-001)."""
    assert "transaction_time" in EntityRecord.__dataclass_fields__


# ---------------------------------------------------------------------------
# FR-007: last_seen is a read-only property alias for valid_time
# ---------------------------------------------------------------------------


def test_last_seen_property_returns_valid_time():
    """record.last_seen == record.valid_time (FR-007)."""
    record = _make_record(valid_time=9999.0)
    assert record.last_seen == record.valid_time


def test_asdict_excludes_last_seen():
    """dataclasses.asdict() omits last_seen (property, not a field) — FR-007."""
    record = _make_record(valid_time=9999.0)
    d = dataclasses.asdict(record)
    assert "last_seen" not in d
    assert "valid_time" in d
    assert "transaction_time" in d


# ---------------------------------------------------------------------------
# FR-002: Decay is driven by valid_time, not transaction_time
# ---------------------------------------------------------------------------


def test_decay_uses_valid_time_not_transaction_time(scorer):
    """Two records with the same transaction_time but different valid_time
    produce different decay_at() scores, confirming valid_time drives decay (FR-002)."""
    now = 1_000_000.0
    # fresh: event happened now → no elapsed valid_time
    fresh = _make_record(valid_time=now, score=0.3, transaction_time=now)
    # stale: event happened one day ago → one full half-life of recovery time
    stale = _make_record(valid_time=now - HALF_LIFE, score=0.3, transaction_time=now)

    score_fresh = scorer.decay_at(fresh, now)
    score_stale = scorer.decay_at(stale, now)

    # stale has had a full day to recover toward SCORE_CAP; fresh has not
    assert score_stale > score_fresh


# ---------------------------------------------------------------------------
# Scenario 1: Normal signal (no explicit valid_time)
# ---------------------------------------------------------------------------


async def test_normal_signal_valid_time_set_to_now(matcher, scorer, store):
    """When signal.valid_time is None, record.valid_time ≈ enrich time (Scenario 1, FR-003)."""
    before = time.time()
    signal = _make_signal()  # valid_time=None
    payload = await enrich(signal, matcher, scorer, store)
    after = time.time()

    assert len(payload.entities) == 1
    vt = payload.entities[0].valid_time
    assert before <= vt <= after


async def test_transaction_time_set_to_now(matcher, scorer, store):
    """record.transaction_time is within 1 second of now (Scenario 1, FR-004)."""
    now = time.time()
    signal = _make_signal()
    payload = await enrich(signal, matcher, scorer, store)

    assert len(payload.entities) == 1
    assert abs(payload.entities[0].transaction_time - now) < 1.0


# ---------------------------------------------------------------------------
# Scenario 6: Future-dated valid_time is clamped
# ---------------------------------------------------------------------------


async def test_future_valid_time_is_clamped(matcher, scorer, store):
    """A signal with valid_time in the future is clamped to transaction_time (Scenario 6)."""
    future_time = time.time() + 9999.0
    signal = _make_signal(valid_time=future_time)
    payload = await enrich(signal, matcher, scorer, store)

    assert len(payload.entities) == 1
    record = payload.entities[0]
    assert record.valid_time <= record.transaction_time


async def test_future_valid_time_emits_warning(matcher, scorer, store, caplog):
    """A signal with valid_time in the future emits a WARNING log (Scenario 6)."""
    future_time = time.time() + 9999.0
    signal = _make_signal(valid_time=future_time)

    with caplog.at_level(logging.WARNING, logger="revok.metadata_writer"):
        await enrich(signal, matcher, scorer, store)

    warning_messages = [r.message for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "future" in msg.lower() or "clamping" in msg.lower()
        for msg in warning_messages
    )


# ---------------------------------------------------------------------------
# Scenario 4: Backward compat — old records without valid_time load correctly
# ---------------------------------------------------------------------------


async def test_backward_compat_old_record_loads(tmp_path):
    """Row without valid_time/transaction_time columns defaults both to last_seen (Scenario 4, FR-006)."""
    cfg = StateStoreConfig(
        sqlite_path=str(tmp_path / "compat.db"),
        hot_layer_max_entries=10,
    )
    s = SqliteStateStore(cfg)
    await s.open()

    legacy_ts = 1717612800.0  # 2024-06-05T20:00:00Z
    # Insert using only the original 5-column schema; valid_time and
    # transaction_time columns will be NULL (default for REAL with no DEFAULT).
    await s._db.execute(
        "INSERT INTO entity_records "
        "  (entity_key, score, last_seen, signal_count, pattern_name) "
        "VALUES ('compat:alice', 0.5, ?, 1, 'header')",
        (legacy_ts,),
    )
    await s._db.commit()

    record = await s.get("compat:alice")
    assert record is not None
    assert record.valid_time == legacy_ts
    assert record.transaction_time == legacy_ts
    assert record.last_seen == legacy_ts  # property alias

    await s.close()


# ---------------------------------------------------------------------------
# Scenario 5: score_cap recovery is unaffected by the bitemporal refactor
# ---------------------------------------------------------------------------


def test_score_cap_recovery_unaffected(scorer):
    """decay_at approaches SCORE_CAP over 7 half-lives; bitemporal change does not alter this (Scenario 5)."""
    now = 0.0
    record = _make_record(valid_time=now, score=0.2)
    seven_days = 7 * HALF_LIFE

    recovered = scorer.decay_at(record, now + seven_days)

    # After 7 half-lives the remaining gap is (1.0-0.2)/2^7 ≈ 0.00625
    # so recovered ≈ 0.99375 — well above 0.99
    assert recovered > 0.99
    assert recovered <= SCORE_CAP
