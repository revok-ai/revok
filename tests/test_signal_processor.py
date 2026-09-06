# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Revok Contributors

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import replace

import pytest

from revok.causal_graph import CausalGraph
from revok.config import CausalGraphConfig, ScoringConfig, SignalPressureConfig
from revok.models import EntityRecord, Signal
from revok.scoring import ScoringEngine
from revok.signal_processor import SignalProcessor


class InMemoryStore:
    def __init__(self) -> None:
        self._data: dict[str, EntityRecord] = {}

    async def get(self, entity_key: str) -> EntityRecord | None:
        return self._data.get(entity_key)

    async def put(self, record: EntityRecord) -> None:
        self._data[record.entity_key] = record

    async def list_all(self, offset: int = 0, limit: int = 100) -> list[EntityRecord]:
        rows = sorted(self._data.values(), key=lambda r: r.entity_key)
        return rows[offset : offset + limit]

    async def delete(self, entity_key: str) -> bool:
        return self._data.pop(entity_key, None) is not None

    async def close(self) -> None:
        return None


class StubBus:
    async def publish(self, signal: Signal) -> None:
        return None

    async def consume(self) -> Signal:
        raise RuntimeError("not used")

    async def close(self) -> None:
        return None


def _make_signal(entity_refs: list[str] | None = None, severity: str | None = None) -> Signal:
    body: dict[str, object] = {}
    if entity_refs is not None:
        body["entity_refs"] = entity_refs
    if severity is not None:
        body["severity"] = severity
    return Signal(
        raw_content="signal",
        source_id="webhook",
        timestamp=time.time(),
        http_method="POST",
        http_path="/signals",
        original_body=json.dumps(body).encode("utf-8"),
        headers={},
    )


def _processor(
    enabled: bool = True, dedupe: object | None = None
) -> tuple[SignalProcessor, InMemoryStore, CausalGraph]:
    store = InMemoryStore()
    graph = CausalGraph()
    scorer = ScoringEngine(
        ScoringConfig(
            half_life_seconds=86400.0,
            signal_strength=0.3,
            score_cap=1.0,
            signal_pressure=SignalPressureConfig(
                severity_weights={"low": 0.2, "medium": 0.4, "high": 0.7},
                default_severity="medium",
            ),
        )
    )
    processor = SignalProcessor(
        StubBus(),
        store,
        scorer,
        graph,
        CausalGraphConfig(
            enabled=enabled,
            max_hops=2,
            min_pressure=0.05,
            attenuation=1.0,
            processing_timeout_seconds=2.0,
        ),
        dedupe=dedupe,  # type: ignore[arg-type]
    )
    return processor, store, graph


async def test_single_root_entity_score_decreases_after_process_one() -> None:
    processor, store, _graph = _processor(enabled=False)
    await store.put(
        EntityRecord(
            entity_key="alpha",
            score=1.0,
            valid_time=time.time(),
            transaction_time=time.time(),
            signal_count=1,
            pattern_name="seed",
        )
    )

    await processor.process_one(_make_signal(entity_refs=["alpha"], severity="high"))
    updated = await store.get("alpha")
    assert updated is not None
    assert updated.score < 1.0


async def test_unknown_severity_falls_back_to_default() -> None:
    processor, store, _graph = _processor(enabled=False)
    await processor.process_one(_make_signal(entity_refs=["alpha"], severity="unknown"))
    updated = await store.get("alpha")
    assert updated is not None
    # default medium=0.4 => effective deduction 0.12 from cap
    assert updated.score == 1.0 - (0.3 * 0.4)


async def test_missing_or_empty_entity_refs_discards_signal() -> None:
    processor, store, _graph = _processor(enabled=False)
    await processor.process_one(_make_signal(entity_refs=[]))
    await processor.process_one(_make_signal(entity_refs=None))
    assert await store.list_all() == []


async def test_store_error_during_apply_root_is_swallowed() -> None:
    class FailingStore(InMemoryStore):
        async def put(self, record: EntityRecord) -> None:
            raise RuntimeError("boom")

    store = FailingStore()
    scorer = ScoringEngine(
        ScoringConfig(half_life_seconds=86400.0, signal_strength=0.3, score_cap=1.0)
    )
    processor = SignalProcessor(
        StubBus(),
        store,
        scorer,
        CausalGraph(),
        CausalGraphConfig(enabled=False),
    )

    await processor.process_one(_make_signal(entity_refs=["alpha"], severity="high"))


async def test_downstream_entities_decrease_with_propagation() -> None:
    processor, store, graph = _processor(enabled=True)
    graph.add_relation("a", "b", weight=0.5)

    await processor.process_one(_make_signal(entity_refs=["a"], severity="high"))

    root = await store.get("a")
    downstream = await store.get("b")
    assert root is not None
    assert downstream is not None
    assert downstream.score < 1.0


