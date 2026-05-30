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

"""Exponential decay scoring engine for the Revok pipeline.

The decay formula (from ``specs/001-create-spec-branch/research.md``):

    λ = ln(2) / half_life_seconds
    score_decayed = existing.score * exp(-λ * Δt)
    score_new     = score_decayed + signal_strength
    score_new     = min(score_new, score_cap)
"""

from __future__ import annotations

import logging
import math

from revok.config import ScoringConfig
from revok.models import EntityRecord

logger = logging.getLogger(__name__)


class ScoringEngine:
    """Compute entity relevance scores using exponential decay.

    Parameters are fully driven by :class:`~revok.config.ScoringConfig`; no
    values are hard-coded (FR-001).

    Args:
        config: Scoring configuration with decay parameters.
    """

    def __init__(self, config: ScoringConfig) -> None:
        if config.half_life_seconds <= 0:
            raise ValueError(
                f"ScoringEngine: half_life_seconds must be > 0; "
                f"got {config.half_life_seconds!r}"
            )
        self._signal_strength = config.signal_strength
        self._score_cap = config.score_cap
        self._lambda = math.log(2) / config.half_life_seconds

    def score(self, existing: EntityRecord | None, now: float) -> float:
        """Compute the new score for an entity after a signal.

        If *existing* is ``None`` (first signal for the entity) the score
        equals ``signal_strength`` (capped at ``score_cap``).  Otherwise the
        previous score is first decayed according to elapsed time before
        adding the signal boost.

        Args:
            existing: Current persisted ``EntityRecord``, or ``None``.
            now: Current Unix epoch timestamp in seconds.

        Returns:
            New score in the range ``[0.0, score_cap]``.
        """
        if existing is None:
            return min(self._signal_strength, self._score_cap)

        delta_t = max(0.0, now - existing.last_seen)
        score_decayed = existing.score * math.exp(-self._lambda * delta_t)
        score_new = score_decayed + self._signal_strength
        return min(score_new, self._score_cap)

    def decay_at(self, record: EntityRecord, now: float) -> float:
        """Return the current decayed score without adding a signal boost.

        Used for read-time score display (Phase 6, T030).

        Args:
            record: The stored ``EntityRecord``.
            now: Current Unix epoch timestamp in seconds.

        Returns:
            Decayed score (no signal boost applied).
        """
        delta_t = max(0.0, now - record.last_seen)
        return record.score * math.exp(-self._lambda * delta_t)
