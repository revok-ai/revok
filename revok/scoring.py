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

"""Confidence/pressure scoring engine for the Revok pipeline.

Confidence model: signals *degrade* confidence (external change detected);
time *recovers* confidence toward ``score_cap`` (stable world = trustworthy
memory).

Recovery formula (between signals — called by ``decay_at``):

    λ = ln(2) / half_life_seconds
    gap = score_cap - current_score
    gap_recovered = gap × exp(-λ × Δt)
    score_recovered = score_cap - gap_recovered

Degradation formula (signal arrives — called by ``score``):

    score_new = score_recovered - signal_strength
    score_new = max(0.0, score_new)

First signal (no prior record):

    score_new = max(0.0, score_cap - signal_strength)
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
        """Compute the new confidence score after a signal arrives.

        Signals degrade confidence (external change detected).  Between signals,
        confidence recovers toward ``score_cap`` (stable world = trustworthy
        memory).

        For the very first signal (``existing`` is ``None``) the score is
        ``max(0.0, score_cap - signal_strength)``.

        For subsequent signals the previous score is first recovered toward
        ``score_cap`` according to elapsed time, then reduced by
        ``signal_strength``.

        Args:
            existing: Current persisted ``EntityRecord``, or ``None``.
            now: Current Unix epoch timestamp in seconds.

        Returns:
            New score in the range ``[0.0, score_cap]``.
        """
        if existing is None:
            return max(0.0, self._score_cap - self._signal_strength)

        delta_t = max(0.0, now - existing.valid_time)
        gap = self._score_cap - existing.score
        score_recovered = self._score_cap - gap * math.exp(-self._lambda * delta_t)
        return max(0.0, score_recovered - self._signal_strength)

    def decay_at(self, record: EntityRecord, now: float) -> float:
        """Return the current recovered confidence without applying a new signal.

        Used for read-time score display (Phase 6, T030).  Confidence grows
        toward ``score_cap`` as time passes with no new signals (stable world).

        Args:
            record: The stored ``EntityRecord``.
            now: Current Unix epoch timestamp in seconds.

        Returns:
            Recovered score in ``[0.0, score_cap]`` (no signal degradation
            applied).
        """
        delta_t = max(0.0, now - record.valid_time)
        gap = self._score_cap - record.score
        return self._score_cap - gap * math.exp(-self._lambda * delta_t)
