# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Revok Contributors

from __future__ import annotations

import json
import time

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


def _processor(enabled: bool = True) -> tuple[SignalProcessor, InMemoryStore, CausalGraph]:
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
