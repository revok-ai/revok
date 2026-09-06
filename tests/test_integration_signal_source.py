# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Revok Contributors

"""End-to-end coverage across source, runner, processor, and ingestion store."""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any

from revok.config import (
    CausalGraphConfig,
    RedisStreamsSourceConfig,
    ScoringConfig,
    SignalPressureConfig,
    StateStoreConfig,
)
from revok.causal_graph import CausalGraph
from revok.models import Signal
from revok.redis_source import RedisStreamsSignalSource
from revok.scoring import ScoringEngine
from revok.signal_dedupe import SqliteIngestionStore
from revok.signal_processor import SignalProcessor
from revok.signal_queue import AsyncioQueueBus
from revok.signal_sources import HttpSignalSource, SourceRunner, SourceSupervisor
from revok.state_store import SqliteStateStore


class FakeRedis:
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
            raise RuntimeError("BUSYGROUP exists")
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
        return [(stream, selected)] if selected else []

    async def xpending_range(
        self, stream: str, group: str, min: str, max: str, count: int
    ) -> list[dict[str, Any]]:
        return [{"message_id": min, "times_delivered": self.delivered.get(min, 1)}]

    async def xack(self, stream: str, group: str, message_id: str) -> int:
        self.acked.add(message_id)
        return 1


def _redis_config(**overrides: Any) -> RedisStreamsSourceConfig:
    base: dict[str, Any] = {
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
    return RedisStreamsSourceConfig(**base)


def _scorer() -> ScoringEngine:
    return ScoringEngine(
        ScoringConfig(
            half_life_seconds=86400.0,
            signal_strength=0.3,
            score_cap=1.0,
            signal_pressure=SignalPressureConfig(
                severity_weights={"low": 0.2, "high": 0.7},
                default_severity="low",
            ),
        )
    )


async def _build(tmp_path: Path, graph: CausalGraph | None = None):
    store = SqliteStateStore(StateStoreConfig(str(tmp_path / "e2e.db"), 16))
    await store.open()
    ingestion = SqliteIngestionStore(str(tmp_path / "e2e.db"))
    await ingestion.open()
    processor = SignalProcessor(
        AsyncioQueueBus(),
        store,
        _scorer(),
        graph or CausalGraph(),
        CausalGraphConfig(enabled=True, max_hops=2, min_pressure=0.05, attenuation=1.0),
        dedupe=ingestion,
    )
    return store, ingestion, processor


async def _run_briefly(supervisor: SourceSupervisor, window: float = 0.15) -> None:
    task = asyncio.create_task(supervisor.run())
    await asyncio.sleep(window)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_durable_signal_is_applied_acknowledged_and_deduplicated(
    tmp_path: Path,
) -> None:
    client = FakeRedis()
    message_id = await client.xadd(
        "revok:signals",
        {"payload": json.dumps({"signal_id": "e2e-1", "entity_refs": ["alpha"], "severity": "high"})},
    )
    store, ingestion, processor = await _build(tmp_path)
    try:
        source = RedisStreamsSignalSource(_redis_config(), ingestion, client=client)
        supervisor = SourceSupervisor(
            [SourceRunner(source, processor, name="redis_streams")]
        )
        await _run_briefly(supervisor)

        record = await store.get("alpha")
        assert record is not None and record.score < 1.0
        assert message_id in client.acked
        assert await ingestion.has("e2e-1") is True
    finally:
        await ingestion.close()
        await store.close()


async def test_redelivered_signal_does_not_apply_twice(tmp_path: Path) -> None:
    client = FakeRedis()
    payload = json.dumps({"signal_id": "e2e-2", "entity_refs": ["alpha"], "severity": "high"})
    await client.xadd("revok:signals", {"payload": payload})
    store, ingestion, processor = await _build(tmp_path)
    try:
        source = RedisStreamsSignalSource(_redis_config(), ingestion, client=client)
        await _run_briefly(
            SourceSupervisor([SourceRunner(source, processor, name="redis_streams")])
        )
        after_first = await store.get("alpha")

        # Same signal id published again, as a redelivery would appear.
        await client.xadd("revok:signals", {"payload": payload})
        source2 = RedisStreamsSignalSource(_redis_config(), ingestion, client=client)
        await _run_briefly(
            SourceSupervisor([SourceRunner(source2, processor, name="redis_streams")])
        )
        after_second = await store.get("alpha")

        assert after_first is not None and after_second is not None
        assert after_second.score == after_first.score
        assert after_second.signal_count == after_first.signal_count
    finally:
        await ingestion.close()
        await store.close()


async def test_same_entity_signals_from_two_sources_both_apply_in_order(
    tmp_path: Path,
) -> None:
    client = FakeRedis()
    await client.xadd(
        "revok:signals",
        {"payload": json.dumps({"signal_id": "durable-1", "entity_refs": ["alpha"], "severity": "low"})},
    )
    store, ingestion, processor = await _build(tmp_path)
    bus = AsyncioQueueBus()
    await bus.publish(
        Signal(
            raw_content="alpha",
            source_id="http",
            timestamp=1000.0,
            http_method="POST",
            http_path="/signals",
            original_body=json.dumps(
                {"entity_refs": ["alpha"], "severity": "low"}
            ).encode(),
            headers={},
            signal_id="http-1",
        )
    )
    try:
        supervisor = SourceSupervisor(
            [
                SourceRunner(HttpSignalSource(bus), processor, name="http"),
                SourceRunner(
                    RedisStreamsSignalSource(_redis_config(), ingestion, client=client),
                    processor,
                    name="redis_streams",
                ),
            ]
        )
        await _run_briefly(supervisor)

        record = await store.get("alpha")
        assert record is not None
        # Both applied exactly once; neither overwrote the other.
        assert record.signal_count == 2
    finally:
        await ingestion.close()
        await store.close()


async def test_dead_lettered_signal_is_retrievable_and_stream_advances(
    tmp_path: Path,
) -> None:
    client = FakeRedis()
    poison = await client.xadd("revok:signals", {"payload": "{not json"})
    client.delivered[poison] = 1
    healthy = await client.xadd(
        "revok:signals",
        {"payload": json.dumps({"signal_id": "healthy-1", "entity_refs": ["alpha"], "severity": "high"})},
    )
    store, ingestion, processor = await _build(tmp_path)
    try:
        source = RedisStreamsSignalSource(_redis_config(), ingestion, client=client)
        await _run_briefly(
            SourceSupervisor([SourceRunner(source, processor, name="redis_streams")])
        )

        records = await ingestion.list_dead_letters()
        assert records, "poison entry must be set aside"
        assert "unparseable" in records[0].failure_reason
        assert poison in client.acked

        # The stream advanced past the poison entry.
        assert healthy in client.acked
        applied = await store.get("alpha")
        assert applied is not None and applied.score < 1.0
    finally:
        await ingestion.close()
        await store.close()
