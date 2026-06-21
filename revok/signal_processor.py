# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Revok Contributors

"""Async signal consumer that applies root and causal propagation updates."""

from __future__ import annotations

import asyncio
import json
import logging
import time

from revok.config import CausalGraphConfig
from revok.interfaces import GraphBackend, MessageBus, StateStore
from revok.models import EntityRecord, Signal
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
    ) -> None:
        self._bus = bus
        self._store = store
        self._scorer = scorer
        self._graph = graph
        self._config = config

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
        payload = self._parse_signal_payload(signal)
        refs = payload.get("entity_refs")
        if not isinstance(refs, list):
            return []
        resolved: list[str] = []
        seen: set[str] = set()
        for item in refs:
            entity = str(item).strip().lower()
            if not entity or entity in seen:
                continue
            seen.add(entity)
            resolved.append(entity)
        return resolved

    def _resolve_pressure(self, signal: Signal) -> float:
        payload = self._parse_signal_payload(signal)
        severity = payload.get("severity")
        severity_value = str(severity) if severity is not None else None
        return self._scorer.pressure_for_severity(severity_value)

    async def _apply_root(
        self,
        entity_key: str,
        pressure: float,
        processing_time: float,
        valid_time: float,
    ) -> None:
        existing = await self._store.get(entity_key)
        if existing is not None and valid_time < existing.valid_time:
            logger.warning(
                "Out-of-order signal for '%s': valid_time %.3f is earlier than existing "
                "valid_time %.3f; applying anyway",
                entity_key,
                valid_time,
                existing.valid_time,
            )

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

    async def _apply_propagation(
        self,
        root_keys: list[str],
        pressure: float,
        processing_time: float,
        valid_time: float,
    ) -> None:
        """Apply propagation from all roots and keep max pressure per entity."""
        aggregate: dict[str, float] = {}
        for root in root_keys:
            propagated = self._graph.propagate(
                root,
                pressure,
                max_hops=self._config.max_hops,
                min_pressure=self._config.min_pressure,
                attenuation=self._config.attenuation,
            )
            for entity_key, propagated_pressure in propagated.items():
                current = aggregate.get(entity_key)
                if current is None or propagated_pressure > current:
                    aggregate[entity_key] = propagated_pressure

        for entity_key, propagated_pressure in aggregate.items():
            try:
                await self._apply_root(
                    entity_key,
                    propagated_pressure,
                    processing_time,
                    valid_time,
                )
            except Exception:
                logger.error(
                    "SignalProcessor failed applying propagated entity '%s'",
                    entity_key,
                    exc_info=True,
                )

    async def process_one(self, signal: Signal) -> None:
        """Process one signal; all failures are logged and swallowed."""
        roots = self._resolve_entities(signal)
        if not roots:
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

        for root in roots:
            try:
                await self._apply_root(root, pressure, processing_time, valid_time)
            except Exception:
                logger.error(
                    "SignalProcessor failed applying root entity '%s'",
                    root,
                    exc_info=True,
                )

        if self._config.enabled:
            await self._apply_propagation(
                roots,
                pressure,
                processing_time,
                valid_time,
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
