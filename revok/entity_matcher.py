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
    """Extract named entities from text using alias catalogs and/or regex patterns.

    Matching priority:

    1. **Alias catalog** (``config.entities``): plain-text aliases compiled to
       word-boundary, case-insensitive patterns internally. Entity key is the
       canonical ``EntityDef.id`` regardless of which alias matched.
    2. **Legacy regex patterns** (``config.patterns``): raw named patterns;
       entity key is ``raw_text.lower().strip()`` (backward-compatible).

    For large catalogs, callers should send ``X-Revok-Entity: <id>`` instead
    of relying on text matching — see :func:`~revok.metadata_writer.enrich`.

    Args:
        config: Entity matcher configuration.
    """

    def __init__(self, config: EntityMatcherConfig) -> None:
        # Legacy regex patterns (backward-compatible; key = raw_text.lower())
        self._patterns: list[tuple[str, re.Pattern[str]]] = [
            (pat.name, re.compile(pat.regex))
            for pat in config.patterns
        ]
        # Alias catalog: compile each alias as word-boundary case-insensitive regex.
        # Tuple: (compiled_pattern, canonical_entity_id)
        self._alias_patterns: list[tuple[re.Pattern[str], str]] = []
        for entity_def in config.entities:
            for alias in entity_def.aliases:
                compiled = re.compile(
                    r"\b" + re.escape(alias) + r"\b",
                    re.IGNORECASE,
                )
                self._alias_patterns.append((compiled, entity_def.id))

        if not self._patterns and not self._alias_patterns:
            logger.warning(
                "EntityMatcher has no patterns or entities configured. "
                "Only caller-tagged entities (X-Revok-Entity header) will be processed."
            )

    def match(self, text: str) -> list[Entity]:
        """Extract all entities from *text*.

        Alias catalog entries are checked first; legacy regex patterns follow.
        Deduplication is by entity key — first occurrence wins.

        Args:
            text: The raw text to scan for entity matches.

        Returns:
            list[Entity]: Deduplicated entities. Alias catalog entries use the
            canonical ``EntityDef.id`` as the key; legacy regex entries use
            ``raw_text.lower().strip()``.
        """
        seen_keys: set[str] = set()
        entities: list[Entity] = []

        # Alias catalog: key = canonical entity id (not raw matched text)
        for pattern, canonical_id in self._alias_patterns:
            for match in pattern.finditer(text):
                if canonical_id and canonical_id not in seen_keys:
                    seen_keys.add(canonical_id)
                    entities.append(
                        Entity(
                            key=canonical_id,
                            raw_text=match.group(),
                            pattern_name=canonical_id,
                        )
                    )

        # Legacy regex patterns: key = raw_text.lower().strip() (backward-compatible)
        for name, pattern in self._patterns:
            for match in pattern.finditer(text):
                raw_text = match.group()
                key = Entity.normalize(raw_text)
                if key and key not in seen_keys:
                    seen_keys.add(key)
                    entities.append(Entity(key=key, raw_text=raw_text, pattern_name=name))

        return entities
