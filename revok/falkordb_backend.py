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

"""Causal graph backend using FalkorDB Lite (embedded) for weighted causal propagation.

Optional extra — requires ``falkordblite``:

    pip install 'revok[falkordb-lite]'

The Python import lives under ``redislite``, which is falkordblite's internal
package name.  Importing this module is always safe; the ImportError is deferred
to instantiation so OSS users who never install the extra are unaffected.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from revok.interfaces import GraphBackend, GraphReader
from revok.models import PropagationStep, PropagationTrace

if TYPE_CHECKING:
    from redislite.falkordb_client import FalkorDB as _FalkorDBType

logger = logging.getLogger(__name__)

try:
    from redislite.falkordb_client import FalkorDB as _FalkorDB

    _FALKORDB_LITE_AVAILABLE = True
except ImportError:
    _FALKORDB_LITE_AVAILABLE = False


class FalkorDBGraphBackend(GraphBackend, GraphReader):
    """Directed weighted causal graph backed by FalkorDB Lite.

    Nodes are ``Entity`` labelled; edges are ``CAUSES`` typed with a ``weight``
    property.  Propagation uses the same bounded BFS with max-pressure
    aggregation as :class:`~revok.causal_graph.CausalGraph`, with one Cypher
    query issued per hop to fetch all frontier edges in a single round-trip.

    Args:
        dbfilename: Path to the database file.  ``None`` (default) creates a
            temporary, process-local instance that is discarded on close.
        graph_name: Name of the graph within the FalkorDB instance.
            Defaults to ``"revok"``.
        db: Optional pre-existing ``FalkorDB`` connection to reuse.  When
            provided, ``close()`` will NOT shut down the shared connection.
            Intended for test isolation (Option B: shared server, unique graph
            names per test).
    """

    def __init__(
        self,
        dbfilename: str | None = None,
        graph_name: str = "revok",
        *,
        db: _FalkorDBType | None = None,
    ) -> None:
        if not _FALKORDB_LITE_AVAILABLE:
            raise ImportError(
                "FalkorDB Lite is not installed. "
                "Install it with: pip install 'revok[falkordb-lite]'"
            )
        if db is not None:
            self._db = db
            self._owns_db = False
        else:
            self._db = _FalkorDB(dbfilename)
            self._owns_db = True
        self._closed = False
        self._graph = self._db.select_graph(graph_name)

    # ------------------------------------------------------------------
    # GraphBackend interface — mutations
    # ------------------------------------------------------------------

    def add_entity(self, entity_id: str, score: float) -> None:
        """Add or update an entity node.

        If the node already exists its ``score`` property is updated in place.
        """
        self._graph.query(
            "MERGE (n:Entity {id: $id}) SET n.score = $score",
            params={"id": entity_id, "score": score},
        )
        logger.debug("Upserted entity node %r score=%.4f", entity_id, score)

    def add_relation(self, source_id: str, target_id: str, weight: float = 1.0) -> None:
        """Add a directed weighted relation edge.

        Both endpoints are created automatically with ``score=0.0`` if they do
        not yet exist, mirroring :meth:`~revok.causal_graph.CausalGraph.add_relation`.
        """
        if weight <= 0.0 or weight > 1.0:
            raise ValueError(f"relation weight must be in (0, 1], got {weight!r}")
        self._graph.query(
            "MERGE (a:Entity {id: $src}) ON CREATE SET a.score = 0.0 "
            "WITH a "
            "MERGE (b:Entity {id: $tgt}) ON CREATE SET b.score = 0.0 "
            "WITH a, b "
            "MERGE (a)-[r:CAUSES]->(b) SET r.weight = $weight",
            params={"src": source_id, "tgt": target_id, "weight": float(weight)},
        )
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
        """Propagate pressure from a root entity via bounded BFS.

        One Cypher query is issued per hop to retrieve all outgoing edges for
        the current frontier in a single round-trip; pressure math runs in
        Python identically to the NetworkX reference implementation.

        Returns:
            Mapping of downstream entity key to max propagated pressure.
            The root entity is excluded.
        """
        if max_hops < 0:
            raise ValueError("max_hops must be >= 0")
        if min_pressure < 0.0 or min_pressure > 1.0:
            raise ValueError("min_pressure must be in [0, 1]")
        if attenuation <= 0.0 or attenuation > 1.0:
            raise ValueError("attenuation must be in (0, 1]")
        if initial_pressure <= 0.0:
            return {}
        if not self.has_node(root_entity_id):
            return {}

        frontier: dict[str, float] = {root_entity_id: float(initial_pressure)}
        seen: set[str] = {root_entity_id}
        result: dict[str, float] = {}

        for _hop in range(1, max_hops + 1):
            next_frontier: dict[str, float] = {}
            hop_result = self._graph.query(
                "UNWIND $sources AS src_id "
                "MATCH (s:Entity {id: src_id})-[r:CAUSES]->(t:Entity) "
                "RETURN src_id, t.id, r.weight",
                params={"sources": list(frontier.keys())},
            )
            for row in hop_result.result_set:
                source_id, target_id, edge_w = row[0], row[1], float(row[2])
                if target_id in seen:
                    continue
                pressure = frontier[source_id] * edge_w * attenuation
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
        if not self.has_node(root_entity_id):
            return PropagationTrace(
                root_entity_key=root_entity_id,
                initial_pressure=root_pressure,
                steps=[],
                termination_reason="completed",
            )

        frontier: dict[str, float] = {root_entity_id: root_pressure}
        seen: set[str] = {root_entity_id}
        steps = [root_step]
        below_floor = False

        for depth in range(1, max_hops + 1):
            next_frontier: dict[str, tuple[float, str, float]] = {}
            saw_candidate = False
            for source_id, source_pressure in frontier.items():
                for target_id in self.successors(source_id):
                    if target_id in seen:
                        continue
                    saw_candidate = True
                    edge_weight = self.edge_weight(source_id, target_id)
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
            for target_id in self.successors(source_id):
                if target_id in seen:
                    continue
                pressure = source_pressure * self.edge_weight(source_id, target_id) * attenuation
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

    # ------------------------------------------------------------------
    # GraphReader interface — read-only structural introspection
    # ------------------------------------------------------------------

    def has_node(self, entity_id: str) -> bool:
        """Return ``True`` if the entity key exists in the graph."""
        result = self._graph.query(
            "MATCH (n:Entity {id: $id}) RETURN count(n)",
            params={"id": entity_id},
        )
        return result.result_set[0][0] > 0

    def successors(self, entity_id: str) -> list[str]:
        """Return direct successors, or ``[]`` for unknown nodes."""
        result = self._graph.query(
            "MATCH (n:Entity {id: $id})-[:CAUSES]->(m:Entity) RETURN m.id",
            params={"id": entity_id},
        )
        return [row[0] for row in result.result_set]

    def predecessors(self, entity_id: str) -> list[str]:
        """Return direct predecessors, or ``[]`` for unknown nodes."""
        result = self._graph.query(
            "MATCH (n:Entity {id: $id})<-[:CAUSES]-(m:Entity) RETURN m.id",
            params={"id": entity_id},
        )
        return [row[0] for row in result.result_set]

    def edge_weight(self, source_id: str, target_id: str) -> float:
        """Return edge weight.

        Raises:
            KeyError: If the edge does not exist.
        """
        result = self._graph.query(
            "MATCH (a:Entity {id: $src})-[r:CAUSES]->(b:Entity {id: $tgt}) "
            "RETURN r.weight",
            params={"src": source_id, "tgt": target_id},
        )
        if not result.result_set:
            raise KeyError(f"No edge from {source_id!r} to {target_id!r}")
        return float(result.result_set[0][0])

    def node_score(self, entity_id: str) -> float:
        """Return the last score recorded for an entity node.

        Raises:
            KeyError: If the node does not exist.
        """
        result = self._graph.query(
            "MATCH (n:Entity {id: $id}) RETURN n.score",
            params={"id": entity_id},
        )
        if not result.result_set:
            raise KeyError(f"No node {entity_id!r}")
        return float(result.result_set[0][0])

    def nodes(self) -> list[str]:
        """Return all entity keys present in the graph.

        Executes ``MATCH (n:Entity) RETURN n.id`` to retrieve all entity
        nodes, including isolated ones (no :CAUSES edges).

        Returns:
            Unordered list of all entity keys. Returns ``[]`` for an empty
            graph.
        """
        result = self._graph.query("MATCH (n:Entity) RETURN n.id")
        return [row[0] for row in result.result_set]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Shut down the embedded FalkorDB process.

        No-op when this instance was constructed with an injected ``db``
        (the caller owns the lifecycle in that case).
        """
        if self._closed:
            return
        self._closed = True
        if self._owns_db:
            self._db.close()
