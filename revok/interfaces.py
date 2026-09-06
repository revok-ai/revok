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

from revok.models import (
    DeadLetterRecord,
    DedupeRecord,
    DownstreamEntity,
    EntityRecord,
    EnrichedPayload,
    InspectionReport,
    MemoryAdapterResponse,
    PropagationPath,
    PropagationTrace,
    ResolutionTrace,
    ResolvedTarget,
    Signal,
    SignalRecord,
)


@runtime_checkable
class SignalSource(Protocol):
    """A source that delivers raw memory signals to the Revok pipeline.

    Implementations must not assume a transport. HTTP has no acknowledgment
    semantics; durable transports do.
    """

    def receive(self) -> AsyncIterator[Signal]:
        """Yield incoming signals one at a time.

        Yields:
            Signal: The next incoming signal from this source.

        The iterator runs until the source is exhausted or the coroutine
        is cancelled. Implementations must not block the event loop.
        """
        ...

    async def ack(self, signal: Signal) -> None:
        """Report *signal* as successfully processed.

        Allows the source to advance its position. Must be a no-op, never an
        error, on transports without acknowledgment semantics.
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
class GraphReader(Protocol):
    """Read-only structural introspection of the causal graph.

    Implementations MUST be synchronous (graph is in-memory).
    Returns empty collections for unknown nodes — never raises for absent nodes.
    """

    def has_node(self, entity_id: str) -> bool:
        """Return ``True`` if the entity key exists in the graph.

        Args:
            entity_id: Normalized entity identifier.

        Returns:
            ``True`` if the node is present, ``False`` otherwise.
        """
        ...

    def successors(self, entity_id: str) -> list[str]:
        """Return the direct successors (downstream neighbors) of an entity.

        Args:
            entity_id: Normalized entity identifier.

        Returns:
            List of successor entity keys, or ``[]`` for unknown nodes.
        """
        ...

    def predecessors(self, entity_id: str) -> list[str]:
        """Return the direct predecessors (upstream neighbors) of an entity.

        Args:
            entity_id: Normalized entity identifier.

        Returns:
            List of predecessor entity keys, or ``[]`` for unknown nodes.
        """
        ...

    def edge_weight(self, source_id: str, target_id: str) -> float:
        """Return the weight of the directed edge from *source_id* to *target_id*.

        Args:
            source_id: Normalized source entity identifier.
            target_id: Normalized target entity identifier.

        Returns:
            Edge weight in ``(0, 1]``.

        Raises:
            KeyError: If the edge does not exist.
        """
        ...

    def node_score(self, entity_id: str) -> float:
        """Return the last score recorded for an entity node.

        Args:
            entity_id: Normalized entity identifier.

        Returns:
            The score attribute stored on the node.

        Raises:
            KeyError: If the node does not exist.
        """
        ...

    def nodes(self) -> list[str]:
        """Return all entity keys present in the causal graph.

        Returns:
            Unordered list of all entity keys. Includes isolated nodes
            (nodes with no edges) and nodes that appear only as relation
            targets. Returns ``[]`` for an empty graph. Never raises.

        Note:
            Callers MUST treat the result as unordered.
            The result is graph-derived and does not depend on the state store.
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

    def propagate_detailed(
        self,
        root_entity_id: str,
        initial_pressure: float,
        *,
        max_hops: int,
        min_pressure: float,
        attenuation: float,
    ) -> PropagationTrace:
        """Propagate pressure and return traversal metadata for inspection."""
        ...

    def close(self) -> None:
        """Release any resources held by this backend.

        Must be idempotent — safe to call multiple times.
        No-op for in-memory backends; shuts down managed processes for
        backends that own external resources.
        """
        ...


@runtime_checkable
class SignalHistoryStore(Protocol):
    """Persistent log of signal events for explainability queries."""

    async def record(self, event: SignalRecord) -> None:
        """Persist a single signal event.

        Args:
            event: The ``SignalRecord`` to persist. ``event.id`` is ignored;
                the store assigns an auto-incremented row id.
        """
        ...

    async def get_for_entity(self, entity_key: str) -> list[SignalRecord]:
        """Retrieve all recorded signal events for an entity.

        Args:
            entity_key: Normalized entity identifier.

        Returns:
            List of ``SignalRecord`` instances ordered by ``processed_at`` descending.
        """
        ...

    async def close(self) -> None:
        """Flush pending writes and release database resources.

        Must be idempotent — safe to call multiple times.
        """
        ...


