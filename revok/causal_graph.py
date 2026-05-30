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

"""Causal graph scaffold backed by NetworkX (Constitution § V — scaffold only).

In Revok 0.1.0 this module **only** maintains the directed entity graph.
No traversal, inference, or graph query operations are implemented in this
version — those are deferred to a future release.
"""

from __future__ import annotations

import logging

import networkx as nx  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


class CausalGraph:
    """A directed graph of entity relationships, backed by ``nx.DiGraph``.

    Entities are nodes; relations are directed edges.  The ``score`` attribute
    on each node reflects the most recently observed relevance score for that
    entity.

    This is a **scaffold** implementation (FR-014, Constitution § V).
    Graph queries and traversal will be added in a future release.
    """

    def __init__(self) -> None:
        self._graph: nx.DiGraph = nx.DiGraph()

    def add_entity(self, entity_id: str, score: float) -> None:
        """Add or update an entity node in the causal graph.

        If the node already exists its ``score`` attribute is updated in place.

        Args:
            entity_id: Normalized entity identifier (e.g. ``"alice"``).
            score:     Current relevance score for the entity.
        """
        if self._graph.has_node(entity_id):
            self._graph.nodes[entity_id]["score"] = score
            logger.debug("Updated entity node %r score=%.4f", entity_id, score)
        else:
            self._graph.add_node(entity_id, score=score)
            logger.debug("Added entity node %r score=%.4f", entity_id, score)

    def add_relation(self, source_id: str, target_id: str) -> None:
        """Add a directed edge from *source_id* to *target_id*.

        Both nodes are created automatically if they do not yet exist
        (with ``score=0.0``).

        Args:
            source_id: The entity at the tail of the directed edge.
            target_id: The entity at the head of the directed edge.
        """
        for node_id in (source_id, target_id):
            if not self._graph.has_node(node_id):
                self._graph.add_node(node_id, score=0.0)
        self._graph.add_edge(source_id, target_id)
        logger.debug("Added relation %r → %r", source_id, target_id)

    @property
    def node_count(self) -> int:
        """Return the number of entity nodes currently in the graph."""
        return int(self._graph.number_of_nodes())

    @property
    def edge_count(self) -> int:
        """Return the number of directed relation edges currently in the graph."""
        return int(self._graph.number_of_edges())
