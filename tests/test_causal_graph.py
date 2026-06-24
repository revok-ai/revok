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

"""Tests for revok.causal_graph.CausalGraph scaffold (T037)."""

from __future__ import annotations

import pytest

from revok.causal_graph import CausalGraph


class TestAddEntity:
    def test_new_entity_is_added_as_node(self):
        g = CausalGraph()
        g.add_entity("alice", 0.5)
        assert g.node_count == 1

    def test_node_stores_score(self):
        g = CausalGraph()
        g.add_entity("alice", 0.42)
        assert g._graph.nodes["alice"]["score"] == pytest.approx(0.42)

    def test_update_existing_entity_changes_score(self):
        g = CausalGraph()
        g.add_entity("alice", 0.3)
        g.add_entity("alice", 0.9)
        # Still one node, score updated
        assert g.node_count == 1
        assert g._graph.nodes["alice"]["score"] == pytest.approx(0.9)

    def test_multiple_entities_each_become_a_node(self):
        g = CausalGraph()
        g.add_entity("alice", 0.3)
        g.add_entity("bob", 0.5)
        g.add_entity("carol", 0.1)
        assert g.node_count == 3


class TestAddRelation:
    def test_relation_creates_directed_edge(self):
        g = CausalGraph()
        g.add_entity("alice", 0.5)
        g.add_entity("bob", 0.3)
        g.add_relation("alice", "bob")
        assert g.edge_count == 1
        assert g._graph.has_edge("alice", "bob")

    def test_relation_is_directed_not_undirected(self):
        g = CausalGraph()
        g.add_relation("alice", "bob")
        assert g._graph.has_edge("alice", "bob")
        assert not g._graph.has_edge("bob", "alice")

    def test_relation_auto_creates_missing_nodes(self):
        g = CausalGraph()
        g.add_relation("alice", "bob")
        assert g.node_count == 2
        assert g._graph.nodes["alice"]["score"] == pytest.approx(0.0)
        assert g._graph.nodes["bob"]["score"] == pytest.approx(0.0)

    def test_relation_preserves_existing_node_scores(self):
        g = CausalGraph()
        g.add_entity("alice", 0.8)
        g.add_relation("alice", "bob")
        # alice score must not be clobbered
        assert g._graph.nodes["alice"]["score"] == pytest.approx(0.8)

    def test_relation_stores_weight(self):
        g = CausalGraph()
        g.add_relation("alice", "bob", weight=0.6)
        assert g._graph["alice"]["bob"]["weight"] == pytest.approx(0.6)


class TestCounts:
    def test_empty_graph_has_zero_nodes_and_edges(self):
        g = CausalGraph()
        assert g.node_count == 0
        assert g.edge_count == 0

    def test_node_count_is_int(self):
        g = CausalGraph()
        g.add_entity("x", 0.1)
        assert isinstance(g.node_count, int)

    def test_edge_count_is_int(self):
        g = CausalGraph()
        g.add_relation("x", "y")
        assert isinstance(g.edge_count, int)

    def test_counts_reflect_multiple_relations(self):
        g = CausalGraph()
        g.add_relation("a", "b")
        g.add_relation("b", "c")
        g.add_relation("a", "c")
        assert g.node_count == 3
        assert g.edge_count == 3


