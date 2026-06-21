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

"""Protocol interfaces for all pluggable Revok components.

All Protocol classes MUST be defined here before any concrete
implementation is written (FR-002, Constitution § III — Interface First).
No concrete classes are defined in this module.
"""

from __future__ import annotations

from typing import AsyncIterator, Protocol, runtime_checkable

from revok.models import EntityRecord, MemoryAdapterResponse, EnrichedPayload, Signal


@runtime_checkable
class SignalSource(Protocol):
    """A source that delivers raw memory signals to the Revok pipeline."""

    async def receive(self) -> AsyncIterator[Signal]:
        """Yield incoming signals one at a time.

        Yields:
            Signal: The next incoming signal from this source.

        The iterator runs until the source is exhausted or the coroutine
        is cancelled. Implementations must not block the event loop.
        """
        ...

    async def close(self) -> None:
        """Release any resources held by this source.

        Must be idempotent — safe to call multiple times.
        """
        ...


@runtime_checkable
class MessageBus(Protocol):
    """Async channel that carries signals between pipeline stages."""

    async def publish(self, signal: Signal) -> None:
        """Place a signal onto the bus for downstream consumption.

        Args:
            signal: The signal to publish.

        Must not block the event loop. Must not raise on a full queue;
        implementations should apply backpressure or drop with logging.
        """
        ...

    async def consume(self) -> Signal:
        """Wait for and return the next signal from the bus.

        Returns:
            Signal: The next signal available for processing.

        Blocks until a signal is available. Must be cancellation-safe.
        """
        ...

    async def close(self) -> None:
        """Drain the bus and release resources.

        Must be idempotent.
        """
        ...


@runtime_checkable
class StateStore(Protocol):
    """Persistent + cached storage for EntityRecord state."""

    async def get(self, entity_key: str) -> EntityRecord | None:
        """Fetch the current record for an entity key.

        Args:
            entity_key: Normalized entity identifier.

        Returns:
            The EntityRecord if it exists, None otherwise.

        Must check the hot layer first, then fall back to the durable store.
        """
        ...

    async def put(self, record: EntityRecord) -> None:
        """Persist an entity record, replacing any existing record for the same key.

        Args:
            record: The EntityRecord to persist.

        Must write to the durable store AND update the hot layer atomically
        from the caller's perspective.
        """
        ...

    async def list_all(self, offset: int = 0, limit: int = 100) -> list[EntityRecord]:
        """Return a paginated list of all stored entity records.

        Args:
            offset: Number of records to skip (for pagination).
            limit: Maximum number of records to return.

        Returns:
            List of EntityRecord instances, ordered by entity_key.
        """
        ...

    async def delete(self, entity_key: str) -> bool:
        """Delete the record for an entity key.

        Args:
            entity_key: Normalized entity identifier.

        Returns:
            True if a record was deleted, False if it did not exist.
        """
        ...

    async def close(self) -> None:
        """Flush pending writes and release resources.

        Must be idempotent.
        """
        ...


@runtime_checkable
class MemoryAdapter(Protocol):
    """Downstream adapter that forwards enriched writes and raw pass-throughs to Mem0."""

    async def write(
        self, payload: EnrichedPayload, http_path: str = "/"
    ) -> MemoryAdapterResponse:
        """Forward an enriched write payload to the upstream memory store.

        Args:
            payload: The enriched payload to forward.
            http_path: Original request path (may include query string).

        Returns:
            MemoryAdapterResponse: The upstream HTTP response.

        Must not raise on upstream HTTP errors; use is_error to signal failure.
        """
        ...

    async def forward(self, signal: Signal) -> MemoryAdapterResponse:
        """Replay the raw signal to the upstream store unchanged.

        Args:
            signal: The original signal to replay.

        Returns:
            MemoryAdapterResponse: The upstream HTTP response.

        Used for read requests and non-write methods that bypass enrichment.
        """
        ...

    async def close(self) -> None:
        """Release the underlying HTTP session.

        Must be idempotent.
        """
        ...


@runtime_checkable
class GraphBackend(Protocol):
    """Graph backend for causal propagation over entity relationships."""

    def add_entity(self, entity_id: str, score: float) -> None:
        """Add or update an entity node.

        Args:
            entity_id: Normalized entity identifier.
            score: Current score for that entity.
        """
        ...

    def add_relation(self, source_id: str, target_id: str, weight: float = 1.0) -> None:
        """Add a directed weighted relation edge.

        Args:
            source_id: Source entity key.
            target_id: Target entity key.
            weight: Edge propagation multiplier in (0, 1].
        """
        ...

    def propagate(
        self,
        root_entity_id: str,
        initial_pressure: float,
        *,
        max_hops: int,
        min_pressure: float,
        attenuation: float,
    ) -> dict[str, float]:
        """Propagate pressure from a root entity to downstream entities.

        Returns:
            Mapping of downstream entity key to propagated pressure.
        """
        ...
