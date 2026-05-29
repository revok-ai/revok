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

"""Named-regex entity extraction for the Revok pipeline.

``EntityMatcher`` compiles patterns from config at init time and applies
them to signal content via the stdlib ``re`` module only (no spaCy,
no NLTK — Constitution § V).
"""

from __future__ import annotations

import logging
import re

from revok.config import EntityMatcherConfig
from revok.models import Entity

logger = logging.getLogger(__name__)


class EntityMatcher:
    """Extract named entities from text using pre-compiled regex patterns.

    Patterns are applied in order; all non-overlapping matches are returned
    across all patterns. Each match produces an :class:`~revok.models.Entity`.

    Args:
        config: Entity matcher configuration with named patterns.
    """

    def __init__(self, config: EntityMatcherConfig) -> None:
        self._patterns: list[tuple[str, re.Pattern[str]]] = [
            (pat.name, re.compile(pat.regex))
            for pat in config.patterns
        ]

    def match(self, text: str) -> list[Entity]:
        """Extract all entities from *text*.

        Args:
            text: The raw text to scan for entity matches.

        Returns:
            list[Entity]: Deduplicated entities by normalized key. If the
            same key is matched by multiple patterns or multiple times, the
            first occurrence is kept.
        """
        seen_keys: set[str] = set()
        entities: list[Entity] = []

        for name, pattern in self._patterns:
            for match in pattern.finditer(text):
                raw_text = match.group()
                key = Entity.normalize(raw_text)
                if key and key not in seen_keys:
                    seen_keys.add(key)
                    entities.append(Entity(key=key, raw_text=raw_text, pattern_name=name))

        return entities