class TestPropagate:
    def test_linear_chain_pressure_attenuation(self):
        g = CausalGraph()
        g.add_relation("a", "b", weight=0.5)
        g.add_relation("b", "c", weight=0.5)

        out = g.propagate(
            "a",
            1.0,
            max_hops=3,
            min_pressure=0.0,
            attenuation=1.0,
        )
        assert out["b"] == pytest.approx(0.5)
        assert out["c"] == pytest.approx(0.25)

    def test_diamond_topology_max_path_wins_not_sum(self):
        g = CausalGraph()
        g.add_relation("a", "b", weight=0.9)
        g.add_relation("a", "c", weight=0.5)
        g.add_relation("b", "d", weight=0.5)
        g.add_relation("c", "d", weight=1.0)

        out = g.propagate(
            "a",
            1.0,
            max_hops=3,
            min_pressure=0.0,
            attenuation=1.0,
        )
        # d gets max(0.9*0.5, 0.5*1.0) = 0.5
        assert out["d"] == pytest.approx(0.5)

    def test_cyclic_graph_terminates(self):
        g = CausalGraph()
        g.add_relation("a", "b", weight=0.8)
        g.add_relation("b", "c", weight=0.8)
        g.add_relation("c", "a", weight=0.8)

        out = g.propagate(
            "a",
            1.0,
            max_hops=10,
            min_pressure=0.0,
            attenuation=1.0,
        )
        assert "a" not in out
        assert out["b"] == pytest.approx(0.8)
        assert out["c"] == pytest.approx(0.64)

    def test_hop_limit_cutoff(self):
        g = CausalGraph()
        g.add_relation("a", "b", weight=1.0)
        g.add_relation("b", "c", weight=1.0)
        g.add_relation("c", "d", weight=1.0)

        out = g.propagate(
            "a",
            1.0,
            max_hops=2,
            min_pressure=0.0,
            attenuation=1.0,
        )
        assert "b" in out and "c" in out
        assert "d" not in out

    def test_min_pressure_cutoff(self):
        g = CausalGraph()
        g.add_relation("a", "b", weight=0.5)
        g.add_relation("b", "c", weight=0.5)
        out = g.propagate(
            "a",
            1.0,
            max_hops=3,
            min_pressure=0.3,
            attenuation=1.0,
        )
        assert "b" in out
        assert "c" not in out

    def test_no_outgoing_edges_returns_empty(self):
        g = CausalGraph()
        g.add_entity("a", 1.0)
        out = g.propagate(
            "a",
            1.0,
            max_hops=2,
            min_pressure=0.0,
            attenuation=1.0,
        )
        assert out == {}

    def test_unknown_root_returns_empty(self):
        g = CausalGraph()
        g.add_relation("a", "b", weight=1.0)
        out = g.propagate(
            "x",
            1.0,
            max_hops=2,
            min_pressure=0.0,
            attenuation=1.0,
        )
        assert out == {}


# ---------------------------------------------------------------------------
# T004: GraphReader protocol compliance tests
# ---------------------------------------------------------------------------


class TestGraphReaderProtocol:
    def test_has_node_true_for_added_entity(self):
        g = CausalGraph()
        g.add_entity("alice", 0.5)
        assert g.has_node("alice") is True

    def test_has_node_false_for_unknown(self):
        g = CausalGraph()
        assert g.has_node("unknown") is False

    def test_successors_empty_for_unknown(self):
        g = CausalGraph()
        assert g.successors("unknown") == []

    def test_predecessors_empty_for_unknown(self):
        g = CausalGraph()
        assert g.predecessors("unknown") == []

    def test_successors_after_add_relation(self):
        g = CausalGraph()
        g.add_relation("a", "b", weight=0.8)
        assert g.successors("a") == ["b"]
        assert g.predecessors("b") == ["a"]

    def test_predecessors_after_add_relation(self):
        g = CausalGraph()
        g.add_relation("a", "b", weight=0.8)
        assert g.predecessors("a") == []
        assert g.predecessors("b") == ["a"]

    def test_edge_weight_returns_correct_value(self):
        g = CausalGraph()
        g.add_relation("a", "b", weight=0.6)
        assert g.edge_weight("a", "b") == pytest.approx(0.6)

    def test_edge_weight_keyerror_for_missing_edge(self):
        g = CausalGraph()
        g.add_entity("a", 0.5)
        g.add_entity("b", 0.5)
        with pytest.raises(KeyError):
            g.edge_weight("a", "b")

    def test_node_score_reflects_add_entity(self):
        g = CausalGraph()
        g.add_entity("alice", 0.75)
        assert g.node_score("alice") == pytest.approx(0.75)

    def test_node_score_keyerror_for_missing_node(self):
        g = CausalGraph()
        with pytest.raises(KeyError):
            g.node_score("ghost")

    def test_successors_excludes_predecessors(self):
        g = CausalGraph()
        g.add_relation("a", "b", weight=0.5)
        g.add_relation("c", "b", weight=0.3)
        assert set(g.successors("b")) == set()
        assert set(g.predecessors("b")) == {"a", "c"}
