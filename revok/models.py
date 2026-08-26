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

"""Core domain dataclasses for the Revok pipeline.

All dataclasses here are pure data containers with no business logic.
Immutable dataclasses use ``frozen=True``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Signal:
    """Primary input to the Revok pipeline — a memory event from an AI agent.

    Immutable once created (FR-004).

    Attributes:
        raw_content: Original text payload from the agent.
        source_id: Identifier of the originating agent or session.
        timestamp: Unix epoch seconds (float for sub-second precision).
        http_method: Original HTTP method (e.g., ``"POST"``).
        http_path: Original HTTP path (e.g., ``"/v1/memories"``).
        original_body: Raw request body bytes for pass-through on error.
        headers: Original HTTP headers for upstream forwarding.
    """

    raw_content: str
    source_id: str
    timestamp: float
    http_method: str
    http_path: str
    original_body: bytes
    headers: dict[str, str]
    valid_time: float | None = None


@dataclass(frozen=True)
class Entity:
    """A named concept extracted from signal content.

    Attributes:
        key: Normalized identifier — ``raw_text.lower().strip()``.
        raw_text: Original matched text from the signal.
        pattern_name: Name of the config pattern that matched (e.g., ``"person"``).
    """

    key: str
    raw_text: str
    pattern_name: str

    @staticmethod
    def normalize(raw_text: str) -> str:
        """Return the normalized key for *raw_text*.

        Args:
            raw_text: The raw matched string.

        Returns:
            Lowercased, stripped string.
        """
        return raw_text.lower().strip()


@dataclass
class EntityRecord:
    """Persisted state of a tracked entity in the SQLite store and hot layer.

    Attributes:
        entity_key: Normalized entity identifier.
        score: Current confidence score ``[0.0, score_cap]``.
        valid_time: Unix epoch seconds when the signal event actually occurred
            (event time). Drives score decay.
        transaction_time: Unix epoch seconds when Revok recorded the signal
            (wall-clock write time). Always >= valid_time.
        signal_count: Total number of signals that referenced this entity.
        pattern_name: Pattern category from the most recent match.

    Caller contract:
        ``transaction_time >= valid_time`` must hold. Structurally guaranteed
        by the write-time clamp in ``metadata_writer.enrich``. Not enforced
        via ``__post_init__``.
    """

    entity_key: str
    score: float
    valid_time: float
    transaction_time: float
    signal_count: int
    pattern_name: str
    contradiction_count: int = 0
    last_contradiction_time: float | None = None
    last_value_fingerprint: str | None = None

    @property
    def last_seen(self) -> float:
        """Backward-compat alias — returns ``valid_time``."""
        return self.valid_time


@dataclass
class EnrichedPayload:
    """Original signal JSON body augmented with Revok's entity metadata block.

    Forwarded to the upstream Mem0 adapter after enrichment.

    Attributes:
        original_body: Parsed original JSON body from the signal.
        entities: All entity records scored from this signal.
        revok_version: Revok version string (e.g., ``"0.1.0"``).
        processed_at: Unix epoch seconds of enrichment completion.
    """

    original_body: dict[str, object]
    entities: list[EntityRecord]
    revok_version: str
    processed_at: float
    original_bytes: bytes = field(default_factory=bytes)

    def to_upstream_dict(self) -> dict[str, object]:
        """Merge original body with the ``x_revok`` metadata block.

        Returns:
            dict: Combined payload ready for JSON serialisation and forwarding.
        """
        import datetime  # noqa: PLC0415

        entity_list = [
            {
                "id": rec.entity_key,
                "score": rec.score,
                "signal_count": rec.signal_count,
                "last_seen": datetime.datetime.fromtimestamp(
                    rec.valid_time, tz=datetime.timezone.utc
                ).isoformat(),
                "pattern_name": rec.pattern_name,
            }
            for rec in self.entities
        ]

        processed_at_iso = datetime.datetime.fromtimestamp(
            self.processed_at, tz=datetime.timezone.utc
        ).isoformat()

        return {
            **self.original_body,
            "x_revok": {
                "version": self.revok_version,
                "processed_at": processed_at_iso,
                "entities": entity_list,
            },
        }


@dataclass
class MemoryAdapterResponse:
    """Response returned by the downstream memory adapter after a proxied operation.

    Attributes:
        status: HTTP status code from upstream.
        body: Raw response body bytes.
        headers: Response headers from upstream.
        is_error: ``True`` if ``status >= 400``.
    """

    status: int
    body: bytes
    headers: dict[str, str]
    is_error: bool


# ---------------------------------------------------------------------------
# Inspector dataclasses (Feature 006)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CausalNeighbor:
    """A direct causal neighbor of an inspected entity.

    Attributes:
        entity_key: Normalized identifier of the neighbor entity.
        direction: ``"upstream"`` (predecessor) or ``"downstream"`` (successor).
        weight: Edge weight on the causal relation ``(0, 1]``.
        score: Current score of the neighbor entity, or ``None`` if not in store.
    """

    entity_key: str
    direction: str  # Literal["upstream", "downstream"]
    weight: float
    score: float | None


@dataclass(frozen=True)
class InspectionReport:
    """Snapshot of an entity's state and its direct causal neighborhood.

    Attributes:
        entity_key: Normalized entity identifier.
        score: Current confidence score.
        valid_time: Unix epoch seconds of the most recent signal event.
        transaction_time: Unix epoch seconds when the record was written.
        signal_count: Total number of signals referencing this entity.
        contradiction_count: Total number of contradiction events detected.
        upstream: Direct predecessors in the causal graph.
        downstream: Direct successors in the causal graph.
        inspected_at: Unix epoch timestamp when this snapshot was generated.
    """

    entity_key: str
    score: float
    valid_time: float
    transaction_time: float
    signal_count: int
    contradiction_count: int
    upstream: list[CausalNeighbor]
    downstream: list[CausalNeighbor]
    inspected_at: float


@dataclass(frozen=True)
class DownstreamEntity:
    """An entity reachable from a root via forward causal propagation.

    Attributes:
        entity_key: Normalized entity identifier.
        pressure: Maximum attenuated pressure received at this entity.
        hops: Number of causal hops from the root entity.
    """

    entity_key: str
    pressure: float
    hops: int


@dataclass(frozen=True)
class PropagationPath:
    """One complete path from a root to a target entity through the causal graph.

    Attributes:
        hops: Ordered list of entity keys from root (index 0) to target (last).
        pressures: Attenuated pressure at each hop; ``pressures[0] == 1.0`` at the root.
        is_dominant: ``True`` if this path carries the highest terminal pressure.
    """

    hops: list[str]
    pressures: list[float]
    is_dominant: bool


@dataclass(frozen=True)
class SignalRecord:
    """A recorded signal event for an entity, stored in the signal history log.

    Attributes:
        id: Auto-assigned SQLite row id; ``None`` before persistence.
        entity_key: Normalized entity identifier that received the signal.
        source_id: Identifier of the originating agent or signal source.
        processed_at: Unix epoch seconds when the signal was processed.
        score_before: Entity score before the signal was applied; ``None`` for new entities.
        score_after: Entity score after the signal was applied.
        is_propagated: ``True`` when this event was created by causal propagation.
        upstream_source: Key of the root entity that triggered propagation; ``None`` for direct signals.
    """

    id: int | None
    entity_key: str
    source_id: str
    processed_at: float
    score_before: float | None
    score_after: float
    is_propagated: bool
    upstream_source: str | None


# ---------------------------------------------------------------------------
# Graph topology dataclasses (Feature 007)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GraphNodeView:
    """A node entry in the graph topology response.

    Attributes:
        key: Normalized entity identifier.
        score: Resolved live score (decay_at > node_score > 1.0 fallback).
    """

    key: str
    score: float


@dataclass(frozen=True)
class GraphEdgeView:
    """A directed edge entry in the graph topology response.

    Attributes:
        source: Source entity key (tail of directed edge).
        target: Target entity key (head of directed edge).
        weight: Edge propagation weight in (0, 1].
    """

    source: str
    target: str
    weight: float


@dataclass(frozen=True)
class GraphTopologyResponse:
    """Complete graph topology snapshot for GET /v1/inspector/graph.

    Both ``nodes`` and ``edges`` are derived from a single synchronous
    graph.nodes() snapshot before any await (FR-016).

    Attributes:
        nodes: All graph nodes with resolved live scores.
        edges: All directed edges, enumerated over snapshot keys only.
    """

    nodes: list[GraphNodeView]
    edges: list[GraphEdgeView]
