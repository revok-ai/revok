# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Revok Contributors
#
# Tests for revok.inspector.RevokInspector.

from __future__ import annotations

import pytest
import pytest_asyncio

from revok.causal_graph import CausalGraph
from revok.inspector import EntityNotFoundError, RevokInspector
from revok.models import EntityRecord


# ---------------------------------------------------------------------------
# Helpers — minimal in-memory StateStore stub
# ---------------------------------------------------------------------------


class _MemoryStore:
    """Minimal in-memory StateStore for testing."""

    def __init__(self) -> None:
        self._data: dict[str, EntityRecord] = {}

    async def get(self, entity_key: str) -> EntityRecord | None:
        return self._data.get(entity_key)

    async def put(self, record: EntityRecord) -> None:
        self._data[record.entity_key] = record

    async def list_all(self, offset: int = 0, limit: int = 100) -> list[EntityRecord]:
        items = sorted(self._data.values(), key=lambda r: r.entity_key)
        return items[offset : offset + limit]

    async def delete(self, entity_key: str) -> bool:
        return self._data.pop(entity_key, None) is not None

    async def close(self) -> None:
        pass


def _record(key: str, score: float = 0.5) -> EntityRecord:
    return EntityRecord(
        entity_key=key,
        score=score,
        valid_time=1_000_000.0,
        transaction_time=1_000_001.0,
        signal_count=1,
        pattern_name="test",
    )


# ---------------------------------------------------------------------------
# T006: inspect_entity
# ---------------------------------------------------------------------------


class TestInspectEntity:
    @pytest.mark.asyncio
    async def test_entity_not_in_store_returns_none(self) -> None:
        store = _MemoryStore()
        graph = CausalGraph()
        inspector = RevokInspector(store, graph, None)
        result = await inspector.inspect_entity("ghost")
        assert result is None

    @pytest.mark.asyncio
    async def test_entity_in_store_no_graph_node_returns_empty_neighbors(self) -> None:
        store = _MemoryStore()
        await store.put(_record("alice", 0.7))
        graph = CausalGraph()
        inspector = RevokInspector(store, graph, None)
        result = await inspector.inspect_entity("alice")
        assert result is not None
        assert result.entity_key == "alice"
        assert result.score == pytest.approx(0.7)
        assert result.upstream == []
        assert result.downstream == []

    @pytest.mark.asyncio
    async def test_entity_in_store_and_graph_with_neighbors(self) -> None:
        store = _MemoryStore()
        await store.put(_record("alice", 0.8))
        await store.put(_record("bob", 0.6))
        graph = CausalGraph()
        graph.add_relation("alice", "bob", weight=0.9)
        graph.add_entity("alice", 0.8)
        graph.add_entity("bob", 0.6)
        inspector = RevokInspector(store, graph, None)

        # Inspect alice (upstream has no predecessors; downstream is bob)
        result_alice = await inspector.inspect_entity("alice")
        assert result_alice is not None
        assert result_alice.upstream == []
        assert len(result_alice.downstream) == 1
        d = result_alice.downstream[0]
        assert d.entity_key == "bob"
        assert d.direction == "downstream"
        assert d.weight == pytest.approx(0.9)
        assert d.score == pytest.approx(0.6)

        # Inspect bob (upstream is alice; downstream is empty)
        result_bob = await inspector.inspect_entity("bob")
        assert result_bob is not None
        assert len(result_bob.upstream) == 1
        u = result_bob.upstream[0]
        assert u.entity_key == "alice"
        assert u.direction == "upstream"
        assert u.weight == pytest.approx(0.9)
        assert u.score == pytest.approx(0.8)
        assert result_bob.downstream == []

    @pytest.mark.asyncio
    async def test_neighbor_in_graph_but_not_store_score_is_none(self) -> None:
        store = _MemoryStore()
        await store.put(_record("alice", 0.5))
        # bob is in graph but NOT in store
        graph = CausalGraph()
        graph.add_relation("alice", "bob", weight=0.7)
        graph.add_entity("alice", 0.5)
        inspector = RevokInspector(store, graph, None)
        result = await inspector.inspect_entity("alice")
        assert result is not None
        assert len(result.downstream) == 1
        assert result.downstream[0].score is None

    @pytest.mark.asyncio
    async def test_inspected_at_is_recent(self) -> None:
        import time

        store = _MemoryStore()
        await store.put(_record("alice", 0.5))
        graph = CausalGraph()
        inspector = RevokInspector(store, graph, None)
        before = time.time()
        result = await inspector.inspect_entity("alice")
        after = time.time()
        assert result is not None
        assert before <= result.inspected_at <= after


# ---------------------------------------------------------------------------
# T008: get_downstream
# ---------------------------------------------------------------------------


