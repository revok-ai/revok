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

"""Tests for revok.state_store.SqliteStateStore."""

import pytest

from revok.config import ScoringConfig, StateStoreConfig
from revok.models import EntityRecord
from revok.scoring import ScoringEngine
from revok.state_store import SqliteStateStore


def make_config(tmp_path, name: str = "test.db", max_entries: int = 10) -> StateStoreConfig:
    return StateStoreConfig(
        sqlite_path=str(tmp_path / name),
        hot_layer_max_entries=max_entries,
    )


def make_record(key: str, score: float = 0.5, signal_count: int = 1) -> EntityRecord:
    return EntityRecord(
        entity_key=key,
        score=score,
        last_seen=1000.0,
        signal_count=signal_count,
        pattern_name="person",
    )


@pytest.fixture
async def store(tmp_path):
    s = SqliteStateStore(make_config(tmp_path))
    await s.open()
    yield s
    await s.close()


async def test_put_then_get_returns_same_record(store):
    rec = make_record("alice", score=0.42)
    await store.put(rec)
    result = await store.get("alice")
    assert result is not None
    assert result.entity_key == "alice"
    assert result.score == pytest.approx(0.42)
    assert result.signal_count == 1


async def test_get_nonexistent_returns_none(store):
    result = await store.get("nobody")
    assert result is None


async def test_second_put_overwrites_first(store):
    await store.put(make_record("alice", score=0.3))
    await store.put(make_record("alice", score=0.9, signal_count=2))
    result = await store.get("alice")
    assert result is not None
    assert result.score == pytest.approx(0.9)
    assert result.signal_count == 2


async def test_lru_evicts_oldest_when_at_max_capacity(tmp_path):
    """The oldest hot-layer entry is evicted; the record remains in SQLite."""
    cfg = StateStoreConfig(
        sqlite_path=str(tmp_path / "lru.db"),
        hot_layer_max_entries=2,
    )
    store = SqliteStateStore(cfg)
    await store.open()

    # Fill the hot layer to capacity
    await store.put(make_record("a"))
    await store.put(make_record("b"))
    # This third put should evict "a" from the hot layer
    await store.put(make_record("c"))

    assert "a" not in store._hot  # evicted from hot layer
    # But "a" is still in SQLite
    result = await store.get("a")
    assert result is not None
    assert result.entity_key == "a"

    await store.close()


async def test_records_survive_close_and_reopen(tmp_path):
    """Persisted records are readable after the store is closed and reopened (SC-008)."""
    cfg = make_config(tmp_path, name="persist.db")

    store1 = SqliteStateStore(cfg)
    await store1.open()
    await store1.put(make_record("alice", score=0.77))
    await store1.close()

    store2 = SqliteStateStore(cfg)
    await store2.open()
    result = await store2.get("alice")
    assert result is not None
    assert result.score == pytest.approx(0.77)
    await store2.close()


async def test_decay_on_read_returns_decayed_score(tmp_path):
    """get() applies read-time decay when scorer is configured (T030, acceptance scenario 4.3).

    After exactly one half-life the returned score must be approximately
    half the stored value.
    """
    import time

    half_life = 10.0  # seconds
    scorer = ScoringEngine(
        ScoringConfig(half_life_seconds=half_life, signal_strength=1.0, score_cap=100.0)
    )
    cfg = StateStoreConfig(
        sqlite_path=str(tmp_path / "decay.db"),
        hot_layer_max_entries=10,
    )
    store = SqliteStateStore(cfg, scorer=scorer)
    await store.open()

    # Store a record whose last_seen is exactly one half-life in the past
    stored_score = 1.0
    now = time.time()
    record = EntityRecord(
        entity_key="alice",
        score=stored_score,
        last_seen=now - half_life,
        signal_count=1,
        pattern_name="person",
    )
    await store.put(record)

    # Evict from hot layer so we exercise the SQLite path too
    store._hot.clear()

    result = await store.get("alice")
    assert result is not None
    # Score should be approximately stored_score / 2 after one half-life
    assert result.score == pytest.approx(stored_score / 2, rel=0.05)
    # Persisted score must NOT have been modified
    assert result.last_seen == record.last_seen

    await store.close()
