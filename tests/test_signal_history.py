from __future__ import annotations

import pytest

from revok.models import (
    PropagationStep,
    PropagationTrace,
    ResolutionTrace,
    ResolvedTarget,
    SignalRecord,
)
from revok.signal_history import SqliteSignalHistoryStore


@pytest.mark.asyncio
async def test_resolver_trace_survives_reopen(tmp_db_path: str) -> None:
    trace = ResolutionTrace(
        signal_id="signal-1",
        created_at=1.0,
        completed_at=2.0,
        source_id="webhook",
        signal_text="Alice changed her profile",
        status="completed",
        error_code=None,
        error_detail=None,
        targets=[ResolvedTarget("user:alice", 0.8, "matched Alice")],
        dropped_targets=[],
        invalidations=[
            SignalRecord(
                id=None,
                entity_key="user:alice",
                source_id="webhook",
                processed_at=2.0,
                score_before=None,
                score_after=0.7,
                is_propagated=False,
                upstream_source=None,
            )
        ],
        propagation=[
            PropagationTrace(
                root_entity_key="user:alice",
                initial_pressure=0.56,
                steps=[
                    PropagationStep("user:alice", 0, 0.56),
                    PropagationStep("profile:alice", 1, 0.5, "user:alice", 0.9),
                ],
                termination_reason="completed",
            )
        ],
    )

    store = SqliteSignalHistoryStore(tmp_db_path, max_rows=10)
    await store.open()
    await store.start_trace(trace)
    await store.close()

    reopened = SqliteSignalHistoryStore(tmp_db_path, max_rows=10)
    await reopened.open()
    try:
        result = await reopened.get_trace("signal-1")
        assert result == trace
        assert (await reopened.list_traces()) == [trace]
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_trace_retention_is_global(tmp_db_path: str) -> None:
    store = SqliteSignalHistoryStore(tmp_db_path, max_rows=2)
    await store.open()
    try:
        for index in range(3):
            await store.start_trace(
                ResolutionTrace(
                    signal_id=f"signal-{index}",
                    created_at=float(index),
                    completed_at=float(index),
                    source_id="test",
                    signal_text="signal",
                    status="completed",
                    error_code=None,
                    error_detail=None,
                    targets=[],
                    dropped_targets=[],
                    invalidations=[],
                    propagation=[],
                )
            )
        traces = await store.list_traces()
        assert [trace.signal_id for trace in traces] == ["signal-2", "signal-1"]
    finally:
        await store.close()
