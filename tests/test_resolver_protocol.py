from __future__ import annotations

from revok.causal_graph import CausalGraph
from revok.interfaces import GraphBackend, GraphReader, Resolver, ResolverTraceStore
from revok.models import ResolvedTarget


class ProtocolResolver:
    def resolve(self, signal_text: str) -> list[ResolvedTarget]:
        return []


def test_graph_backend_and_reader_protocols_are_runtime_checkable() -> None:
    graph = CausalGraph()
    assert isinstance(graph, GraphBackend)
    assert isinstance(graph, GraphReader)


def test_resolver_protocol_is_runtime_checkable() -> None:
    assert isinstance(ProtocolResolver(), Resolver)
    assert not isinstance(object(), Resolver)


def test_trace_store_protocol_requires_trace_methods() -> None:
    assert not isinstance(object(), ResolverTraceStore)
