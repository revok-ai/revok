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

"""RevokInspector — read-only explainability API over entity state and causal graph."""

from __future__ import annotations

import time
from collections import deque

from revok.interfaces import GraphReader, SignalHistoryStore, StateStore
from revok.models import (
    CausalNeighbor,
    DownstreamEntity,
    InspectionReport,
    PropagationPath,
    SignalRecord,
)


class EntityNotFoundError(Exception):
    """Raised by get_signals when the entity does not exist in the store."""

    def __init__(self, entity_key: str) -> None:
        super().__init__(entity_key)
        self.entity_key = entity_key


class RevokInspector:
    """Composes StateStore + GraphReader + SignalHistoryStore for read-only explainability.

    All methods are async to match the Inspector Protocol and allow future async
    graph backends without API changes.
    """

    def __init__(
        self,
        store: StateStore,
        graph: GraphReader,
        history: SignalHistoryStore | None,
    ) -> None:
        """Initialise the inspector.

        Args:
            store: Persistent entity state store (async).
            graph: Read-only causal graph (sync).
            history: Optional signal history store; pass ``None`` to disable.
        """
        self._store = store
        self._graph = graph
        self._history = history

    async def inspect_entity(self, entity_key: str) -> InspectionReport | None:
        """Return a snapshot of an entity's state and direct causal neighbors.

        Fetches the entity record from the store, then queries the graph for direct
        predecessors (upstream) and successors (downstream). Neighbor scores are
        fetched from the store; ``None`` is used when a neighbor has no store record.

        Args:
            entity_key: Normalized entity identifier.

        Returns:
            ``InspectionReport`` if the entity exists in the store, ``None`` otherwise.
        """
        record = await self._store.get(entity_key)
        if record is None:
            return None

        upstream: list[CausalNeighbor] = []
        downstream: list[CausalNeighbor] = []

        if self._graph.has_node(entity_key):
            for pred in self._graph.predecessors(entity_key):
                try:
                    weight = self._graph.edge_weight(pred, entity_key)
                except KeyError:
                    weight = 1.0
                neighbor_record = await self._store.get(pred)
                upstream.append(
                    CausalNeighbor(
                        entity_key=pred,
                        direction="upstream",
                        weight=weight,
                        score=neighbor_record.score if neighbor_record is not None else None,
                    )
                )

            for succ in self._graph.successors(entity_key):
                try:
                    weight = self._graph.edge_weight(entity_key, succ)
                except KeyError:
                    weight = 1.0
                neighbor_record = await self._store.get(succ)
                downstream.append(
                    CausalNeighbor(
                        entity_key=succ,
                        direction="downstream",
                        weight=weight,
                        score=neighbor_record.score if neighbor_record is not None else None,
                    )
                )

        return InspectionReport(
            entity_key=entity_key,
            score=record.score,
            valid_time=record.valid_time,
            transaction_time=record.transaction_time,
            signal_count=record.signal_count,
            contradiction_count=record.contradiction_count,
            upstream=upstream,
            downstream=downstream,
            inspected_at=time.time(),
        )

    async def get_downstream(
        self,
        entity_key: str,
        *,
        max_hops: int,
        min_pressure: float,
        attenuation: float,
    ) -> list[DownstreamEntity]:
        """Return all entities reachable from *entity_key* via forward propagation.

        Uses forward BFS with max-pressure aggregation. Excludes *entity_key* itself.
        Returns ``[]`` if the entity has no graph node.

        Args:
            entity_key: Root entity identifier.
            max_hops: Maximum BFS depth.
            min_pressure: Minimum pressure threshold; nodes below this are excluded.
            attenuation: Per-hop pressure multiplier in ``(0, 1]``.

        Returns:
            List of ``DownstreamEntity`` sorted by descending pressure.
        """
        if not self._graph.has_node(entity_key):
            return []

        # frontier maps entity_key -> (pressure, hops)
        frontier: dict[str, tuple[float, int]] = {entity_key: (1.0, 0)}
        visited: set[str] = {entity_key}
        # result maps entity_key -> DownstreamEntity (best pressure seen so far)
        result: dict[str, DownstreamEntity] = {}

        for _hop in range(max_hops):
            next_frontier: dict[str, tuple[float, int]] = {}
            for source, (src_pressure, src_hops) in frontier.items():
                for target in self._graph.successors(source):
                    if target in visited:
                        continue
                    try:
                        w = self._graph.edge_weight(source, target)
                    except KeyError:
                        w = 1.0
                    p = src_pressure * w * attenuation
                    if p < min_pressure:
                        continue
                    existing = result.get(target)
                    new_hops = src_hops + 1
                    if existing is None or p > existing.pressure:
                        result[target] = DownstreamEntity(
                            entity_key=target, pressure=p, hops=new_hops
                        )
                    prev_p = next_frontier.get(target, (0.0, 0))[0]
                    if p > prev_p:
                        next_frontier[target] = (p, new_hops)

            if not next_frontier:
                break

            visited.update(next_frontier.keys())
            frontier = next_frontier

        return sorted(result.values(), key=lambda e: -e.pressure)

    async def get_paths(
        self,
        entity_key: str,
        *,
        max_hops: int,
        min_pressure: float,
        attenuation: float,
        max_paths: int,
    ) -> list[PropagationPath]:
        """Return propagation paths from upstream roots to *entity_key*.

        Uses backward BFS to discover ancestor paths, then computes pressures
        forward along each discovered path. Caps results at *max_paths*.
        Sets ``is_dominant=True`` on the path with the highest terminal pressure.

        Args:
            entity_key: Target entity identifier.
            max_hops: Maximum backward BFS depth.
            min_pressure: Minimum pressure threshold for path inclusion.
            attenuation: Per-hop pressure multiplier in ``(0, 1]``.
            max_paths: Maximum number of paths to return.

        Returns:
            List of ``PropagationPath`` with ``is_dominant`` set on the highest-pressure path.
        """
        if not self._graph.has_node(entity_key):
            return []

        # Phase 1: backward BFS — discover simple paths from roots to entity_key
        # Each queue entry: (current_node, path_so_far_as_list, visited_set)
        raw_paths: list[list[str]] = []
        queue: deque[tuple[str, list[str], set[str]]] = deque()
        queue.append((entity_key, [entity_key], {entity_key}))

        while queue and len(raw_paths) < max_paths:
            current, path, path_visited = queue.popleft()

            preds = self._graph.predecessors(current)
            if not preds:
                # current is a root (no predecessors) — record path in root-first order
                # only if the path contains at least one upstream hop (length > 1)
                if len(path) > 1:
                    raw_paths.append(list(reversed(path)))
                if len(raw_paths) >= max_paths:
                    break
                continue

            if len(path) > max_hops + 1:
                # Reached depth limit without finding a root — do not record
                continue

            for pred in preds:
                if pred in path_visited:
                    continue  # cycle safety
                queue.append((pred, path + [pred], path_visited | {pred}))

        if not raw_paths:
            return []

        # Phase 2: compute pressures forward along each discovered path
        built: list[tuple[list[str], list[float]]] = []
        for raw_path in raw_paths:
            pressures: list[float] = [1.0]
            p = 1.0
            viable = True
            for i in range(1, len(raw_path)):
                prev = raw_path[i - 1]
                curr = raw_path[i]
                try:
                    w = self._graph.edge_weight(prev, curr)
                except KeyError:
                    w = 1.0
                p = p * w * attenuation
                pressures.append(p)
            terminal = pressures[-1]
            if terminal < min_pressure:
                viable = False
            if viable:
                built.append((raw_path, pressures))

        if not built:
            return []

        # Phase 3: mark dominant path on the returned subset
        max_terminal = max(pressures[-1] for _, pressures in built)
        result: list[PropagationPath] = []
        for hops, pressures in built:
            result.append(
                PropagationPath(
                    hops=hops,
                    pressures=pressures,
                    is_dominant=(pressures[-1] == max_terminal),
                )
            )
        return result

    async def get_signals(self, entity_key: str) -> list[SignalRecord] | None:
        """Return the signal history for *entity_key*, or ``None`` if history is disabled.

        Args:
            entity_key: Normalized entity identifier.

        Returns:
            List of ``SignalRecord`` if history is enabled, ``None`` if disabled.

        Raises:
            EntityNotFoundError: If the entity does not exist in the store.
        """
        entity = await self._store.get(entity_key)
        if entity is None:
            raise EntityNotFoundError(entity_key)
        if self._history is None:
            return None
        return await self._history.get_for_entity(entity_key)
