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

import hashlib
import logging
import math
import re

from revok.config import ScoringConfig, SignalPressureConfig
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
        self._contradiction_window_seconds = config.contradiction_window_seconds
        self._contradiction_penalty = config.contradiction_penalty
        self._signal_pressure = config.signal_pressure or SignalPressureConfig()

    @staticmethod
    def extract_fingerprint(raw_content: str) -> str | None:
        """Extract a canonical value fingerprint from signal content.

        Tries to parse the first numeric value (optionally preceded by ``$``).
        If found, normalises to ``str(float(value))`` so ``"$500"``, ``"500"``,
        ``"500.0"``, and ``"$500.00"`` all produce ``"500.0"``.

        Falls back to a lowercase SHA-256 hex digest of the entire content when
        no numeric value is found.  Returns ``None`` when *raw_content* is
        empty.

        Args:
            raw_content: Raw signal content string.

        Returns:
            Canonical fingerprint string, or ``None`` if *raw_content* is empty.
        """
        if not raw_content:
            return None
        match = re.search(r"\$?\s*(\d+(?:\.\d+)?)", raw_content)
        if match:
            return str(float(match.group(1)))
        return hashlib.sha256(raw_content.lower().encode()).hexdigest()

    def detect_contradiction(
        self,
        existing: EntityRecord | None,
        new_fingerprint: str | None,
        new_valid_time: float,
    ) -> bool:
        """Return ``True`` when a contradiction is detected.

        A contradiction is detected when all of these conditions hold:

        * *existing* is not ``None`` (there is a prior signal to compare against)
        * both fingerprints are not ``None``
        * the fingerprints differ (conflicting values)
        * the gap between signals is **strictly less than** the configured window
          (open interval — a gap equal to the window does not trigger)

        Args:
            existing: Prior entity record, or ``None`` for first signal.
            new_fingerprint: Fingerprint of the incoming signal content.
            new_valid_time: ``valid_time`` of the incoming signal.

        Returns:
            ``True`` if a contradiction is detected, ``False`` otherwise.
        """
        if existing is None:
            return False
        if new_fingerprint is None or existing.last_value_fingerprint is None:
            return False
        if new_fingerprint == existing.last_value_fingerprint:
            return False
        gap = new_valid_time - existing.valid_time
        return gap < self._contradiction_window_seconds

    def score(
        self,
        existing: EntityRecord | None,
        now: float,
        *,
        is_contradiction: bool = False,
    ) -> float:
        """Compute the new confidence score after a signal arrives.

        Signals degrade confidence (external change detected).  Between signals,
        confidence recovers toward ``score_cap`` (stable world = trustworthy
        memory).

        For the very first signal (``existing`` is ``None``) the score is
        ``max(0.0, score_cap - signal_strength)``.

        For subsequent signals the previous score is first recovered toward
        ``score_cap`` according to elapsed time, then reduced by
        ``signal_strength`` (and additionally by ``contradiction_penalty`` when
        *is_contradiction* is ``True``).

        Args:
            existing: Current persisted ``EntityRecord``, or ``None``.
            now: Current Unix epoch timestamp in seconds.
            is_contradiction: When ``True``, apply an additional
                ``contradiction_penalty`` deduction on top of ``signal_strength``.

        Returns:
            New score in the range ``[0.0, score_cap]``.
        """
        if existing is None:
            base = max(0.0, self._score_cap - self._signal_strength)
            if is_contradiction:
                base = max(0.0, base - self._contradiction_penalty)
            return base

        delta_t = max(0.0, now - existing.valid_time)
        gap = self._score_cap - existing.score
        score_recovered = self._score_cap - gap * math.exp(-self._lambda * delta_t)
        score_new = score_recovered - self._signal_strength
        if is_contradiction:
            score_new -= self._contradiction_penalty
        return max(0.0, score_new)

    def score_with_pressure(
        self,
        existing: EntityRecord | None,
        now: float,
        pressure: float,
        *,
        is_contradiction: bool = False,
    ) -> float:
        """Compute score using a caller-supplied pressure value in [0, 1]."""
        pressure = max(0.0, min(1.0, float(pressure)))
        effective_strength = self._signal_strength * pressure

        if existing is None:
            base = max(0.0, self._score_cap - effective_strength)
            if is_contradiction:
                base = max(0.0, base - self._contradiction_penalty)
            return base

        delta_t = max(0.0, now - existing.valid_time)
        gap = self._score_cap - existing.score
        score_recovered = self._score_cap - gap * math.exp(-self._lambda * delta_t)
        score_new = score_recovered - effective_strength
        if is_contradiction:
            score_new -= self._contradiction_penalty
        return max(0.0, score_new)

    def pressure_for_severity(self, severity: str | None) -> float:
        """Map a severity label to configured pressure with fallback defaults."""
        return self._signal_pressure.resolve(severity)

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
