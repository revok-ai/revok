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

"""Signal enrichment pipeline: extract entities → score → persist → EnrichedPayload.

The ``enrich()`` coroutine is the core of the Revok MVP value proposition (US1).
It is resilient: any exception during entity processing is logged at ERROR and
the function returns an ``EnrichedPayload`` with an empty entities list rather
than propagating the error (acceptance scenario 1.4).
"""

from __future__ import annotations

import json
import logging
import time

from revok.entity_matcher import EntityMatcher
from revok.interfaces import StateStore
from revok.models import EnrichedPayload, Entity, EntityRecord, Signal
from revok.scoring import ScoringEngine
from revok.causal_graph import CausalGraph

logger = logging.getLogger(__name__)


async def enrich(
    signal: Signal,
    matcher: EntityMatcher,
    scorer: ScoringEngine,
    store: StateStore,
) -> EnrichedPayload:
    """Enrich a write signal with entity metadata.

    Steps:

    1. Parse ``signal.original_body`` as JSON (falls back to ``{}`` on error).
    2. Extract entities from ``signal.raw_content`` using *matcher*.
    3. For each entity: fetch existing record, compute new score, persist the
       updated :class:`~revok.models.EntityRecord`.
    4. Return :class:`~revok.models.EnrichedPayload` with all scored records.

    On any exception during steps 2–3 the function logs at ``ERROR`` and
    returns a payload with an empty entities list (acceptance scenario 1.4).

    Args:
        signal: The incoming write signal to enrich.
        matcher: Named-regex entity extractor.
        scorer: Exponential decay scoring engine.
        store: Persistent entity state store.

    Returns:
        :class:`~revok.models.EnrichedPayload` ready for the upstream adapter.
    """
    from revok import __version__  # deferred to avoid circular import

    now = time.time()

    # --- Step 1: parse original body ----------------------------------------
    original_body: dict[str, object] = {}
    try:
        if signal.original_body:
            parsed = json.loads(signal.original_body)
            original_body = parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, ValueError):
        logger.warning(
            "Signal from %s has non-JSON body; using empty dict for enrichment",
            signal.source_id,
        )

    # --- Steps 2–3: extract, score, persist ---------------------------------
    scored_records: list[EntityRecord] = []
    graph = CausalGraph()
    try:
        # Header-tagged mode: caller explicitly names the entity, bypasses text
        # matching. Recommended for large catalogs and production deployments.
        header_entity_id: str | None = next(
            (
                v.strip()
                for k, v in signal.headers.items()
                if k.lower() == "x-revok-entity"
            ),
            None,
        )
        if header_entity_id:
            entities: list[Entity] = [
                Entity(
                    key=header_entity_id.lower(),
                    raw_text=header_entity_id,
                    pattern_name="header",
                )
            ]
        else:
            entities = matcher.match(signal.raw_content)
        for entity in entities:
            existing = await store.get(entity.key)
            new_score = scorer.score(existing, now)
            signal_count = (existing.signal_count + 1) if existing is not None else 1
            record = EntityRecord(
                entity_key=entity.key,
                score=new_score,
                last_seen=now,
                signal_count=signal_count,
                pattern_name=entity.pattern_name,
            )
            await store.put(record)
            scored_records.append(record)
            graph.add_entity(entity.key, new_score)
    except Exception:
        logger.error(
            "Enrichment pipeline failed for signal from %s",
            signal.source_id,
            exc_info=True,
        )
        scored_records = []

    return EnrichedPayload(
        original_body=original_body,
        entities=scored_records,
        revok_version=__version__,
        processed_at=now,
    )
