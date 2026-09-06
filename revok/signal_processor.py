# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Revok Contributors

"""Async signal consumer that applies root and causal propagation updates."""

from __future__ import annotations

import asyncio
import json
import logging
import time

from revok.config import CausalGraphConfig
from revok.interfaces import (
    GraphBackend,
    MessageBus,
    ResolverTraceStore,
    SignalHistoryStore,
    StateStore,
)
from revok.models import (
    EntityRecord,
    PropagationTrace,
    ResolutionTrace,
    ResolvedTarget,
    Signal,
    SignalRecord,
)
from revok.scoring import ScoringEngine

logger = logging.getLogger(__name__)


class SignalProcessor:
    """Consume external signals and apply score updates.

    Processing policy: errors are logged and discarded so the consumer loop continues.
    """

    def __init__(
        self,
        bus: MessageBus,
        store: StateStore,
        scorer: ScoringEngine,
        graph: GraphBackend,
        config: CausalGraphConfig,
        history: SignalHistoryStore | None = None,
        trace_store: ResolverTraceStore | None = None,
    ) -> None:
        self._bus = bus
        self._store = store
        self._scorer = scorer
        self._graph = graph
        self._config = config
        self._history = history
        self._trace_store = trace_store

    def _parse_signal_payload(self, signal: Signal) -> dict[str, object]:
        if not signal.original_body:
            return {}
        try:
            parsed = json.loads(signal.original_body)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, ValueError):
            return {}

    def _resolve_entities(self, signal: Signal) -> list[str]:
        """Resolve root entities from /signals body entity_refs only."""
        return [target.entity_key for target in self._resolve_targets(signal)]

    def _resolve_targets(self, signal: Signal) -> list[ResolvedTarget]:
        """Resolve explicit or precomputed targets carried by the signal."""
        if signal.resolved_targets:
            return list(signal.resolved_targets)
        payload = self._parse_signal_payload(signal)
        refs = payload.get("entity_refs")
        if not isinstance(refs, list):
            return []
        resolved: list[ResolvedTarget] = []
        seen: set[str] = set()
        for item in refs:
            entity = str(item).strip().lower()
            if not entity or entity in seen:
                continue
            seen.add(entity)
            resolved.append(ResolvedTarget(entity, 1.0, "explicit entity reference"))
        return resolved

    def _resolve_pressure(self, signal: Signal) -> float:
        payload = self._parse_signal_payload(signal)
        severity = payload.get("severity")
        severity_value = str(severity) if severity is not None else None
        return self._scorer.pressure_for_severity(severity_value)

    async def _record_history_event(self, event: SignalRecord) -> None:
        if self._history is None:
            return
        try:
            await self._history.record(event)
        except Exception:
            logger.error("Signal history recording failed for '%s'", event.entity_key, exc_info=True)

    async def _apply_root(
        self,
        entity_key: str,
        pressure: float,
        processing_time: float,
        valid_time: float,
        source_id: str,
        is_propagated: bool = False,
        upstream_source: str | None = None,
    ) -> tuple[float | None, float, SignalRecord]:
        existing = await self._store.get(entity_key)
        if existing is not None and valid_time < existing.valid_time:
            logger.warning(
                "Out-of-order signal for '%s': valid_time %.3f is earlier than existing "
                "valid_time %.3f; applying anyway",
                entity_key,
                valid_time,
                existing.valid_time,
            )

        score_before = existing.score if existing is not None else None
        new_score = self._scorer.score_with_pressure(existing, valid_time, pressure)
        signal_count = (existing.signal_count + 1) if existing is not None else 1
        record = EntityRecord(
            entity_key=entity_key,
            score=new_score,
            valid_time=valid_time,
            transaction_time=processing_time,
            signal_count=signal_count,
            pattern_name=(existing.pattern_name if existing is not None else "signal"),
            contradiction_count=(existing.contradiction_count if existing is not None else 0),
            last_contradiction_time=(
                existing.last_contradiction_time if existing is not None else None
            ),
            last_value_fingerprint=(
                existing.last_value_fingerprint if existing is not None else None
            ),
        )
        await self._store.put(record)
        self._graph.add_entity(entity_key, new_score)

        event = SignalRecord(
            id=None,
            entity_key=entity_key,
            source_id=source_id,
            processed_at=processing_time,
            score_before=score_before,
            score_after=new_score,
            is_propagated=is_propagated,
            upstream_source=upstream_source,
        )
        await self._record_history_event(event)

        return score_before, new_score, event

    async def _apply_propagation(
        self,
        root_pressures: dict[str, float],
        processing_time: float,
        valid_time: float,
        source_id: str,
    ) -> tuple[list[SignalRecord], list[PropagationTrace]]:
        """Apply propagation from all roots and keep max pressure per entity."""
        aggregate: dict[str, tuple[float, str]] = {}
        traces: list[PropagationTrace] = []
        for root, root_pressure in root_pressures.items():
            trace = self._graph.propagate_detailed(
                root,
                root_pressure,
                max_hops=self._config.max_hops,
                min_pressure=self._config.min_pressure,
                attenuation=self._config.attenuation,
            )
            traces.append(trace)
            propagated = {
                step.entity_key: step.pressure
                for step in trace.steps
                if step.entity_key != root
            }
            for entity_key, propagated_pressure in propagated.items():
                current = aggregate.get(entity_key)
                if current is None or propagated_pressure > current[0]:
                    aggregate[entity_key] = (propagated_pressure, root)

        events: list[SignalRecord] = []
        for entity_key, (propagated_pressure, root) in aggregate.items():
            try:
                _, _, event = await self._apply_root(
                    entity_key,
                    propagated_pressure,
                    processing_time,
                    valid_time,
                    source_id=source_id,
                    is_propagated=True,
                    upstream_source=root,
                )
                events.append(event)
            except Exception:
                logger.error(
                    "SignalProcessor failed applying propagated entity '%s'",
                    entity_key,
                    exc_info=True,
                )
        return events, traces

    async def process_one(self, signal: Signal) -> None:
        """Process one signal; all failures are logged and swallowed."""
        targets = self._resolve_targets(signal)
        if not targets:
            logger.info("Signal discarded: missing or empty entity_refs")
            return

        processing_time = time.time()
        valid_time = signal.timestamp if signal.timestamp > 0 else processing_time
        if valid_time > processing_time:
            logger.warning(
                "signal.timestamp %.3f is in the future (transaction_time=%.3f); "
                "clamping valid_time",
                valid_time,
                processing_time,
            )
            valid_time = processing_time

        pressure = self._resolve_pressure(signal)
        root_pressures = {
            target.entity_key: pressure * target.confidence for target in targets
        }

        events: list[SignalRecord] = []
        for target in targets:
            try:
                _, _, event = await self._apply_root(
                    target.entity_key,
                    root_pressures[target.entity_key],
                    processing_time,
                    valid_time,
                    signal.source_id,
                )
                events.append(event)
            except Exception:
                logger.error(
                    "SignalProcessor failed applying root entity '%s'",
                    target.entity_key,
                    exc_info=True,
                )

        if self._config.enabled:
            propagated_events, propagation = await self._apply_propagation(
                root_pressures,
                processing_time,
                valid_time,
                signal.source_id,
            )
            events.extend(propagated_events)
        else:
            propagation = []

        if self._trace_store is not None and signal.signal_id is not None:
            try:
                await self._trace_store.finish_trace(
                    ResolutionTrace(
                        signal_id=signal.signal_id,
                        created_at=processing_time,
                        completed_at=time.time(),
                        source_id=signal.source_id,
                        signal_text=signal.signal_text or "",
                        status="completed",
                        error_code=None,
                        error_detail=None,
                        targets=targets,
                        dropped_targets=list(signal.dropped_targets),
                        invalidations=events,
                        propagation=propagation,
                    )
                )
            except Exception:
                logger.error("Resolver trace completion failed", exc_info=True)

    async def run(self) -> None:
        """Consume indefinitely until cancelled or bus is closed."""
        while True:
            try:
                signal = await self._bus.consume()
            except asyncio.CancelledError:
                break

            try:
                await asyncio.wait_for(
                    self.process_one(signal),
                    timeout=self._config.processing_timeout_seconds,
                )
            except asyncio.TimeoutError:
                logger.error(
                    "SignalProcessor timed out after %.3fs",
                    self._config.processing_timeout_seconds,
                )
            except Exception:
                logger.error("SignalProcessor failed processing signal", exc_info=True)
