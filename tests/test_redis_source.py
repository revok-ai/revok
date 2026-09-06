# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Revok Contributors

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import pytest

from revok.config import RedisStreamsSourceConfig
from revok.models import DeadLetterRecord
from revok.redis_source import RedisStreamsSignalSource


class FakeRedis:
    """Minimal Redis Streams consumer-group stand-in.

    Message ids are zero-padded so lexicographic cursor comparison matches
    Redis ordering.
    """

    def __init__(self) -> None:
        self.entries: list[tuple[str, dict[str, str]]] = []
        self.delivered: dict[str, int] = {}
        self.acked: set[str] = set()
        self.groups: set[str] = set()
        self._counter = 0

    async def xgroup_create(
        self, stream: str, group: str, id: str = "0", mkstream: bool = False
    ) -> None:
        if group in self.groups:
            raise RuntimeError("BUSYGROUP Consumer Group name already exists")
        self.groups.add(group)

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self._counter += 1
        message_id = f"{self._counter:06d}-0"
        self.entries.append((message_id, fields))
        return message_id

    async def xreadgroup(
        self,
        group: str,
        consumer: str,
        streams: dict[str, str],
        count: int = 10,
        block: int | None = None,
    ) -> list[Any]:
        stream = next(iter(streams))
        cursor = streams[stream]
        if cursor == ">":
            selected = [e for e in self.entries if e[0] not in self.delivered]
        else:
            selected = [
                e
                for e in self.entries
                if e[0] in self.delivered and e[0] not in self.acked and e[0] > cursor
            ]
        selected = selected[:count]
        for message_id, _ in selected:
            self.delivered[message_id] = self.delivered.get(message_id, 0) + 1
        if not selected:
            return []
        return [(stream, selected)]

    async def xpending_range(
        self, stream: str, group: str, min: str, max: str, count: int
    ) -> list[dict[str, Any]]:
        return [{"message_id": min, "times_delivered": self.delivered.get(min, 1)}]

    async def xack(self, stream: str, group: str, message_id: str) -> int:
        self.acked.add(message_id)
        return 1


class RecordingDeadLetters:
    def __init__(self) -> None:
        self.records: list[DeadLetterRecord] = []

    async def dead_letter(self, entry: DeadLetterRecord) -> None:
        self.records.append(entry)

    async def get_dead_letter(self, signal_id: str) -> DeadLetterRecord | None:
        return next((r for r in self.records if r.signal_id == signal_id), None)

    async def list_dead_letters(
        self, offset: int = 0, limit: int = 100
    ) -> list[DeadLetterRecord]:
        return self.records[offset : offset + limit]


def _config(**overrides: Any) -> RedisStreamsSourceConfig:
    base = {
        "enabled": True,
        "url": "redis://localhost:6379",
        "stream": "revok:signals",
        "consumer_group": "revok",
        "consumer_name": "revok-1",
        "block_ms": 1,
        "batch_size": 10,
        "max_delivery_attempts": 3,
    }
    base.update(overrides)
    return RedisStreamsSourceConfig(**base)  # type: ignore[arg-type]


def _source(
    client: FakeRedis,
    dead_letters: RecordingDeadLetters | None = None,
    **overrides: Any,
) -> RedisStreamsSignalSource:
    return RedisStreamsSignalSource(
        _config(**overrides),
        dead_letters,  # type: ignore[arg-type]
        client=client,
    )


async def _take(source: RedisStreamsSignalSource, count: int) -> list[Any]:
    collected = []
    async for signal in source.receive():
        collected.append(signal)
        if len(collected) >= count:
            break
    return collected


async def _collect(
    source: RedisStreamsSignalSource, *, window: float = 0.1
) -> list[Any]:
    """Collect everything the source yields within a bounded window.

    ``receive`` polls indefinitely, so collection is time-bounded rather than
    count-bounded when the expected result is "nothing".
    """
    collected: list[Any] = []

    async def _run() -> None:
        async for signal in source.receive():
            collected.append(signal)

    task = asyncio.create_task(_run())
    await asyncio.sleep(window)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    return collected


async def test_published_signals_are_delivered() -> None:
    client = FakeRedis()
    await client.xadd("revok:signals", {"payload": json.dumps({"entity_refs": ["a"]})})
    source = _source(client)

    signals = await _take(source, 1)

    assert signals[0].raw_content == "a"
    assert signals[0].signal_id


async def test_signal_id_from_payload_is_preserved() -> None:
    client = FakeRedis()
    await client.xadd(
        "revok:signals",
        {"payload": json.dumps({"signal_id": "given-1", "entity_refs": ["a"]})},
    )
    source = _source(client)

    signals = await _take(source, 1)

    assert signals[0].signal_id == "given-1"


async def test_unacknowledged_entries_are_redelivered() -> None:
    client = FakeRedis()
    await client.xadd("revok:signals", {"payload": json.dumps({"entity_refs": ["a"]})})

    first = _source(client)
    delivered = await _take(first, 1)
    assert delivered  # consumed but never acknowledged

    # A fresh consumer must see the still-pending entry.
    second = _source(client)
    redelivered = await _take(second, 1)
    assert redelivered[0].raw_content == "a"


async def test_acknowledged_entries_are_not_redelivered() -> None:
    client = FakeRedis()
    await client.xadd("revok:signals", {"payload": json.dumps({"entity_refs": ["a"]})})

    first = _source(client)
    delivered = await _take(first, 1)
    await first.ack(delivered[0])

    second = _source(client)
    assert await _collect(second) == []


async def test_entry_exceeding_attempt_bound_is_dead_lettered() -> None:
    client = FakeRedis()
    message_id = await client.xadd(
        "revok:signals", {"payload": json.dumps({"entity_refs": ["a"]})}
    )
    client.delivered[message_id] = 5  # already delivered beyond the bound
    dead_letters = RecordingDeadLetters()
    source = _source(client, dead_letters, max_delivery_attempts=3)

    assert await _collect(source) == []

    assert dead_letters.records
    record = dead_letters.records[0]
    # Reading a pending entry increments times_delivered, as Redis does.
    assert record.delivery_attempts > 3
    assert "delivery attempts" in record.failure_reason
    assert message_id in client.acked


async def test_unparseable_entry_is_dead_lettered_not_raised() -> None:
    client = FakeRedis()
    message_id = await client.xadd("revok:signals", {"payload": "{not json"})
    client.delivered[message_id] = 1
    dead_letters = RecordingDeadLetters()
    source = _source(client, dead_letters)

    assert await _collect(source) == []

    assert dead_letters.records
    assert "unparseable" in dead_letters.records[0].failure_reason
    assert message_id in client.acked


async def test_missing_extra_raises_actionable_error(monkeypatch: Any) -> None:
    import revok.redis_source as module

    monkeypatch.setattr(module, "_REDIS_AVAILABLE", False)
    with pytest.raises(ImportError, match=r"revok\[redis\]"):
        RedisStreamsSignalSource(_config())