@runtime_checkable
class Resolver(Protocol):
    """Map free-text external signals to graph targets."""

    def resolve(self, signal_text: str) -> list[ResolvedTarget]:
        """Return graph-targeted resolution results, ordered by confidence."""
        ...


@runtime_checkable
class ResolverTraceStore(Protocol):
    """Durable storage contract for resolver invocation traces."""

    async def start_trace(self, trace: ResolutionTrace) -> None:
        """Persist a pending trace."""
        ...

    async def finish_trace(self, trace: ResolutionTrace) -> None:
        """Persist the completed or failed trace state."""
        ...

    async def get_trace(self, signal_id: str) -> ResolutionTrace | None:
        """Return one trace by correlation ID."""
        ...

    async def list_traces(self, offset: int = 0, limit: int = 100) -> list[ResolutionTrace]:
        """Return recent traces in descending creation order."""
        ...


@runtime_checkable
class DedupeStore(Protocol):
    """Durable record of fully processed signal identifiers.

    Storage MUST be independent of trace and inspection storage so that
    disabling trace recording cannot disable idempotency.
    """

    async def has(self, signal_id: str) -> bool:
        """Return ``True`` if *signal_id* was already fully processed."""
        ...

    async def record(self, entry: DedupeRecord) -> None:
        """Persist *entry*, evicting oldest rows beyond the configured bound."""
        ...


@runtime_checkable
class DeadLetterStore(Protocol):
    """Durable store of signals set aside after exceeding the attempt limit."""

    async def dead_letter(self, entry: DeadLetterRecord) -> None:
        """Persist *entry* before the originating source acknowledges it."""
        ...

    async def get_dead_letter(self, signal_id: str) -> DeadLetterRecord | None:
        """Return one dead-letter record, or ``None`` when unknown."""
        ...

    async def list_dead_letters(
        self, offset: int = 0, limit: int = 100
    ) -> list[DeadLetterRecord]:
        """Return dead-letter records newest first."""
        ...


@runtime_checkable
class Inspector(Protocol):
    """Read-only explainability API over entity state, causal graph, and signal history."""

    async def inspect_entity(self, entity_key: str) -> InspectionReport | None:
        """Return a snapshot of an entity's state and its direct causal neighbors.

        Args:
            entity_key: Normalized entity identifier.

        Returns:
            ``InspectionReport`` if the entity is in the store, ``None`` otherwise.
        """
        ...

    async def get_downstream(
        self,
        entity_key: str,
        *,
        max_hops: int,
        min_pressure: float,
        attenuation: float,
    ) -> list[DownstreamEntity]:
        """Return all entities reachable from *entity_key* via forward propagation.

        Args:
            entity_key: Root entity identifier.
            max_hops: Maximum BFS depth.
            min_pressure: Minimum pressure threshold; nodes below this are excluded.
            attenuation: Per-hop pressure multiplier in ``(0, 1]``.

        Returns:
            List of ``DownstreamEntity`` sorted by descending pressure.
        """
        ...

    async def get_paths(
        self,
        entity_key: str,
        *,
        max_hops: int,
        min_pressure: float,
        attenuation: float,
        max_paths: int,
    ) -> list[PropagationPath]:
        """Return all propagation paths from upstream roots to *entity_key*.

        Args:
            entity_key: Target entity identifier.
            max_hops: Maximum backward BFS depth.
            min_pressure: Minimum pressure threshold for path inclusion.
            attenuation: Per-hop pressure multiplier in ``(0, 1]``.
            max_paths: Maximum number of paths to return.

        Returns:
            List of ``PropagationPath`` with ``is_dominant`` set on the highest-pressure path.
        """
        ...

    async def get_signals(self, entity_key: str) -> list[SignalRecord] | None:
        """Return the signal history for *entity_key*, or ``None`` if history is disabled.

        Args:
            entity_key: Normalized entity identifier.

        Returns:
            List of ``SignalRecord`` if history is enabled, ``None`` if disabled.

        Raises:
            EntityNotFoundError: If the entity does not exist in the store.
        """
        ...
