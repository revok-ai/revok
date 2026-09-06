# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Revok Contributors

"""Async signal consumer that applies root and causal propagation updates."""

from __future__ import annotations

import asyncio
import json
import logging
import time

from revok.config import CausalGraphConfig
from revok.entity_locks import EntityLockRegistry
from revok.interfaces import (
    DedupeStore,
    GraphBackend,
    MessageBus,
    ResolverTraceStore,
    SignalHistoryStore,
    StateStore,
)
from revok.models import (
    DedupeRecord,
    EntityRecord,
    ProcessingOutcome,
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
        dedupe: DedupeStore | None = None,
        locks: EntityLockRegistry | None = None,
    ) -> None:
        self._bus = bus
        self._store = store
        self._scorer = scorer
        self._graph = graph
        self._config = config
        self._history = history
        self._trace_store = trace_store
        self._dedupe = dedupe
        self._locks = locks or EntityLockRegistry()

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

    def _compute_traces(
        self, root_pressures: dict[str, float]
    ) -> list[PropagationTrace]:
        """Traverse propagation for every root without mutating any state."""
        if not self._config.enabled:
            return []
        return [
            self._graph.propagate_detailed(
                root,
                root_pressure,
                max_hops=self._config.max_hops,
                min_pressure=self._config.min_pressure,
                attenuation=self._config.attenuation,
            )
            for root, root_pressure in root_pressures.items()
        ]

    @staticmethod
    def _affected_keys(
        root_pressures: dict[str, float], traces: list[PropagationTrace]
    ) -> set[str]:
        """Return every entity key a signal will write, roots plus propagation."""
        keys = set(root_pressures)
        for trace in traces:
            keys.update(step.entity_key for step in trace.steps)
        return keys

    async def _apply_propagation(
        self,
        traces: list[PropagationTrace],
        processing_time: float,
        valid_time: float,
        source_id: str,
    ) -> list[SignalRecord]:
        """Apply propagation from all roots and keep max pressure per entity."""
        aggregate: dict[str, tuple[float, str]] = {}
        for trace in traces:
            root = trace.root_entity_key
            for step in trace.steps:
                if step.entity_key == root:
                    continue
                current = aggregate.get(step.entity_key)
                if current is None or step.pressure > current[0]:
                    aggregate[step.entity_key] = (step.pressure, root)

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
        return events

    async def process_one(self, signal: Signal) -> ProcessingOutcome:
        """Process one signal; failures are logged and reported, never raised.

        Returns:
            ProcessingOutcome: ``applied``, ``duplicate``, or ``failed``. The
            caller acknowledges the signal only when the outcome permits it.
        """
        signal_id = signal.signal_id or ""
        targets = self._resolve_targets(signal)
        if not targets:
            logger.info("Signal discarded: missing or empty entity_refs")
            return ProcessingOutcome(signal_id=signal_id, status="applied")

        if self._dedupe is None or not signal_id:
            return await self._apply_signal(signal, targets, signal_id)

        # The signal lock also serializes two concurrent deliveries of the same id.
        async with self._locks.acquire_signal(signal_id):
            try:
                already_processed = await self._dedupe.has(signal_id)
            except Exception:
                logger.error(
                    "Deduplication lookup failed for signal '%s'",
                    signal_id,
                    exc_info=True,
                )
                return ProcessingOutcome(
                    signal_id=signal_id,
                    status="failed",
                    failure_reason="deduplication lookup failed",
                )

            if already_processed:
                logger.info("Signal '%s' already processed; suppressing", signal_id)
                return ProcessingOutcome(signal_id=signal_id, status="duplicate")

            outcome = await self._apply_signal(signal, targets, signal_id)
            if outcome.status != "applied":
                return outcome

            try:
                await self._dedupe.record(
                    DedupeRecord(
                        signal_id=signal_id,
                        processed_at=time.time(),
                        source_name=signal.source_id,
                        entity_keys=outcome.entity_keys,
                    )
                )
            except Exception:
                logger.error(
                    "Deduplication record failed for signal '%s'",
                    signal_id,
                    exc_info=True,
                )
                return ProcessingOutcome(
                    signal_id=signal_id,
                    status="failed",
                    failure_reason="deduplication record failed",
                    entity_keys=outcome.entity_keys,
                )
            return outcome

    async def _apply_signal(
        self,
        signal: Signal,
        targets: list[ResolvedTarget],
        signal_id: str,
    ) -> ProcessingOutcome:
        """Apply a signal while holding a lock on every entity it writes."""
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
        lock_keys = self._affected_keys(root_pressures, self._compute_traces(root_pressures))

        # Runtime relation registration can grow the reached set between the
        # traversal above and lock acquisition, so re-traverse under lock.
        for attempt in (0, 1):
            async with self._locks.acquire_entities(lock_keys):
                traces = self._compute_traces(root_pressures)
                fresh_keys = self._affected_keys(root_pressures, traces)
                if attempt == 0 and not fresh_keys <= lock_keys:
                    lock_keys |= fresh_keys
                    continue
                return await self._apply_locked(
                    signal,
                    targets,
                    root_pressures,
                    traces,
                    processing_time,
                    valid_time,
                    signal_id,
                )

        return ProcessingOutcome(
            signal_id=signal_id,
            status="failed",
            failure_reason="entity lock set did not converge",
        )

    async def _apply_locked(
        self,
        signal: Signal,
        targets: list[ResolvedTarget],
        root_pressures: dict[str, float],
        traces: list[PropagationTrace],
        processing_time: float,
        valid_time: float,
        signal_id: str,
    ) -> ProcessingOutcome:
        failures: list[str] = []
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
            except Exception as exc:
                failures.append(f"{target.entity_key}: {exc}")
                logger.error(
                    "SignalProcessor failed applying root entity '%s'",
                    target.entity_key,
                    exc_info=True,
                )

        if self._config.enabled:
            events.extend(
                await self._apply_propagation(
                    traces, processing_time, valid_time, signal.source_id
                )
            )

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
                        propagation=traces,
                    )
                )
            except Exception:
                logger.error("Resolver trace completion failed", exc_info=True)

        entity_keys = tuple(event.entity_key for event in events)
        if failures:
            return ProcessingOutcome(
                signal_id=signal_id,
                status="failed",
                failure_reason="; ".join(failures),
                entity_keys=entity_keys,
            )
        return ProcessingOutcome(
            signal_id=signal_id, status="applied", entity_keys=entity_keys
        )

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
