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

"""Tests for revok.signal_queue.AsyncioQueueBus."""

import asyncio

import pytest

from revok.models import Signal
from revok.signal_queue import AsyncioQueueBus


def make_signal(source_id: str = "test-agent") -> Signal:
    return Signal(
        raw_content="hello world",
        source_id=source_id,
        timestamp=1000.0,
        http_method="POST",
        http_path="/v1/memories",
        original_body=b'{"content":"hello world"}',
        headers={},
    )


async def test_published_signal_is_consumed_in_fifo_order():
    bus = AsyncioQueueBus()
    s1 = make_signal("agent-1")
    s2 = make_signal("agent-2")
    await bus.publish(s1)
    await bus.publish(s2)

    r1 = await bus.consume()
    r2 = await bus.consume()

    assert r1.source_id == "agent-1"
    assert r2.source_id == "agent-2"


async def test_consume_awaits_when_queue_is_empty():
    """consume() must yield control until a signal becomes available."""
    bus = AsyncioQueueBus()

    async def delayed_publish():
        await asyncio.sleep(0.01)
        await bus.publish(make_signal("delayed"))

    asyncio.create_task(delayed_publish())
    result = await asyncio.wait_for(bus.consume(), timeout=1.0)
    assert result.source_id == "delayed"


async def test_close_makes_consume_raise_cancelled_error():
    bus = AsyncioQueueBus()
    await bus.close()
    with pytest.raises(asyncio.CancelledError):
        await bus.consume()


async def test_close_is_idempotent():
    """Calling close() twice should not raise."""
    bus = AsyncioQueueBus()
    await bus.close()
    await bus.close()  # second close must not raise or enqueue a second sentinel


async def test_multiple_signals_consumed_in_order():
    bus = AsyncioQueueBus()
    signals = [make_signal(f"agent-{i}") for i in range(5)]
    for s in signals:
        await bus.publish(s)

    consumed = [await bus.consume() for _ in range(5)]
    assert [c.source_id for c in consumed] == [f"agent-{i}" for i in range(5)]
