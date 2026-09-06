# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Revok Contributors

"""Durable signal source backed by Redis Streams consumer groups.

Optional extra — requires ``redis``::

    pip install 'revok[redis]'

Importing this module is always safe; the ImportError is deferred to
construction so a default install is unaffected.

Delivery is at-least-once: entries stay in the consumer group's pending list
until acknowledged, so an abrupt stop redelivers them. Redis maintains a
per-entry delivery counter, which is used directly as the attempt bound rather
than tracking attempts separately.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from revok.config import RedisStreamsSourceConfig
from revok.interfaces import DeadLetterStore
from revok.models import DeadLetterRecord, Signal

logger = logging.getLogger(__name__)

try:
    import redis.asyncio as _redis

    _REDIS_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only without the extra
    _REDIS_AVAILABLE = False

_MISSING_EXTRA = (
    "The Redis Streams signal source requires the 'redis' extra. "
    "Install it with: pip install 'revok[redis]'"
)


class RedisStreamsSignalSource:
    """``SignalSource`` delivering durable signals from a Redis stream.

    Args:
        config: Durable source settings.
        dead_letters: Store receiving signals that exceed the attempt bound.
        client: Pre-built async Redis client. Injected by tests; when omitted a
            client is created from ``config.url``.
    """

    name = "redis_streams"

    def __init__(
        self,
        config: RedisStreamsSourceConfig,
        dead_letters: DeadLetterStore | None = None,
        *,
        client: Any | None = None,
    ) -> None:
        if client is None:
            if not _REDIS_AVAILABLE:
                raise ImportError(_MISSING_EXTRA)
            client = _redis.from_url(config.url, decode_responses=True)
            self._owns_client = True
        else:
            self._owns_client = False
        self._client = client
        self._config = config
        self._dead_letters = dead_letters
        self._pending: dict[str, str] = {}
        self._closed = False

    async def _ensure_group(self) -> None:
        try:
            await self._client.xgroup_create(
                self._config.stream,
                self._config.consumer_group,
                id="0",
                mkstream=True,
            )
        except Exception as exc:  # noqa: BLE001 - client-specific BUSYGROUP error
            if "BUSYGROUP" not in str(exc):
                raise

    @staticmethod
    def _flatten(response: Any) -> list[tuple[str, dict[str, str]]]:
        """Normalize an ``xreadgroup`` response into (message id, fields) pairs."""
        entries: list[tuple[str, dict[str, str]]] = []
        if not response:
            return entries
        for _stream, messages in response:
            for message_id, fields in messages:
                entries.append((str(message_id), dict(fields)))
        return entries

    async def _delivery_count(self, message_id: str) -> int:
        try:
            pending = await self._client.xpending_range(
                self._config.stream,
                self._config.consumer_group,
                min=message_id,
                max=message_id,
                count=1,
            )
        except Exception:
            logger.error(
                "Failed reading delivery count for '%s'", message_id, exc_info=True
            )
            return 1
        if not pending:
            return 1
        entry = pending[0]
        times = entry.get("times_delivered") if isinstance(entry, dict) else None
        return int(times) if times is not None else 1

    async def _set_aside(
        self,
        message_id: str,
        payload: str,
        attempts: int,
        reason: str,
        signal_id: str,
    ) -> None:
        """Record a dead letter, then acknowledge so the stream advances."""
        if self._dead_letters is not None:
            await self._dead_letters.dead_letter(
                DeadLetterRecord(
                    signal_id=signal_id or message_id,
                    source_name=self.name,
                    payload=payload,
                    delivery_attempts=attempts,
                    failure_reason=reason,
                    dead_lettered_at=time.time(),
                )
            )
        else:
            logger.warning(
                "Signal '%s' exceeded %d attempts with no dead-letter store: %s",
                signal_id or message_id,
                attempts,
                reason,
            )
        await self._client.xack(
            self._config.stream, self._config.consumer_group, message_id
        )

    def _build_signal(self, payload: str) -> Signal:
        body = json.loads(payload)
        if not isinstance(body, dict):
            raise ValueError("stream entry payload must be a JSON object")
        entity_refs = body.get("entity_refs") or []
        if not isinstance(entity_refs, list):
            raise ValueError("entity_refs must be a list when present")
        signal_text = str(body.get("signal_text") or "").strip()
        raw_content = " ".join(str(r) for r in entity_refs) if entity_refs else signal_text
        return Signal(
            raw_content=raw_content,
            source_id=str(body.get("source") or self.name),
            timestamp=float(body.get("timestamp") or time.time()),
            http_method="STREAM",
            http_path=f"/{self._config.stream}",
            original_body=payload.encode("utf-8"),
            headers={},
            signal_id=str(body.get("signal_id") or uuid.uuid4()),
            signal_text=signal_text or None,
        )

    async def _prepare(
        self, message_id: str, fields: dict[str, str], *, from_backlog: bool
    ) -> Signal | None:
        payload = fields.get("payload")
        if payload is None:
            payload = json.dumps(fields)

        attempts = await self._delivery_count(message_id) if from_backlog else 1
        if attempts > self._config.max_delivery_attempts:
            await self._set_aside(
                message_id,
                payload,
                attempts,
                f"exceeded {self._config.max_delivery_attempts} delivery attempts",
                signal_id="",
            )
            return None

        try:
            signal = self._build_signal(payload)
        except Exception as exc:  # noqa: BLE001 - malformed entries must not crash
            await self._set_aside(
                message_id, payload, attempts, f"unparseable entry: {exc}", signal_id=""
            )
            return None

        self._pending[signal.signal_id or message_id] = message_id
        return signal

    async def receive(self) -> AsyncIterator[Signal]:
        """Yield pending entries first, then newly published ones."""
        await self._ensure_group()

        # Pending entries first: this is what makes a crash redeliver. The cursor
        # advances past each batch; re-reading from "0" would loop forever on an
        # entry that is yielded but not yet acknowledged.
        cursor = "0"
        while not self._closed:
            response = await self._client.xreadgroup(
                self._config.consumer_group,
                self._config.consumer_name,
                {self._config.stream: cursor},
                count=self._config.batch_size,
            )
            entries = self._flatten(response)
            if not entries:
                break
            for message_id, fields in entries:
                signal = await self._prepare(message_id, fields, from_backlog=True)
                if signal is not None:
                    yield signal
            cursor = entries[-1][0]

        while not self._closed:
            response = await self._client.xreadgroup(
                self._config.consumer_group,
                self._config.consumer_name,
                {self._config.stream: ">"},
                count=self._config.batch_size,
                block=self._config.block_ms,
            )
            entries = self._flatten(response)
            if not entries:
                # Yield control for clients that do not block server-side.
                await asyncio.sleep(0.01)
                continue
            for message_id, fields in entries:
                signal = await self._prepare(message_id, fields, from_backlog=False)
                if signal is not None:
                    yield signal

    async def ack(self, signal: Signal) -> None:
        """Acknowledge the stream entry backing *signal*."""
        message_id = self._pending.pop(signal.signal_id or "", None)
        if message_id is None:
            return
        await self._client.xack(
            self._config.stream, self._config.consumer_group, message_id
        )

    async def close(self) -> None:
        """Stop consuming and release the client when this source owns it."""
        self._closed = True
        if self._owns_client:
            aclose = getattr(self._client, "aclose", None)
            if aclose is not None:
                await aclose()