async def test_two_root_overlap_uses_max_pressure_not_sum() -> None:
    processor, store, graph = _processor(enabled=True)
    graph.add_relation("r1", "x", weight=0.2)
    graph.add_relation("r2", "x", weight=0.9)

    await processor.process_one(_make_signal(entity_refs=["r1", "r2"], severity="high"))

    x = await store.get("x")
    assert x is not None
    expected_pressure = 0.7 * 0.9
    expected_score = 1.0 - (0.3 * expected_pressure)
    assert x.score == expected_score


async def test_valid_time_and_transaction_time_diverge_by_event_vs_processing_time() -> None:
    processor, store, _graph = _processor(enabled=False)
    processing_now = time.time()
    event_time = processing_now - 60.0
    signal = Signal(
        raw_content="signal",
        source_id="webhook",
        timestamp=event_time,
        http_method="POST",
        http_path="/signals",
        original_body=json.dumps({"entity_refs": ["alpha"], "severity": "high"}).encode(
            "utf-8"
        ),
        headers={},
    )

    await processor.process_one(signal)
    record = await store.get("alpha")
    assert record is not None
    assert record.valid_time == pytest.approx(event_time, abs=0.01)
    assert record.transaction_time >= processing_now
    assert record.transaction_time > record.valid_time


async def test_future_timestamp_is_clamped_to_processing_time(caplog: pytest.LogCaptureFixture) -> None:
    processor, store, _graph = _processor(enabled=False)
    future = time.time() + 120.0
    signal = Signal(
        raw_content="signal",
        source_id="webhook",
        timestamp=future,
        http_method="POST",
        http_path="/signals",
        original_body=json.dumps({"entity_refs": ["alpha"], "severity": "high"}).encode(
            "utf-8"
        ),
        headers={},
    )

    with caplog.at_level("WARNING"):
        await processor.process_one(signal)

    record = await store.get("alpha")
    assert record is not None
    assert record.valid_time == pytest.approx(record.transaction_time, abs=0.05)
    assert "is in the future" in caplog.text


async def test_out_of_order_signal_logs_warning_but_applies(
    caplog: pytest.LogCaptureFixture,
) -> None:
    processor, store, _graph = _processor(enabled=False)

    await store.put(
        EntityRecord(
            entity_key="alpha",
            score=1.0,
            valid_time=1000.0,
            transaction_time=1000.0,
            signal_count=1,
            pattern_name="seed",
        )
    )

    signal = Signal(
        raw_content="signal",
        source_id="webhook",
        timestamp=900.0,
        http_method="POST",
        http_path="/signals",
        original_body=json.dumps({"entity_refs": ["alpha"], "severity": "high"}).encode(
            "utf-8"
        ),
        headers={},
    )

    with caplog.at_level("WARNING"):
        await processor.process_one(signal)

    updated = await store.get("alpha")
    assert updated is not None
    assert updated.signal_count == 2
    assert updated.valid_time == pytest.approx(900.0, abs=0.01)
    assert "Out-of-order signal" in caplog.text


# ---------------------------------------------------------------------------
# Concurrency stress
# ---------------------------------------------------------------------------

class YieldingStore(InMemoryStore):
    """Store that suspends between read and write so lost updates can surface.

    ``InMemoryStore`` contains no awaits, so its coroutines never yield and
    concurrent ``process_one`` calls would run to completion one at a time,
    hiding read-modify-write races.
    """

    async def get(self, entity_key: str) -> EntityRecord | None:
        await asyncio.sleep(0)
        return await super().get(entity_key)

    async def put(self, record: EntityRecord) -> None:
        await asyncio.sleep(0)
        await super().put(record)


STRESS_ROOTS = ("a", "b", "c")
STRESS_SIGNAL_COUNT = 50


def _stress_processor() -> tuple[SignalProcessor, YieldingStore]:
    """Build a processor whose roots all propagate onto shared downstream nodes."""
    store = YieldingStore()
    graph = CausalGraph()
    for root in STRESS_ROOTS:
        graph.add_relation(root, "shared", weight=1.0)
    graph.add_relation("shared", "tail", weight=1.0)

    # Small signal_strength keeps 50 accumulated updates well clear of the 0.0
    # floor, so clamping cannot mask a lost update.
    scorer = ScoringEngine(
        ScoringConfig(
            half_life_seconds=86400.0,
            signal_strength=0.001,
            score_cap=1.0,
            signal_pressure=SignalPressureConfig(
                severity_weights={"low": 0.2},
                default_severity="low",
            ),
        )
    )
    processor = SignalProcessor(
        StubBus(),
        store,
        scorer,
        graph,
        CausalGraphConfig(
            enabled=True,
            max_hops=2,
            min_pressure=0.05,
            attenuation=1.0,
            processing_timeout_seconds=5.0,
        ),
    )
    return processor, store


