# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Revok Contributors

"""Signal ingestion sources and the runners that drive them.

Every ingestion path implements ``SignalSource``. A runner consumes one source,
dispatches signals concurrently under a bound, and acknowledges each signal only
when its processing outcome permits it. Sources are independent: a failure in
one never stops another.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Iterable
from dataclasses import replace

from revok.interfaces import GraphReader, Resolver, SignalSource
from revok.models import Signal
from revok.resolver_runtime import DEFAULT_RESOLVER_TIMEOUT_SECONDS, invoke_resolver
from revok.signal_processor import SignalProcessor
from revok.signal_queue import AsyncioQueueBus

logger = logging.getLogger(__name__)


class HttpSignalSource:
    """``SignalSource`` over the in-process queue fed by ``POST /signals``.

    HTTP has no acknowledgment semantics, so ``ack`` is a no-op.
    """

    name = "http"

    def __init__(self, bus: AsyncioQueueBus) -> None:
        self._bus = bus

    async def receive(self) -> AsyncIterator[Signal]:
        """Yield signals published by the HTTP handler until cancelled."""
        while True:
            signal = await self._bus.consume()
            yield signal

    async def ack(self, signal: Signal) -> None:
        """No-op: the HTTP transport cannot advance a position."""
        return None

    async def close(self) -> None:
        """Close the underlying queue. Idempotent."""
        await self._bus.close()


class SourceRunner:
    """Drives one ``SignalSource``, dispatching signals under a concurrency bound."""

    def __init__(
        self,
        source: SignalSource,
        processor: SignalProcessor,
        *,
        name: str,
        max_concurrent: int = 16,
        resolver: Resolver | None = None,
        graph: GraphReader | None = None,
        resolver_timeout: float = DEFAULT_RESOLVER_TIMEOUT_SECONDS,
    ) -> None:
        self._source = source
        self._processor = processor
        self.name = name
        self._max_concurrent = max(1, max_concurrent)
        self._resolver = resolver
        self._graph = graph
        self._resolver_timeout = resolver_timeout

    async def run(self) -> None:
        """Consume the source until it is exhausted, cancelled, or fails."""
        semaphore = asyncio.Semaphore(self._max_concurrent)
        inflight: set[asyncio.Task[None]] = set()
        try:
            async for signal in self._source.receive():
                task = asyncio.create_task(self._handle(signal, semaphore))
                inflight.add(task)
                task.add_done_callback(inflight.discard)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error(
                "Signal source '%s' stopped with an error", self.name, exc_info=True
            )
        finally:
            if inflight:
                try:
                    await asyncio.gather(*list(inflight), return_exceptions=True)
                except asyncio.CancelledError:
                    pass

    async def _maybe_resolve(self, signal: Signal) -> Signal:
        """Resolve free text for sources that did not resolve at ingestion.

        The HTTP path resolves in the request handler so it can return an error
        status. Durable sources have no response to carry one, so a resolver
        failure here leaves the signal unacknowledged for redelivery.
        """
        if self._resolver is None or self._graph is None:
            return signal
        if signal.resolved_targets or not signal.signal_text:
            return signal
        try:
            body = json.loads(signal.original_body or b"{}")
            refs = body.get("entity_refs") if isinstance(body, dict) else None
        except (json.JSONDecodeError, ValueError):
            refs = None
        if refs:
            return signal

        validation = await invoke_resolver(
            self._resolver,
            signal.signal_text,
            self._graph,
            timeout_seconds=self._resolver_timeout,
        )
        return replace(
            signal,
            resolved_targets=tuple(validation.targets),
            dropped_targets=tuple(validation.dropped_targets),
        )

    async def _handle(self, signal: Signal, semaphore: asyncio.Semaphore) -> None:
        async with semaphore:
            try:
                resolved = await self._maybe_resolve(signal)
                outcome = await self._processor.process_one(resolved)
            except Exception:
                logger.error(
                    "Signal source '%s' failed processing signal '%s'",
                    self.name,
                    signal.signal_id,
                    exc_info=True,
                )
                return

            if not outcome.should_ack:
                logger.warning(
                    "Signal '%s' from source '%s' not acknowledged: %s",
                    outcome.signal_id,
                    self.name,
                    outcome.failure_reason,
                )
                return

            try:
                await self._source.ack(signal)
            except Exception:
                logger.error(
                    "Signal source '%s' failed acknowledging signal '%s'",
                    self.name,
                    outcome.signal_id,
                    exc_info=True,
                )

    async def close(self) -> None:
        """Close the underlying source."""
        await self._source.close()


class SourceSupervisor:
    """Runs every configured source concurrently with failure isolation."""

    def __init__(self, runners: Iterable[SourceRunner]) -> None:
        self._runners = list(runners)

    @property
    def runners(self) -> list[SourceRunner]:
        """Return the supervised runners."""
        return list(self._runners)

    async def run(self) -> None:
        """Run all sources until cancelled; one failing source never stops another."""
        if not self._runners:
            await asyncio.Event().wait()
            return

        results = await asyncio.gather(
            *(runner.run() for runner in self._runners), return_exceptions=True
        )
        for runner, result in zip(self._runners, results):
            if isinstance(result, BaseException) and not isinstance(
                result, asyncio.CancelledError
            ):
                logger.error(
                    "Signal source '%s' terminated with an error: %s",
                    runner.name,
                    result,
                )

    async def close(self) -> None:
        """Close every source, continuing past individual failures."""
        for runner in self._runners:
            try:
                await runner.close()
            except Exception:
                logger.error(
                    "Failed closing signal source '%s'", runner.name, exc_info=True
                )
