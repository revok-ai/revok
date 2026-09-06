# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Revok Contributors

from __future__ import annotations

import pytest

from revok.models import DeadLetterRecord, DedupeRecord
from revok.signal_dedupe import SqliteIngestionStore


def _dedupe(signal_id: str, processed_at: float = 1.0) -> DedupeRecord:
    return DedupeRecord(
        signal_id=signal_id,
        processed_at=processed_at,
        source_name="http",
        entity_keys=("alpha",),
    )


def _dead_letter(signal_id: str, at: float = 1.0) -> DeadLetterRecord:
    return DeadLetterRecord(
        signal_id=signal_id,
        source_name="redis_streams",
        payload='{"entity_refs": ["alpha"]}',
        delivery_attempts=4,
        failure_reason="store unavailable",
        dead_lettered_at=at,
    )


async def test_recorded_signal_is_reported_as_seen(tmp_db_path: str) -> None:
    store = SqliteIngestionStore(tmp_db_path)
    await store.open()
    try:
        assert await store.has("s1") is False
        await store.record(_dedupe("s1"))
        assert await store.has("s1") is True
    finally:
        await store.close()


async def test_dedupe_records_survive_reopen(tmp_db_path: str) -> None:
    store = SqliteIngestionStore(tmp_db_path)
    await store.open()
    await store.record(_dedupe("s1"))
    await store.close()

    reopened = SqliteIngestionStore(tmp_db_path)
    await reopened.open()
    try:
        assert await reopened.has("s1") is True
    finally:
        await reopened.close()


async def test_dedupe_retention_is_bounded_by_row_count(tmp_db_path: str) -> None:
    store = SqliteIngestionStore(tmp_db_path, dedupe_max_rows=10)
    await store.open()
    try:
        for index in range(50):
            await store.record(_dedupe(f"s{index:03d}", processed_at=float(index)))
        await store.trim_dedupe()

        assert await store.dedupe_count() == 10
        assert await store.has("s049") is True
        assert await store.has("s000") is False
    finally:
        await store.close()


async def test_non_positive_dedupe_bound_is_rejected(tmp_db_path: str) -> None:
    with pytest.raises(ValueError, match="dedupe_max_rows"):
        SqliteIngestionStore(tmp_db_path, dedupe_max_rows=0)


async def test_dead_letter_round_trips_with_recovery_information(
    tmp_db_path: str,
) -> None:
    store = SqliteIngestionStore(tmp_db_path)
    await store.open()
    try:
        await store.dead_letter(_dead_letter("s1"))
        record = await store.get_dead_letter("s1")

        assert record is not None
        assert record.delivery_attempts == 4
        assert record.failure_reason == "store unavailable"
        assert "entity_refs" in record.payload
    finally:
        await store.close()


async def test_dead_letters_survive_reopen_and_list_newest_first(
    tmp_db_path: str,
) -> None:
    store = SqliteIngestionStore(tmp_db_path)
    await store.open()
    await store.dead_letter(_dead_letter("s1", at=1.0))
    await store.dead_letter(_dead_letter("s2", at=2.0))
    await store.close()

    reopened = SqliteIngestionStore(tmp_db_path)
    await reopened.open()
    try:
        records = await reopened.list_dead_letters()
        assert [r.signal_id for r in records] == ["s2", "s1"]
    finally:
        await reopened.close()


async def test_dead_letter_pagination(tmp_db_path: str) -> None:
    store = SqliteIngestionStore(tmp_db_path)
    await store.open()
    try:
        for index in range(5):
            await store.dead_letter(_dead_letter(f"s{index}", at=float(index)))

        page = await store.list_dead_letters(offset=1, limit=2)
        assert [r.signal_id for r in page] == ["s3", "s2"]

        with pytest.raises(ValueError):
            await store.list_dead_letters(limit=0)
    finally:
        await store.close()


async def test_unknown_dead_letter_returns_none(tmp_db_path: str) -> None:
    store = SqliteIngestionStore(tmp_db_path)
    await store.open()
    try:
        assert await store.get_dead_letter("missing") is None
    finally:
        await store.close()
