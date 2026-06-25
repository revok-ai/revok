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


# ---------------------------------------------------------------------------
# T017: Full explainability acceptance scenario (E2E)
# ---------------------------------------------------------------------------


class TestFullExplainabilityScenario:
    """End-to-end acceptance test covering all Inspector read paths.

    Graph: A → B (weight=0.8)
    Signal posted for A with causal propagation enabled.
    Validates StateStore, SignalHistoryStore, and all four Inspector methods.
    """

    @pytest.mark.asyncio
    async def test_full_inspector_round_trip(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        import json
        import time as time_mod
        from pathlib import Path

        from revok.config import CausalGraphConfig, ScoringConfig, SignalPressureConfig, StateStoreConfig
        from revok.models import Signal
        from revok.scoring import ScoringEngine
        from revok.signal_history import SqliteSignalHistoryStore
        from revok.signal_processor import SignalProcessor
        from revok.state_store import SqliteStateStore

        # ── infrastructure setup ────────────────────────────────────────
        db_path = str(tmp_path / "e2e.db")
        history_db_path = str(tmp_path / "e2e_history.db")

        store = SqliteStateStore(StateStoreConfig(sqlite_path=db_path, hot_layer_max_entries=50))
        await store.open()

        history = SqliteSignalHistoryStore(history_db_path, max_rows=1000)
        await history.open()

        graph = CausalGraph()
        graph.add_relation("a", "b", weight=0.8)

        scorer = ScoringEngine(
            ScoringConfig(
                half_life_seconds=86400.0,
                signal_strength=0.3,
                score_cap=1.0,
                signal_pressure=SignalPressureConfig(
                    severity_weights={"low": 0.2, "medium": 0.5, "high": 0.8},
                    default_severity="medium",
                ),
            )
        )

        class _NullBus:
            async def publish(self, signal: Signal) -> None:
                pass

            async def consume(self) -> Signal:
                raise RuntimeError("not used")

            async def close(self) -> None:
                pass

        causal_cfg = CausalGraphConfig(
            enabled=True,
            max_hops=3,
            min_pressure=0.01,
            attenuation=1.0,
        )
        processor = SignalProcessor(
            _NullBus(),
            store,
            scorer,
            graph,
            causal_cfg,
            history=history,
        )
        inspector = RevokInspector(store, graph, history)

        # ── process a signal for A ───────────────────────────────────────
        now = time_mod.time()
        signal = Signal(
            raw_content="test",
            source_id="test-source",
            timestamp=now,
            http_method="POST",
            http_path="/signals",
            original_body=json.dumps({"entity_refs": ["a"], "severity": "high"}).encode(),
            headers={},
        )
        await processor.process_one(signal)

        # ── StateStore: both A and B must be updated ─────────────────────
        record_a = await store.get("a")
        record_b = await store.get("b")
        assert record_a is not None, "a must be in state store after processing"
        assert record_b is not None, "b must be in state store after propagation"
        assert record_a.score > 0, "a score must be positive"
        assert record_b.score > 0, "b score must be positive"

        # ── SignalHistoryStore: direct record for a ───────────────────────
        signals_a = await history.get_for_entity("a")
        assert len(signals_a) >= 1
        direct = signals_a[0]
        assert direct.is_propagated is False
        assert direct.upstream_source is None
        assert direct.entity_key == "a"

        # ── SignalHistoryStore: propagated record for b ───────────────────
        signals_b = await history.get_for_entity("b")
        assert len(signals_b) >= 1
        propagated = signals_b[0]
        assert propagated.is_propagated is True
        assert propagated.entity_key == "b"

        # ── inspect_entity("b"): state + upstream a + empty downstream ───
        before_inspect = time_mod.time()
        report = await inspector.inspect_entity("b")
        after_inspect = time_mod.time()
        assert report is not None
        assert report.score == pytest.approx(record_b.score)
        assert len(report.upstream) == 1
        assert report.upstream[0].entity_key == "a"
        assert report.upstream[0].direction == "upstream"
        assert report.upstream[0].weight == pytest.approx(0.8)
        assert report.downstream == []
        assert before_inspect <= report.inspected_at <= after_inspect

        # ── get_downstream("a"): returns b ──────────────────────────────
        downstream = await inspector.get_downstream(
            "a", max_hops=causal_cfg.max_hops, min_pressure=causal_cfg.min_pressure, attenuation=causal_cfg.attenuation
        )
        downstream_keys = [e.entity_key for e in downstream]
        assert "b" in downstream_keys

        # ── get_paths("b"): single path [a, b] marked dominant ──────────
        paths = await inspector.get_paths(
            "b",
            max_hops=causal_cfg.max_hops,
            min_pressure=causal_cfg.min_pressure,
            attenuation=causal_cfg.attenuation,
            max_paths=100,
        )
        assert len(paths) == 1
        path = paths[0]
        assert path.hops == ["a", "b"]
        assert path.is_dominant is True

        # ── get_signals("b"): propagation record visible ──────────────────
        signals_via_inspector = await inspector.get_signals("b")
        assert signals_via_inspector is not None
        assert any(r.is_propagated for r in signals_via_inspector)

        # ── teardown ────────────────────────────────────────────────────
        await history.close()
        await store.close()


# ---------------------------------------------------------------------------
# SC-003: performance — 100-node / ~450-edge graph within 1 s budget
# ---------------------------------------------------------------------------


class TestPerformanceSC003:
    """SC-003: get_downstream and get_paths complete within budget on a large graph."""

    def _build_layered_graph(self) -> tuple[CausalGraph, str, str]:
        # 10 layers × 10 nodes = 100 nodes; each node → 5 nodes in next layer = 450 edges
        graph = CausalGraph()
        layers = [[f"l{layer}_{i}" for i in range(10)] for layer in range(10)]
        for layer_idx, layer in enumerate(layers[:-1]):
            next_layer = layers[layer_idx + 1]
            for src in layer:
                for tgt in next_layer[:5]:
                    graph.add_relation(src, tgt, weight=0.9)
        return graph, layers[0][0], layers[-1][-1]

    @pytest.mark.asyncio
    async def test_get_downstream_completes_within_budget(self) -> None:
        import time

        store = _MemoryStore()
        graph, root, _ = self._build_layered_graph()
        inspector = RevokInspector(store, graph, None)

        start = time.perf_counter()
        result = await inspector.get_downstream(root, max_hops=15, min_pressure=0.0001, attenuation=0.95)
        elapsed = time.perf_counter() - start

        assert len(result) > 0, "expected downstream nodes in layered graph"
        assert elapsed < 1.0, f"get_downstream on ~100-node graph took {elapsed:.3f}s; budget 1.0s (SC-003)"

    @pytest.mark.asyncio
    async def test_get_paths_completes_within_budget(self) -> None:
        import time

        store = _MemoryStore()
        graph, _, leaf = self._build_layered_graph()
        inspector = RevokInspector(store, graph, None)

        start = time.perf_counter()
        result = await inspector.get_paths(
            leaf, max_hops=15, min_pressure=0.0001, attenuation=0.95, max_paths=100
        )
        elapsed = time.perf_counter() - start

        assert elapsed < 1.0, f"get_paths on ~100-node graph took {elapsed:.3f}s; budget 1.0s (SC-003)"
