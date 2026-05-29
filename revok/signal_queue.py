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

"""AsyncioQueueBus: MessageBus implementation backed by ``asyncio.Queue``.

Delivers signals in FIFO order.  ``close()`` inserts a ``None`` sentinel
that causes ``consume()`` to raise ``asyncio.CancelledError``.
"""

from __future__ import annotations

import asyncio
import logging

from revok.models import Signal

logger = logging.getLogger(__name__)


class AsyncioQueueBus:
    """``MessageBus`` backed by an ``asyncio.Queue``.

    Thread-safety: single-threaded async only (same event loop as aiohttp).
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[Signal | None] = asyncio.Queue()
        self._closed = False

    async def publish(self, signal: Signal) -> None:
        """Place *signal* onto the queue.

        Never blocks the event loop; ``asyncio.Queue`` is unbounded by default.

        Args:
            signal: The signal to publish.
        """
        await self._queue.put(signal)

    async def consume(self) -> Signal:
        """Wait for and return the next signal from the queue.

        Returns:
            The next :class:`~revok.models.Signal` in FIFO order.

        Raises:
            asyncio.CancelledError: If :meth:`close` was called.
        """
        item = await self._queue.get()
        if item is None:
            raise asyncio.CancelledError("AsyncioQueueBus has been closed")
        return item

    async def close(self) -> None:
        """Signal shutdown by inserting a ``None`` sentinel. Idempotent."""
        if not self._closed:
            self._closed = True
            await self._queue.put(None)