class TestGetDownstream:
    _defaults = dict(max_hops=5, min_pressure=0.01, attenuation=0.8)

    @pytest.mark.asyncio
    async def test_no_graph_node_returns_empty(self) -> None:
        store = _MemoryStore()
        graph = CausalGraph()
        inspector = RevokInspector(store, graph, None)
        result = await inspector.get_downstream("a", **self._defaults)
        assert result == []

    @pytest.mark.asyncio
    async def test_linear_chain(self) -> None:
        store = _MemoryStore()
        graph = CausalGraph()
        graph.add_relation("a", "b", weight=1.0)
        graph.add_relation("b", "c", weight=1.0)
        inspector = RevokInspector(store, graph, None)
        result = await inspector.get_downstream("a", **self._defaults)
        keys = [e.entity_key for e in result]
        assert "b" in keys
        assert "c" in keys
        # B should be at pressure 0.8, C at 0.64
        b_entity = next(e for e in result if e.entity_key == "b")
        c_entity = next(e for e in result if e.entity_key == "c")
        assert b_entity.pressure == pytest.approx(0.8)
        assert c_entity.pressure == pytest.approx(0.64)

    @pytest.mark.asyncio
    async def test_diamond_graph_d_appears_once_with_max_pressure(self) -> None:
        # A->B->D, A->C->D — D should appear with max of the two paths
        store = _MemoryStore()
        graph = CausalGraph()
        graph.add_relation("a", "b", weight=1.0)
        graph.add_relation("a", "c", weight=0.5)
        graph.add_relation("b", "d", weight=1.0)
        graph.add_relation("c", "d", weight=1.0)
        inspector = RevokInspector(store, graph, None)
        result = await inspector.get_downstream("a", max_hops=3, min_pressure=0.01, attenuation=1.0)
        keys = [e.entity_key for e in result]
        assert keys.count("d") == 1
        d_entity = next(e for e in result if e.entity_key == "d")
        # Via A->B->D: 1.0 * 1.0 * 1.0 = 1.0; via A->C->D: 0.5 * 1.0 * 1.0 = 0.5
        # Max-pressure = 1.0
        assert d_entity.pressure == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_cycle_graph_terminates(self) -> None:
        store = _MemoryStore()
        graph = CausalGraph()
        graph.add_relation("a", "b", weight=1.0)
        graph.add_relation("b", "c", weight=1.0)
        graph.add_relation("c", "a", weight=1.0)  # cycle back to a
        inspector = RevokInspector(store, graph, None)
        result = await inspector.get_downstream("a", max_hops=10, min_pressure=0.01, attenuation=0.9)
        keys = [e.entity_key for e in result]
        assert "b" in keys
        assert "c" in keys
        assert "a" not in keys  # root excluded

    @pytest.mark.asyncio
    async def test_max_hops_1_returns_only_direct_neighbors(self) -> None:
        store = _MemoryStore()
        graph = CausalGraph()
        graph.add_relation("a", "b", weight=1.0)
        graph.add_relation("b", "c", weight=1.0)
        inspector = RevokInspector(store, graph, None)
        result = await inspector.get_downstream("a", max_hops=1, min_pressure=0.01, attenuation=0.8)
        keys = [e.entity_key for e in result]
        assert "b" in keys
        assert "c" not in keys

    @pytest.mark.asyncio
    async def test_pressure_threshold_excludes_weak_nodes(self) -> None:
        store = _MemoryStore()
        graph = CausalGraph()
        graph.add_relation("a", "b", weight=0.01)  # pressure = 0.01 * 0.8 = 0.008 < 0.05
        inspector = RevokInspector(store, graph, None)
        result = await inspector.get_downstream("a", max_hops=3, min_pressure=0.05, attenuation=0.8)
        keys = [e.entity_key for e in result]
        assert "b" not in keys

    @pytest.mark.asyncio
    async def test_sorted_by_descending_pressure(self) -> None:
        store = _MemoryStore()
        graph = CausalGraph()
        graph.add_relation("a", "b", weight=1.0)
        graph.add_relation("a", "c", weight=0.5)
        inspector = RevokInspector(store, graph, None)
        result = await inspector.get_downstream("a", max_hops=2, min_pressure=0.01, attenuation=1.0)
        pressures = [e.pressure for e in result]
        assert pressures == sorted(pressures, reverse=True)


# ---------------------------------------------------------------------------
# T009: get_paths
# ---------------------------------------------------------------------------


