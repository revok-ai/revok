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


def make_config(
    tmp_path, name: str = "test.db", max_entries: int = 10
) -> StateStoreConfig:
    return StateStoreConfig(
        sqlite_path=str(tmp_path / name),
        hot_layer_max_entries=max_entries,
    )


def make_record(key: str, score: float = 0.5, signal_count: int = 1) -> EntityRecord:
    return EntityRecord(
        entity_key=key,
        score=score,
        valid_time=1000.0,
        transaction_time=1000.0,
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
    """get() applies read-time recovery when scorer is configured (T030, acceptance scenario 4.3).

    After exactly one half-life the gap between the stored score and score_cap
    is halved (score moves halfway toward score_cap).
    """
    import time

    half_life = 10.0  # seconds
    score_cap = 100.0
    scorer = ScoringEngine(
        ScoringConfig(
            half_life_seconds=half_life, signal_strength=1.0, score_cap=score_cap
        )
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
        valid_time=now - half_life,
        transaction_time=now,
        signal_count=1,
        pattern_name="person",
    )
    await store.put(record)

    # Evict from hot layer so we exercise the SQLite path too
    store._hot.clear()

    result = await store.get("alice")
    assert result is not None
    # After one half-life the gap to score_cap is halved:
    # expected = score_cap - (score_cap - stored_score) / 2 = 50.5
    expected_score = score_cap - (score_cap - stored_score) / 2.0
    assert result.score == pytest.approx(expected_score, rel=0.05)
    # Persisted score must NOT have been modified
    assert result.last_seen == record.last_seen

    await store.close()


async def test_list_all_returns_all_records(tmp_path):
    """list_all() returns every stored record in entity_key order."""
    cfg = make_config(tmp_path, name="list.db")
    store = SqliteStateStore(cfg)
    await store.open()

    await store.put(make_record("charlie", score=0.3))
    await store.put(make_record("alice", score=0.9))
    await store.put(make_record("bob", score=0.5))

    results = await store.list_all(offset=0, limit=100)
    assert len(results) == 3
    # Returned in entity_key ascending order
    assert [r.entity_key for r in results] == ["alice", "bob", "charlie"]

    await store.close()


async def test_list_all_offset_and_limit(tmp_path):
    """list_all() respects offset and limit pagination parameters."""
    cfg = make_config(tmp_path, name="paginate.db")
    store = SqliteStateStore(cfg)
    await store.open()

    for i in range(5):
        await store.put(make_record(f"entity_{i:02d}", score=float(i) * 0.1))

    page = await store.list_all(offset=1, limit=2)
    assert len(page) == 2
    assert page[0].entity_key == "entity_01"
    assert page[1].entity_key == "entity_02"

    await store.close()


async def test_list_all_empty_store_returns_empty_list(tmp_path):
    """list_all() on an empty store returns an empty list, not an error."""
    cfg = make_config(tmp_path, name="empty.db")
    store = SqliteStateStore(cfg)
    await store.open()
    results = await store.list_all()
    assert results == []
    await store.close()


# ---------------------------------------------------------------------------
# T040: SQLite corruption raises RuntimeError
# ---------------------------------------------------------------------------


async def test_corrupted_db_raises_runtime_error(tmp_path):
    """open() raises RuntimeError when the database file is corrupted (SC-009)."""
    db_path = tmp_path / "corrupted.db"
    # Write non-SQLite bytes so aiosqlite raises sqlite3.DatabaseError on open
    db_path.write_bytes(b"this is not a valid sqlite3 database file\x00\xff\xfe")

    cfg = StateStoreConfig(
        sqlite_path=str(db_path),
        hot_layer_max_entries=10,
    )
    store = SqliteStateStore(cfg)

    with pytest.raises(RuntimeError, match="corrupted"):
        await store.open()


# ---------------------------------------------------------------------------
# delete()
# ---------------------------------------------------------------------------


async def test_delete_existing_record_returns_true(store):
    """delete() returns True and removes the record when it exists."""
    await store.put(make_record("alice"))
    result = await store.delete("alice")
    assert result is True
    assert await store.get("alice") is None


async def test_delete_nonexistent_record_returns_false(store):
    """delete() returns False without error when the entity key is not found."""
    result = await store.delete("nobody")
    assert result is False
