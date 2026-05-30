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

from dataclasses import dataclass


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
        last_seen: Unix epoch seconds of the most recent signal.
        signal_count: Total number of signals that referenced this entity.
        pattern_name: Pattern category from the most recent match.
    """

    entity_key: str
    score: float
    last_seen: float
    signal_count: int
    pattern_name: str


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
                    rec.last_seen, tz=datetime.timezone.utc
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