class TestGetPaths:
    _defaults = dict(max_hops=5, min_pressure=0.001, attenuation=0.8, max_paths=100)

    @pytest.mark.asyncio
    async def test_entity_not_in_graph_returns_empty(self) -> None:
        store = _MemoryStore()
        graph = CausalGraph()
        inspector = RevokInspector(store, graph, None)
        result = await inspector.get_paths("z", **self._defaults)
        assert result == []

    @pytest.mark.asyncio
    async def test_single_path_is_dominant(self) -> None:
        store = _MemoryStore()
        graph = CausalGraph()
        graph.add_relation("a", "b", weight=1.0)
        graph.add_relation("b", "c", weight=1.0)
        inspector = RevokInspector(store, graph, None)
        result = await inspector.get_paths("c", **self._defaults)
        assert len(result) == 1
        path = result[0]
        assert path.hops == ["a", "b", "c"]
        assert path.is_dominant is True
        assert path.pressures[0] == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_diamond_two_paths_dominant_is_higher_pressure(self) -> None:
        # A->B->D, A->C->D with different weights
        store = _MemoryStore()
        graph = CausalGraph()
        graph.add_relation("a", "b", weight=1.0)
        graph.add_relation("a", "c", weight=0.5)
        graph.add_relation("b", "d", weight=1.0)
        graph.add_relation("c", "d", weight=1.0)
        inspector = RevokInspector(store, graph, None)
        result = await inspector.get_paths("d", max_hops=5, min_pressure=0.001, attenuation=1.0, max_paths=100)
        assert len(result) == 2
        dominant = [p for p in result if p.is_dominant]
        non_dominant = [p for p in result if not p.is_dominant]
        assert len(dominant) == 1
        # path via B has higher pressure (1.0 * 1.0 > 0.5 * 1.0)
        assert dominant[0].hops == ["a", "b", "d"]
        assert len(non_dominant) == 1
        assert non_dominant[0].hops == ["a", "c", "d"]

    @pytest.mark.asyncio
    async def test_no_upstream_sources_returns_empty(self) -> None:
        store = _MemoryStore()
        graph = CausalGraph()
        graph.add_entity("standalone", 0.5)
        inspector = RevokInspector(store, graph, None)
        result = await inspector.get_paths("standalone", **self._defaults)
        assert result == []

    @pytest.mark.asyncio
    async def test_cycle_does_not_appear_in_path(self) -> None:
        store = _MemoryStore()
        graph = CausalGraph()
        graph.add_relation("a", "b", weight=1.0)
        graph.add_relation("b", "a", weight=1.0)  # cycle
        inspector = RevokInspector(store, graph, None)
        # a has predecessor b which has predecessor a — cycle should be skipped
        result = await inspector.get_paths("b", **self._defaults)
        # a has no predecessors other than b (cycle) — so no valid root path
        assert result == []

    @pytest.mark.asyncio
    async def test_max_paths_1_on_diamond_returns_exactly_one_path(self) -> None:
        store = _MemoryStore()
        graph = CausalGraph()
        graph.add_relation("a", "b", weight=1.0)
        graph.add_relation("a", "c", weight=1.0)
        graph.add_relation("b", "d", weight=1.0)
        graph.add_relation("c", "d", weight=1.0)
        inspector = RevokInspector(store, graph, None)
        result = await inspector.get_paths("d", max_hops=5, min_pressure=0.001, attenuation=1.0, max_paths=1)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_max_paths_2_on_5_path_graph_returns_exactly_2(self) -> None:
        # Build 5 independent paths: r1->t, r2->t, r3->t, r4->t, r5->t
        store = _MemoryStore()
        graph = CausalGraph()
        for i in range(1, 6):
            graph.add_relation(f"r{i}", "t", weight=1.0)
        inspector = RevokInspector(store, graph, None)
        result = await inspector.get_paths("t", max_hops=3, min_pressure=0.001, attenuation=1.0, max_paths=2)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_pressures_computed_correctly(self) -> None:
        store = _MemoryStore()
        graph = CausalGraph()
        graph.add_relation("a", "b", weight=0.8)
        graph.add_relation("b", "c", weight=0.5)
        inspector = RevokInspector(store, graph, None)
        result = await inspector.get_paths("c", max_hops=5, min_pressure=0.001, attenuation=1.0, max_paths=100)
        assert len(result) == 1
        path = result[0]
        # pressures: root=1.0, hop_b=0.8, hop_c=0.4
        assert path.pressures[0] == pytest.approx(1.0)
        assert path.pressures[1] == pytest.approx(0.8)
        assert path.pressures[2] == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# T013: get_signals
# ---------------------------------------------------------------------------


class TestGetSignals:
    @pytest.mark.asyncio
    async def test_entity_not_in_store_raises_entity_not_found(self) -> None:
        store = _MemoryStore()
        graph = CausalGraph()
        inspector = RevokInspector(store, graph, None)
        with pytest.raises(EntityNotFoundError):
            await inspector.get_signals("ghost")

    @pytest.mark.asyncio
    async def test_history_none_returns_none(self) -> None:
        store = _MemoryStore()
        await store.put(_record("alice", 0.5))
        graph = CausalGraph()
        inspector = RevokInspector(store, graph, None)
        result = await inspector.get_signals("alice")
        assert result is None

    @pytest.mark.asyncio
    async def test_entity_in_store_no_signals_returns_empty_list(self) -> None:
        from revok.signal_history import SqliteSignalHistoryStore

        store = _MemoryStore()
        await store.put(_record("alice", 0.5))
        graph = CausalGraph()

        import tempfile
        import os

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            history = SqliteSignalHistoryStore(db_path)
            await history.open()
            inspector = RevokInspector(store, graph, history)
            result = await inspector.get_signals("alice")
            assert result == []
        finally:
            await history.close()
            os.unlink(db_path)
