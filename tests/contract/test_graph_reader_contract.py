# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Revok Contributors

"""Contract test suite for any GraphBackend implementation.

Any concrete backend can be validated by subclassing ``GraphReaderContract``
and implementing :meth:`make_backend` to return a fresh instance.

The suite covers:
- ``GraphReader`` read-only introspection (T015)
- ``GraphBackend`` propagation semantics (T015)
"""

from __future__ import annotations

import pytest

from revok.interfaces import GraphBackend


class GraphReaderContract:
    """Mixin that validates GraphReader + propagation semantics for any GraphBackend.

    Subclasses must implement :meth:`make_backend`.
    """

    def make_backend(self) -> GraphBackend:
        """Return a fresh, empty backend instance under test."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # GraphReader — unknown node / edge guard behaviour
    # ------------------------------------------------------------------

    def test_has_node_false_for_unknown(self) -> None:
        g = self.make_backend()
        assert g.has_node("ghost") is False

    def test_successors_empty_for_unknown(self) -> None:
        g = self.make_backend()
        assert g.successors("ghost") == []

    def test_predecessors_empty_for_unknown(self) -> None:
        g = self.make_backend()
        assert g.predecessors("ghost") == []

    def test_edge_weight_keyerror_for_missing_edge(self) -> None:
        g = self.make_backend()
        g.add_entity("a", 0.5)
        g.add_entity("b", 0.5)
        with pytest.raises(KeyError):
            g.edge_weight("a", "b")

    # ------------------------------------------------------------------
    # GraphReader — structural queries after mutations
    # ------------------------------------------------------------------

    def test_node_score_reflects_add_entity(self) -> None:
        g = self.make_backend()
        g.add_entity("alice", 0.75)
        assert g.node_score("alice") == pytest.approx(0.75)

    def test_successors_after_add_relation(self) -> None:
        g = self.make_backend()
        g.add_relation("a", "b", 0.8)
        assert g.successors("a") == ["b"]

    def test_predecessors_after_add_relation(self) -> None:
        g = self.make_backend()
        g.add_relation("a", "b", 0.8)
        assert g.predecessors("b") == ["a"]

    # ------------------------------------------------------------------
    # GraphBackend — propagation semantics
    # ------------------------------------------------------------------

    def test_propagate_single_hop(self) -> None:
        g = self.make_backend()
        g.add_relation("a", "b", weight=0.5)
        out = g.propagate("a", 1.0, max_hops=2, min_pressure=0.0, attenuation=1.0)
        assert "b" in out
        assert out["b"] == pytest.approx(0.5)

    def test_propagate_diamond_max_pressure(self) -> None:
        # A→B→D pressure = 0.9 * 0.5 = 0.45
        # A→C→D pressure = 0.5 * 1.0 = 0.50  ← max wins
        g = self.make_backend()
        g.add_relation("a", "b", weight=0.9)
        g.add_relation("a", "c", weight=0.5)
        g.add_relation("b", "d", weight=0.5)
        g.add_relation("c", "d", weight=1.0)
        out = g.propagate("a", 1.0, max_hops=3, min_pressure=0.0, attenuation=1.0)
        assert out["d"] == pytest.approx(0.5)

    def test_propagate_cycle_terminates(self) -> None:
        g = self.make_backend()
        g.add_relation("a", "b", weight=0.8)
        g.add_relation("b", "c", weight=0.8)
        g.add_relation("c", "a", weight=0.8)
        out = g.propagate("a", 1.0, max_hops=10, min_pressure=0.0, attenuation=1.0)
        assert "a" not in out
        assert "b" in out
        assert "c" in out
