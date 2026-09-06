from __future__ import annotations

import asyncio

import pytest

from revok.causal_graph import CausalGraph
from revok.models import ResolvedTarget
from revok.resolver_runtime import invoke_resolver, validate_targets


class StaticResolver:
    def __init__(self, targets: list[ResolvedTarget]) -> None:
        self.targets = targets

    def resolve(self, signal_text: str) -> list[ResolvedTarget]:
        return self.targets


class SlowResolver:
    def resolve(self, signal_text: str) -> list[ResolvedTarget]:
        import time

        time.sleep(0.1)
        return []


def test_validate_targets_normalizes_keys_and_confidence_order() -> None:
    graph = CausalGraph()
    graph.add_entity("user:alice", 1.0)
    graph.add_entity("user:bob", 1.0)

    result = validate_targets(
        [
            ResolvedTarget("USER:BOB", 0.2, "bob"),
            ResolvedTarget("USER:ALICE", 0.8, "alice"),
        ],
        graph,
    )

    assert [target.entity_key for target in result.targets] == ["user:alice", "user:bob"]


def test_validate_targets_reports_unknown_keys_without_dropping_valid_targets() -> None:
    graph = CausalGraph()
    graph.add_entity("valid", 1.0)
    result = validate_targets(
        [ResolvedTarget("missing", 0.5, "no"), ResolvedTarget("valid", 0.8, "yes")],
        graph,
    )
    assert [target.entity_key for target in result.targets] == ["valid"]
    assert result.dropped_targets[0].entity_key == "missing"


def test_validate_targets_all_unknown_is_zero_match() -> None:
    result = validate_targets([ResolvedTarget("missing", 0.5, "no")], CausalGraph())
    assert result.targets == []
    assert result.dropped_targets[0].reason == "unknown_graph_entity"


@pytest.mark.asyncio
async def test_invoke_resolver_returns_validated_targets() -> None:
    graph = CausalGraph()
    graph.add_entity("a", 1.0)
    result = await invoke_resolver(
        StaticResolver([ResolvedTarget("a", 0.5, "match")]),
        "signal",
        graph,
        timeout_seconds=1.0,
    )
    assert result.targets == [ResolvedTarget("a", 0.5, "match")]


@pytest.mark.asyncio
async def test_invoke_resolver_timeout_is_failure() -> None:
    with pytest.raises(asyncio.TimeoutError):
        await invoke_resolver(SlowResolver(), "signal", CausalGraph(), timeout_seconds=0.01)
