from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from revok.config import (
    CausalGraphConfig,
    CausalRelationshipConfig,
    Config,
    InspectorConfig,
)
from revok.entity_matcher import EntityMatcher
from revok.models import ResolvedTarget
from revok.proxy import build_app
from revok.scoring import ScoringEngine
from revok.state_store import SqliteStateStore


class DeterministicResolver:
    def resolve(self, signal_text: str) -> list[ResolvedTarget]:
        assert signal_text == "Alice changed her profile"
        return [ResolvedTarget("a", 0.5, "deterministic test match")]


class ZeroMatchResolver:
    def resolve(self, signal_text: str) -> list[ResolvedTarget]:
        return []


async def test_free_text_signal_propagates_and_trace_is_retrievable(
    minimal_config: Config,
    tmp_path: Path,
) -> None:
    config = replace(
        minimal_config,
        state_store=replace(minimal_config.state_store, sqlite_path=str(tmp_path / "integration.db")),
        causal_graph=CausalGraphConfig(
            enabled=True,
            max_hops=3,
            min_pressure=0.05,
            attenuation=1.0,
            relationships=[
                CausalRelationshipConfig("a", "b", 0.8),
                CausalRelationshipConfig("b", "c", 0.7),
            ],
        ),
        inspector=InspectorConfig(
            enabled=True,
            signal_history_enabled=True,
            signal_history_max_rows=100,
        ),
    )
    store = SqliteStateStore(config.state_store)
    await store.open()
    try:
        app = build_app(
            config,
            store,
            EntityMatcher(config.entity_matcher),
            ScoringEngine(config.scoring),
            resolver=DeterministicResolver(),
        )
        async with TestClient(TestServer(app)) as client:
            response = await client.post(
                "/signals",
                json={
                    "signal_text": "Alice changed her profile",
                    "severity": "medium",
                    "source": "integration-test",
                },
            )
            assert response.status == 202
            accepted = await response.json()
            assert accepted["resolved_targets"] == 1
            signal_id = accepted["signal_id"]

            trace = None
            for _ in range(20):
                await asyncio.sleep(0.01)
                trace_response = await client.get(f"/v1/resolver/traces/{signal_id}")
                if trace_response.status == 200:
                    trace = await trace_response.json()
                    if trace["status"] == "completed":
                        break
            assert trace is not None
            assert trace["status"] == "completed"
            assert trace["targets"][0]["entity_key"] == "a"
            assert trace["targets"][0]["confidence"] == 0.5
            propagation = trace["propagation"][0]
            assert propagation["root_entity_key"] == "a"
            assert propagation["termination_reason"] == "completed"
            assert [step["entity_key"] for step in propagation["steps"]] == ["a", "b", "c"]
            assert [step["depth"] for step in propagation["steps"]] == [0, 1, 2]
            assert [step["edge_weight"] for step in propagation["steps"]] == [None, 0.8, 0.7]
            root_pressure = ScoringEngine(config.scoring).pressure_for_severity("medium") * 0.5
            assert propagation["steps"][0]["pressure"] == pytest.approx(root_pressure)
            assert propagation["steps"][1]["pressure"] == pytest.approx(root_pressure * 0.8)
            assert propagation["steps"][2]["pressure"] == pytest.approx(
                root_pressure * 0.8 * 0.7
            )
            assert trace["invalidations"]
    finally:
        await store.close()


async def test_zero_match_signal_persists_completed_empty_trace(
    minimal_config: Config,
    tmp_path: Path,
) -> None:
    config = replace(
        minimal_config,
        state_store=replace(minimal_config.state_store, sqlite_path=str(tmp_path / "zero-match.db")),
        causal_graph=CausalGraphConfig(enabled=True),
        inspector=InspectorConfig(
            enabled=True,
            signal_history_enabled=True,
            signal_history_max_rows=100,
        ),
    )
    store = SqliteStateStore(config.state_store)
    await store.open()
    try:
        app = build_app(
            config,
            store,
            EntityMatcher(config.entity_matcher),
            ScoringEngine(config.scoring),
            resolver=ZeroMatchResolver(),
        )
        async with TestClient(TestServer(app)) as client:
            response = await client.post(
                "/signals",
                json={
                    "signal_text": "No matching belief",
                    "severity": "medium",
                    "source": "integration-test",
                },
            )
            assert response.status == 202
            accepted = await response.json()
            assert accepted["resolved_targets"] == 0
            signal_id = accepted["signal_id"]

            trace_response = await client.get(f"/v1/resolver/traces/{signal_id}")
            assert trace_response.status == 200
            trace = await trace_response.json()
            assert trace["status"] == "completed"
            assert trace["targets"] == []
            assert trace["invalidations"] == []
            assert trace["propagation"] == []
    finally:
        await store.close()
