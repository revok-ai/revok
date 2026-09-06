"""Runtime wrappers around externally provided resolver implementations."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

from revok.interfaces import GraphReader, Resolver
from revok.models import DroppedTarget, ResolvedTarget, TargetValidationResult

DEFAULT_RESOLVER_TIMEOUT_SECONDS = 10.0


def validate_targets(
    targets: Iterable[ResolvedTarget],
    graph: GraphReader,
) -> TargetValidationResult:
    """Validate resolver output without performing any resolution.

    Returned targets must reference existing graph nodes and have confidence in
    the inclusive range ``[0.0, 1.0]``. Results are ordered highest-first.
    """
    validated: list[ResolvedTarget] = []
    dropped: list[DroppedTarget] = []
    graph_nodes = set(graph.nodes())
    for target in targets:
        if not isinstance(target, ResolvedTarget):
            raise TypeError("resolver results must be ResolvedTarget instances")
        entity_key = target.entity_key.strip().lower()
        if not entity_key or entity_key not in graph_nodes:
            dropped.append(
                DroppedTarget(
                    entity_key=target.entity_key,
                    reason="unknown_graph_entity",
                )
            )
            continue
        if not 0.0 <= target.confidence <= 1.0:
            raise ValueError(f"resolver confidence must be in [0, 1], got {target.confidence!r}")
        validated.append(
            ResolvedTarget(
                entity_key=entity_key,
                confidence=float(target.confidence),
                rationale=target.rationale,
            )
        )
    return TargetValidationResult(
        targets=sorted(validated, key=lambda target: target.confidence, reverse=True),
        dropped_targets=dropped,
    )


async def invoke_resolver(
    resolver: Resolver,
    signal_text: str,
    graph: GraphReader,
    *,
    timeout_seconds: float = DEFAULT_RESOLVER_TIMEOUT_SECONDS,
) -> TargetValidationResult:
    """Invoke an injected resolver with a bounded timeout.

    This function wraps an external resolver; it does not resolve signals.
    Exceptions and timeouts are allowed to propagate to the HTTP failure path.
    """
    if timeout_seconds <= 0.0:
        raise ValueError("resolver timeout must be greater than zero")
    raw_targets = await asyncio.wait_for(
        asyncio.to_thread(resolver.resolve, signal_text),
        timeout=timeout_seconds,
    )
    return validate_targets(raw_targets, graph)
