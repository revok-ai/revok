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

"""Causal graph backed by NetworkX for weighted causal propagation."""

from __future__ import annotations

import logging

import networkx as nx  # type: ignore[import-untyped]

from revok.interfaces import GraphBackend, GraphReader
from revok.models import PropagationStep, PropagationTrace

logger = logging.getLogger(__name__)


class CausalGraph(GraphBackend, GraphReader):
    """A directed weighted graph of entity relationships.

    Entities are nodes; relations are directed edges.  The ``score`` attribute
    on each node reflects the most recently observed relevance score for that
    entity.

    Propagation uses bounded breadth-first traversal with per-edge attenuation.
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

    def add_relation(self, source_id: str, target_id: str, weight: float = 1.0) -> None:
        """Add a directed edge from *source_id* to *target_id*.

        Both nodes are created automatically if they do not yet exist
        (with ``score=0.0``).

        Args:
            source_id: The entity at the tail of the directed edge.
            target_id: The entity at the head of the directed edge.
            weight: Propagation weight in ``(0, 1]``.
        """
        if weight <= 0.0 or weight > 1.0:
            raise ValueError(f"relation weight must be in (0, 1], got {weight!r}")
        for node_id in (source_id, target_id):
            if not self._graph.has_node(node_id):
                self._graph.add_node(node_id, score=0.0)
        self._graph.add_edge(source_id, target_id, weight=float(weight))
        logger.debug("Added relation %r → %r (weight=%.4f)", source_id, target_id, weight)

    def propagate(
        self,
        root_entity_id: str,
        initial_pressure: float,
        *,
        max_hops: int,
        min_pressure: float,
        attenuation: float,
    ) -> dict[str, float]:
        """Propagate pressure with bounded BFS and max-pressure aggregation.

        The returned mapping excludes the root entity.
        """
        if max_hops < 0:
            raise ValueError("max_hops must be >= 0")
        if min_pressure < 0.0 or min_pressure > 1.0:
            raise ValueError("min_pressure must be in [0, 1]")
        if attenuation <= 0.0 or attenuation > 1.0:
            raise ValueError("attenuation must be in (0, 1]")
        if initial_pressure <= 0.0:
            return {}
        if not self._graph.has_node(root_entity_id):
            return {}

        frontier: dict[str, float] = {root_entity_id: float(initial_pressure)}
        seen: set[str] = {root_entity_id}
        result: dict[str, float] = {}

        for _hop in range(1, max_hops + 1):
            next_frontier: dict[str, float] = {}
            for source_id, source_pressure in frontier.items():
                for target_id in self._graph.successors(source_id):
                    if target_id in seen:
                        continue
                    edge_weight = float(self._graph[source_id][target_id].get("weight", 1.0))
                    pressure = source_pressure * edge_weight * attenuation
                    if pressure < min_pressure:
                        continue
                    current = next_frontier.get(target_id)
                    if current is None or pressure > current:
                        next_frontier[target_id] = pressure

            if not next_frontier:
                break

            for entity_key, pressure in next_frontier.items():
                prev = result.get(entity_key)
                if prev is None or pressure > prev:
                    result[entity_key] = pressure

            seen.update(next_frontier.keys())
            frontier = next_frontier

        return result

    def propagate_detailed(
        self,
        root_entity_id: str,
        initial_pressure: float,
        *,
        max_hops: int,
        min_pressure: float,
        attenuation: float,
    ) -> PropagationTrace:
        """Propagate pressure and retain traversal metadata and stop reason."""
        if max_hops < 0:
            raise ValueError("max_hops must be >= 0")
        if min_pressure < 0.0 or min_pressure > 1.0:
            raise ValueError("min_pressure must be in [0, 1]")
        if attenuation <= 0.0 or attenuation > 1.0:
            raise ValueError("attenuation must be in (0, 1]")

        root_pressure = float(initial_pressure)
        root_step = PropagationStep(
            entity_key=root_entity_id,
            depth=0,
            pressure=root_pressure,
        )
        if root_pressure <= 0.0:
            return PropagationTrace(
                root_entity_key=root_entity_id,
                initial_pressure=root_pressure,
                steps=[root_step],
                termination_reason="min_pressure",
            )
        if not self._graph.has_node(root_entity_id):
            return PropagationTrace(
                root_entity_key=root_entity_id,
                initial_pressure=root_pressure,
                steps=[],
                termination_reason="completed",
            )

        frontier: dict[str, float] = {root_entity_id: root_pressure}
        seen: set[str] = {root_entity_id}
        result: dict[str, float] = {}
        steps = [root_step]
        below_floor = False

        for depth in range(1, max_hops + 1):
            next_frontier: dict[str, tuple[float, str, float]] = {}
            saw_candidate = False
            for source_id, source_pressure in frontier.items():
                for target_id in self._graph.successors(source_id):
                    if target_id in seen:
                        continue
                    saw_candidate = True
                    edge_weight = float(self._graph[source_id][target_id].get("weight", 1.0))
                    pressure = source_pressure * edge_weight * attenuation
                    if pressure < min_pressure:
                        below_floor = True
                        continue
                    current = next_frontier.get(target_id)
                    if current is None or pressure > current[0]:
                        next_frontier[target_id] = (pressure, source_id, edge_weight)

            if not next_frontier:
                reason = "min_pressure" if below_floor and saw_candidate else "completed"
                return PropagationTrace(
                    root_entity_key=root_entity_id,
                    initial_pressure=root_pressure,
                    steps=steps,
                    termination_reason=reason,
                )

            for entity_key, (pressure, parent_id, edge_weight) in next_frontier.items():
                result[entity_key] = pressure
                steps.append(
                    PropagationStep(
                        entity_key=entity_key,
                        depth=depth,
                        pressure=pressure,
                        parent_entity_key=parent_id,
                        edge_weight=edge_weight,
                    )
                )
            seen.update(next_frontier)
            frontier = {key: value[0] for key, value in next_frontier.items()}

        has_eligible_successor = False
        has_below_floor_successor = False
        for source_id, source_pressure in frontier.items():
            for target_id in self._graph.successors(source_id):
                if target_id in seen:
                    continue
                pressure = (
                    source_pressure
                    * float(self._graph[source_id][target_id].get("weight", 1.0))
                    * attenuation
                )
                if pressure < min_pressure:
                    has_below_floor_successor = True
                else:
                    has_eligible_successor = True

        if has_eligible_successor:
            reason = "max_hops"
        elif has_below_floor_successor:
            reason = "min_pressure"
        else:
            reason = "completed"
        return PropagationTrace(
            root_entity_key=root_entity_id,
            initial_pressure=root_pressure,
            steps=steps,
            termination_reason=reason,
        )

    @property
    def node_count(self) -> int:
        """Return the number of entity nodes currently in the graph."""
        return int(self._graph.number_of_nodes())

    @property
    def edge_count(self) -> int:
        """Return the number of directed relation edges currently in the graph."""
        return int(self._graph.number_of_edges())

    # ------------------------------------------------------------------
    # GraphReader interface (read-only structural introspection)
    # ------------------------------------------------------------------

    def has_node(self, entity_id: str) -> bool:
        """Return ``True`` if the entity key exists in the graph.

        Args:
            entity_id: Normalized entity identifier.

        Returns:
            ``True`` if the node is present, ``False`` otherwise.
        """
        return bool(self._graph.has_node(entity_id))

    def successors(self, entity_id: str) -> list[str]:
        """Return the direct successors of an entity.

        Args:
            entity_id: Normalized entity identifier.

        Returns:
            List of successor entity keys, or ``[]`` for unknown nodes.
        """
        if not self._graph.has_node(entity_id):
            return []
        return list(self._graph.successors(entity_id))

    def predecessors(self, entity_id: str) -> list[str]:
        """Return the direct predecessors of an entity.

        Args:
            entity_id: Normalized entity identifier.

        Returns:
            List of predecessor entity keys, or ``[]`` for unknown nodes.
        """
        if not self._graph.has_node(entity_id):
            return []
        return list(self._graph.predecessors(entity_id))

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
        if not self._graph.has_edge(source_id, target_id):
            raise KeyError(f"No edge from {source_id!r} to {target_id!r}")
        return float(self._graph[source_id][target_id].get("weight", 1.0))

    def close(self) -> None:
        """No-op — NetworkX graphs hold no external resources."""

    def node_score(self, entity_id: str) -> float:
        """Return the last score recorded for an entity node.

        Args:
            entity_id: Normalized entity identifier.

        Returns:
            The score attribute stored on the node.

        Raises:
            KeyError: If the node does not exist.
        """
        if not self._graph.has_node(entity_id):
            raise KeyError(f"No node {entity_id!r}")
        return float(self._graph.nodes[entity_id].get("score", 0.0))

    def nodes(self) -> list[str]:
        """Return all entity keys present in the graph.

        Returns:
            Unordered list of all entity keys. Includes isolated nodes and
            target-only keys. Returns ``[]`` for an empty graph.
        """
        return list(self._graph.nodes())
