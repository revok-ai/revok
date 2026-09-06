# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Revok Contributors

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from revok.models import ProcessingOutcome, Signal
from revok.signal_queue import AsyncioQueueBus
from revok.signal_sources import HttpSignalSource, SourceRunner, SourceSupervisor


def _signal(signal_id: str, entity: str = "alpha") -> Signal:
    return Signal(
        raw_content=entity,
        source_id="test",
        timestamp=1000.0,
        http_method="POST",
        http_path="/signals",
        original_body=json.dumps({"entity_refs": [entity]}).encode(),
        headers={},
        signal_id=signal_id,
    )


class RecordingProcessor:
    """Stand-in processor returning a configurable outcome per signal."""

    def __init__(self, statuses: dict[str, str] | None = None) -> None:
        self.seen: list[str] = []
        self._statuses = statuses or {}

    async def process_one(self, signal: Signal) -> ProcessingOutcome:
        await asyncio.sleep(0)
        self.seen.append(signal.signal_id or "")
        status = self._statuses.get(signal.signal_id or "", "applied")
        return ProcessingOutcome(
            signal_id=signal.signal_id or "",
            status=status,
            failure_reason="boom" if status == "failed" else None,
        )


class ListSource:
    def __init__(self, signals: list[Signal], name: str = "list") -> None:
        self.name = name
        self._signals = signals
        self.acked: list[str] = []
        self.closed = False

    async def receive(self) -> AsyncIterator[Signal]:
        for signal in self._signals:
            yield signal

    async def ack(self, signal: Signal) -> None:
        self.acked.append(signal.signal_id or "")

    async def close(self) -> None:
        self.closed = True


class ExplodingSource:
    def __init__(self, name: str = "exploding") -> None:
        self.name = name
        self.closed = False

    async def receive(self) -> AsyncIterator[Signal]:
        raise RuntimeError("source boom")
        yield  # pragma: no cover - marks this an async generator

    async def ack(self, signal: Signal) -> None:
        return None

    async def close(self) -> None:
        self.closed = True


async def test_runner_delivers_every_signal_to_the_processor() -> None:
    signals = [_signal("s1"), _signal("s2")]
    source = ListSource(signals)
    processor = RecordingProcessor()

    await SourceRunner(source, processor, name="list").run()  # type: ignore[arg-type]

    assert processor.seen == ["s1", "s2"]


async def test_applied_signals_are_acknowledged() -> None:
    source = ListSource([_signal("s1")])
    processor = RecordingProcessor()

    await SourceRunner(source, processor, name="list").run()  # type: ignore[arg-type]

    assert source.acked == ["s1"]


async def test_duplicate_signals_are_acknowledged() -> None:
    source = ListSource([_signal("s1")])
    processor = RecordingProcessor({"s1": "duplicate"})

    await SourceRunner(source, processor, name="list").run()  # type: ignore[arg-type]

    assert source.acked == ["s1"]


async def test_failed_signals_are_not_acknowledged() -> None:
    source = ListSource([_signal("s1"), _signal("s2")])
    processor = RecordingProcessor({"s1": "failed"})

    await SourceRunner(source, processor, name="list").run()  # type: ignore[arg-type]

    assert source.acked == ["s2"]


async def test_source_failure_is_contained_and_others_continue() -> None:
    healthy = ListSource([_signal("s1")], name="healthy")
    broken = ExplodingSource()
    processor = RecordingProcessor()

    supervisor = SourceSupervisor(
        [
            SourceRunner(broken, processor, name="broken"),  # type: ignore[arg-type]
            SourceRunner(healthy, processor, name="healthy"),  # type: ignore[arg-type]
        ]
    )
    await asyncio.wait_for(supervisor.run(), timeout=2.0)

    assert processor.seen == ["s1"]
    assert healthy.acked == ["s1"]


async def test_supervisor_closes_every_source_regardless_of_order() -> None:
    first = ListSource([], name="first")
    second = ExplodingSource(name="second")
    processor = RecordingProcessor()

    supervisor = SourceSupervisor(
        [
            SourceRunner(first, processor, name="first"),  # type: ignore[arg-type]
            SourceRunner(second, processor, name="second"),  # type: ignore[arg-type]
        ]
    )
    await supervisor.close()

    assert first.closed is True
    assert second.closed is True


async def test_http_source_yields_published_signals_and_ack_is_a_no_op() -> None:
    bus = AsyncioQueueBus()
    source = HttpSignalSource(bus)
    await bus.publish(_signal("s1"))

    received = await anext(source.receive())
    assert received.signal_id == "s1"

    # HTTP has no acknowledgment semantics; this must not raise.
    await source.ack(received)
    await source.close()


async def test_runner_processes_unrelated_signals_concurrently() -> None:
    release = asyncio.Event()
    started = asyncio.Semaphore(0)

    class BlockingProcessor:
        def __init__(self) -> None:
            self.completed: list[str] = []

        async def process_one(self, signal: Signal) -> ProcessingOutcome:
            started.release()
            await release.wait()
            self.completed.append(signal.signal_id or "")
            return ProcessingOutcome(signal_id=signal.signal_id or "", status="applied")

    processor = BlockingProcessor()
    source = ListSource([_signal("s1", "a"), _signal("s2", "b")])
    runner = SourceRunner(source, processor, name="list", max_concurrent=4)  # type: ignore[arg-type]

    task = asyncio.create_task(runner.run())
    # Both must be in flight before either completes.
    await asyncio.wait_for(started.acquire(), timeout=1.0)
    await asyncio.wait_for(started.acquire(), timeout=1.0)
    release.set()
    await asyncio.wait_for(task, timeout=2.0)

    assert sorted(processor.completed) == ["s1", "s2"]
