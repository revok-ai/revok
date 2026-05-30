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