async def _stress_snapshot(
    signals: list[Signal],
    *,
    concurrent: bool,
) -> dict[str, tuple[float, int]]:
    processor, store = _stress_processor()
    if concurrent:
        await asyncio.gather(*(processor.process_one(s) for s in signals))
    else:
        for signal in signals:
            await processor.process_one(signal)
    records = await store.list_all(limit=1000)
    return {r.entity_key: (round(r.score, 9), r.signal_count) for r in records}


async def test_concurrent_processing_matches_serial_processing() -> None:
    """Concurrent processing must produce the same scores as serial processing.

    Every root propagates onto ``shared`` and ``tail``, so all 50 signals contend
    for the same two downstream entities. A lost update shows up as a lower
    signal_count or a higher score than the serial baseline.
    """
    timestamp = time.time()
    signals = [
        replace(
            _make_signal(entity_refs=[STRESS_ROOTS[i % len(STRESS_ROOTS)]], severity="low"),
            timestamp=timestamp,
        )
        for i in range(STRESS_SIGNAL_COUNT)
    ]

    serial = await _stress_snapshot(signals, concurrent=False)
    concurrent = await _stress_snapshot(signals, concurrent=True)

    # Guard against a vacuous pass: the shared entities must actually accumulate
    # every update, and scores must move without hitting the clamp.
    assert serial["shared"][1] == STRESS_SIGNAL_COUNT
    assert serial["tail"][1] == STRESS_SIGNAL_COUNT
    assert 0.0 < serial["shared"][0] < 1.0

    assert concurrent == serial


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


async def test_duplicate_delivery_is_suppressed_with_tracing_disabled(
    tmp_db_path: str,
) -> None:
    """Idempotency must not depend on trace storage being enabled."""
    from revok.signal_dedupe import SqliteIngestionStore

    dedupe = SqliteIngestionStore(tmp_db_path)
    await dedupe.open()
    try:
        # trace_store is None here: tracing is off, idempotency must still hold.
        processor, store, _graph = _processor(enabled=False, dedupe=dedupe)
        signal = replace(
            _make_signal(entity_refs=["alpha"], severity="high"), signal_id="dup-1"
        )

        first = await processor.process_one(signal)
        after_first = await store.get("alpha")
        second = await processor.process_one(signal)
        after_second = await store.get("alpha")

        assert first.status == "applied"
        assert second.status == "duplicate"
        assert after_first is not None and after_second is not None
        assert after_second.score == after_first.score
        assert after_second.signal_count == after_first.signal_count
    finally:
        await dedupe.close()


async def test_distinct_signals_targeting_one_entity_both_apply(
    tmp_db_path: str,
) -> None:
    from revok.signal_dedupe import SqliteIngestionStore

    dedupe = SqliteIngestionStore(tmp_db_path)
    await dedupe.open()
    try:
        processor, store, _graph = _processor(enabled=False, dedupe=dedupe)
        for signal_id in ("a-1", "a-2"):
            await processor.process_one(
                replace(
                    _make_signal(entity_refs=["alpha"], severity="high"),
                    signal_id=signal_id,
                )
            )

        record = await store.get("alpha")
        assert record is not None
        assert record.signal_count == 2
    finally:
        await dedupe.close()


async def test_concurrent_duplicate_delivery_applies_once(tmp_db_path: str) -> None:
    from revok.signal_dedupe import SqliteIngestionStore

    dedupe = SqliteIngestionStore(tmp_db_path)
    await dedupe.open()
    try:
        processor, store, _graph = _processor(enabled=False, dedupe=dedupe)
        signal = replace(
            _make_signal(entity_refs=["alpha"], severity="high"), signal_id="race-1"
        )

        outcomes = await asyncio.gather(
            processor.process_one(signal), processor.process_one(signal)
        )

        statuses = sorted(o.status for o in outcomes)
        assert statuses == ["applied", "duplicate"]
        record = await store.get("alpha")
        assert record is not None
        assert record.signal_count == 1
    finally:
        await dedupe.close()


async def test_scoring_is_precomputed_on_the_write_path() -> None:
    """Reads must not recompute or mutate confidence."""
    processor, store, _graph = _processor(enabled=False)
    await processor.process_one(_make_signal(entity_refs=["alpha"], severity="high"))

    first = await store.get("alpha")
    second = await store.get("alpha")

    assert first is not None and second is not None
    assert first.score == second.score
    assert first.signal_count == second.signal_count
    # The stored value already reflects the applied pressure.
    assert first.score == 1.0 - (0.3 * 0.7)


async def test_propagation_attenuation_semantics_are_unchanged() -> None:
    processor, store, graph = _processor(enabled=True)
    graph.add_relation("root", "mid", weight=0.5)

    await processor.process_one(_make_signal(entity_refs=["root"], severity="high"))

    mid = await store.get("mid")
    assert mid is not None
    # pressure 0.7 * edge weight 0.5 * attenuation 1.0
    assert mid.score == 1.0 - (0.3 * 0.7 * 0.5)
