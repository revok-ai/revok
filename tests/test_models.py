# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Revok Contributors
#
# Tests for revok.models — data integrity and method correctness.

from __future__ import annotations


import pytest

from revok.models import (
    EnrichedPayload,
    Entity,
    EntityRecord,
    MemoryAdapterResponse,
    Signal,
    GraphNodeView,
    GraphEdgeView,
    GraphTopologyResponse,
)


class TestSignal:
    def test_frozen_immutability(self):
        sig = Signal(
            raw_content="Alice Smith visited today.",
            source_id="agent-1",
            timestamp=1000.0,
            http_method="POST",
            http_path="/v1/memories",
            original_body=b"{}",
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises((AttributeError, TypeError)):
            sig.raw_content = "changed"  # type: ignore[misc]

    def test_fields_preserved(self):
        sig = Signal(
            raw_content="hello",
            source_id="s1",
            timestamp=9999.0,
            http_method="POST",
            http_path="/v1/memories",
            original_body=b"data",
            headers={"X-Agent": "test"},
        )
        assert sig.raw_content == "hello"
        assert sig.source_id == "s1"
        assert sig.timestamp == 9999.0
        assert sig.http_method == "POST"
        assert sig.http_path == "/v1/memories"
        assert sig.original_body == b"data"
        assert sig.headers == {"X-Agent": "test"}


class TestEntity:
    def test_normalize_lowercases_and_strips(self):
        assert Entity.normalize("  Alice Smith  ") == "alice smith"
        assert Entity.normalize("ALICE") == "alice"
        assert Entity.normalize("bob") == "bob"

    def test_entity_key_is_normalized(self):
        e = Entity(
            key=Entity.normalize("Alice Smith"),
            raw_text="Alice Smith",
            pattern_name="person",
        )
        assert e.key == "alice smith"

    def test_frozen_immutability(self):
        e = Entity(key="x", raw_text="X", pattern_name="p")
        with pytest.raises((AttributeError, TypeError)):
            e.key = "y"  # type: ignore[misc]


class TestEntityRecord:
    def test_mutable(self):
        rec = EntityRecord(
            entity_key="alice smith",
            score=0.5,
            valid_time=1000.0,
            transaction_time=1000.0,
            signal_count=2,
            pattern_name="person",
        )
        rec.score = 0.8
        assert rec.score == 0.8

    def test_fields_set_correctly(self):
        rec = EntityRecord(
            entity_key="bob jones",
            score=0.3,
            valid_time=2000.0,
            transaction_time=2000.0,
            signal_count=1,
            pattern_name="person",
        )
        assert rec.entity_key == "bob jones"
        assert rec.score == 0.3
        assert rec.last_seen == 2000.0
        assert rec.signal_count == 1
        assert rec.pattern_name == "person"

    def test_entity_record_contradiction_defaults(self):
        rec = EntityRecord(
            entity_key="x",
            score=0.5,
            valid_time=1.0,
            transaction_time=1.0,
            signal_count=1,
            pattern_name="p",
        )
        assert rec.contradiction_count == 0
        assert rec.last_contradiction_time is None
        assert rec.last_value_fingerprint is None

    def test_entity_record_contradiction_fields_settable(self):
        rec = EntityRecord(
            entity_key="x",
            score=0.5,
            valid_time=1.0,
            transaction_time=1.0,
            signal_count=1,
            pattern_name="p",
            contradiction_count=3,
            last_contradiction_time=999.0,
            last_value_fingerprint="500.0",
        )
        assert rec.contradiction_count == 3
        assert rec.last_contradiction_time == 999.0
        assert rec.last_value_fingerprint == "500.0"


class TestEnrichedPayload:
    def _make_record(
        self, key: str = "alice smith", score: float = 0.87
    ) -> EntityRecord:
        return EntityRecord(
            entity_key=key,
            score=score,
            valid_time=1748476740.0,
            transaction_time=1748476740.0,
            signal_count=3,
            pattern_name="person",
        )

    def test_to_upstream_dict_merges_x_revok(self):
        payload = EnrichedPayload(
            original_body={"user_id": "u1", "content": "Alice Smith visited."},
            entities=[self._make_record()],
            revok_version="0.1.0",
            processed_at=1748476800.0,
        )
        result = payload.to_upstream_dict()

        assert result["user_id"] == "u1"
        assert result["content"] == "Alice Smith visited."
        assert "x_revok" in result
        x = result["x_revok"]
        assert x["version"] == "0.1.0"
        assert len(x["entities"]) == 1
        entity = x["entities"][0]
        assert entity["id"] == "alice smith"
        assert entity["score"] == 0.87
        assert entity["signal_count"] == 3
        assert entity["pattern_name"] == "person"
        assert "last_seen" in entity
        assert "processed_at" in x

    def test_to_upstream_dict_empty_entities(self):
        payload = EnrichedPayload(
            original_body={"content": "no entities here"},
            entities=[],
            revok_version="0.1.0",
            processed_at=1748476800.0,
        )
        result = payload.to_upstream_dict()
        assert result["x_revok"]["entities"] == []

    def test_original_body_not_mutated(self):
        orig = {"key": "value"}
        payload = EnrichedPayload(
            original_body=orig,
            entities=[],
            revok_version="0.1.0",
            processed_at=1748476800.0,
        )
        payload.to_upstream_dict()
        assert orig == {"key": "value"}


class TestMemoryAdapterResponse:
    def test_is_error_true_for_4xx(self):
        resp = MemoryAdapterResponse(
            status=404, body=b"not found", headers={}, is_error=True
        )
        assert resp.is_error is True

    def test_is_error_false_for_2xx(self):
        resp = MemoryAdapterResponse(status=200, body=b"ok", headers={}, is_error=False)
        assert resp.is_error is False


# ---------------------------------------------------------------------------
# Feature 007: GraphNodeView, GraphEdgeView, GraphTopologyResponse
# ---------------------------------------------------------------------------


class TestGraphNodeView:
    def test_graph_node_view_construction(self):
        node = GraphNodeView(key="alice", score=0.75)
        assert node.key == "alice"
        assert node.score == pytest.approx(0.75)

    def test_graph_node_view_frozen(self):
        node = GraphNodeView(key="alice", score=0.75)
        with pytest.raises((AttributeError, TypeError)):
            node.key = "bob"  # type: ignore[misc]


class TestGraphEdgeView:
    def test_graph_edge_view_construction(self):
        edge = GraphEdgeView(source="a", target="b", weight=0.5)
        assert edge.source == "a"
        assert edge.target == "b"
        assert edge.weight == pytest.approx(0.5)

    def test_graph_edge_view_frozen(self):
        edge = GraphEdgeView(source="a", target="b", weight=0.5)
        with pytest.raises((AttributeError, TypeError)):
            edge.source = "x"  # type: ignore[misc]


class TestGraphTopologyResponse:
    def test_graph_topology_response_construction(self):
        nodes = [GraphNodeView(key="a", score=1.0), GraphNodeView(key="b", score=0.5)]
        edges = [GraphEdgeView(source="a", target="b", weight=0.8)]
        resp = GraphTopologyResponse(nodes=nodes, edges=edges)
        assert len(resp.nodes) == 2
        assert len(resp.edges) == 1
        assert resp.nodes[0].key == "a"
        assert resp.edges[0].source == "a"
