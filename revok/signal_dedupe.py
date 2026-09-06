# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Revok Contributors

"""Deduplication and dead-letter persistence for signal ingestion.

At-least-once delivery means a signal can arrive more than once. Applying it
twice would drop confidence further than the event warranted, so processing is
guarded by a durable record of completed signal identifiers.

This store is deliberately independent of trace storage. Trace recording is
gated by ``inspector.signal_history_enabled``; reusing it here would let
disabling tracing silently disable idempotency.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import cast

import aiosqlite

from revok.models import DeadLetterRecord, DedupeRecord

logger = logging.getLogger(__name__)

_TRIM_INTERVAL = 100

_CREATE_DEDUPE_TABLE = """
CREATE TABLE IF NOT EXISTS signal_dedupe (
    signal_id    TEXT PRIMARY KEY,
    processed_at REAL NOT NULL,
    source_name  TEXT NOT NULL,
    entity_keys  TEXT NOT NULL
)
"""

_CREATE_DEDUPE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_signal_dedupe_processed
    ON signal_dedupe (processed_at DESC, signal_id DESC)
"""

_CREATE_DEAD_LETTER_TABLE = """
CREATE TABLE IF NOT EXISTS signal_dead_letters (
    signal_id         TEXT PRIMARY KEY,
    source_name       TEXT NOT NULL,
    payload           TEXT NOT NULL,
    delivery_attempts INTEGER NOT NULL,
    failure_reason    TEXT NOT NULL,
    dead_lettered_at  REAL NOT NULL
)
"""

_CREATE_DEAD_LETTER_INDEX = """
CREATE INDEX IF NOT EXISTS idx_signal_dead_letters_time
    ON signal_dead_letters (dead_lettered_at DESC, signal_id DESC)
"""

_SELECT_DEAD_LETTER_COLUMNS = """
SELECT signal_id, source_name, payload, delivery_attempts,
       failure_reason, dead_lettered_at
FROM signal_dead_letters
"""


class SqliteIngestionStore:
    """SQLite-backed deduplication and dead-letter storage.

    Args:
        db_path: Path to the SQLite database file, shared with the state store.
        dedupe_max_rows: Instance-wide deduplication row bound, oldest evicted
            first. No time-based expiry: a slow restart or a stream backlog
            would silently exceed any chosen window.
    """

    def __init__(self, db_path: str, dedupe_max_rows: int = 100_000) -> None:
        if dedupe_max_rows <= 0:
            raise ValueError("dedupe_max_rows must be greater than 0")
        self._db_path = db_path
        self._dedupe_max_rows = dedupe_max_rows
        self._conn: aiosqlite.Connection | None = None
        self._writes_since_trim = 0

    async def open(self) -> None:
        """Open the connection and create both tables if needed."""
        self._conn = await aiosqlite.connect(self._db_path)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute(_CREATE_DEDUPE_TABLE)
        await self._conn.execute(_CREATE_DEDUPE_INDEX)
        await self._conn.execute(_CREATE_DEAD_LETTER_TABLE)
        await self._conn.execute(_CREATE_DEAD_LETTER_INDEX)
        await self._conn.commit()

    def _require_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("SqliteIngestionStore is not open")
        return self._conn

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    async def has(self, signal_id: str) -> bool:
        """Return ``True`` if *signal_id* was already fully processed."""
        conn = self._require_conn()
        async with conn.execute(
            "SELECT 1 FROM signal_dedupe WHERE signal_id = ?", (signal_id,)
        ) as cursor:
            return await cursor.fetchone() is not None

    async def record(self, entry: DedupeRecord) -> None:
        """Persist *entry*, trimming periodically rather than on every write."""
        conn = self._require_conn()
        await conn.execute(
            """
INSERT OR REPLACE INTO signal_dedupe
    (signal_id, processed_at, source_name, entity_keys)
VALUES (?, ?, ?, ?)
""",
            (
                entry.signal_id,
                entry.processed_at,
                entry.source_name,
                json.dumps(list(entry.entity_keys)),
            ),
        )
        await conn.commit()

        self._writes_since_trim += 1
        if self._writes_since_trim >= _TRIM_INTERVAL:
            await self.trim_dedupe()

    async def trim_dedupe(self) -> None:
        """Evict deduplication rows beyond the configured bound, oldest first."""
        conn = self._require_conn()
        await conn.execute(
            """
DELETE FROM signal_dedupe
WHERE signal_id NOT IN (
    SELECT signal_id
    FROM signal_dedupe
    ORDER BY processed_at DESC, signal_id DESC
    LIMIT ?
)
""",
            (self._dedupe_max_rows,),
        )
        await conn.commit()
        self._writes_since_trim = 0

    async def dedupe_count(self) -> int:
        """Return the number of retained deduplication rows."""
        conn = self._require_conn()
        async with conn.execute("SELECT COUNT(*) FROM signal_dedupe") as cursor:
            row = await cursor.fetchone()
        return int(row[0]) if row is not None else 0

    # ------------------------------------------------------------------
    # Dead letters
    # ------------------------------------------------------------------

    @staticmethod
    def _dead_letter_from_row(row: Sequence[object]) -> DeadLetterRecord:
        return DeadLetterRecord(
            signal_id=str(row[0]),
            source_name=str(row[1]),
            payload=str(row[2]),
            delivery_attempts=int(cast(int, row[3])),
            failure_reason=str(row[4]),
            dead_lettered_at=float(cast(float, row[5])),
        )

    async def dead_letter(self, entry: DeadLetterRecord) -> None:
        """Persist *entry* before the originating source acknowledges it."""
        conn = self._require_conn()
        await conn.execute(
            """
INSERT OR REPLACE INTO signal_dead_letters
    (signal_id, source_name, payload, delivery_attempts,
     failure_reason, dead_lettered_at)
VALUES (?, ?, ?, ?, ?, ?)
""",
            (
                entry.signal_id,
                entry.source_name,
                entry.payload,
                entry.delivery_attempts,
                entry.failure_reason,
                entry.dead_lettered_at,
            ),
        )
        await conn.commit()
        logger.warning(
            "Signal '%s' from source '%s' dead-lettered after %d attempts: %s",
            entry.signal_id,
            entry.source_name,
            entry.delivery_attempts,
            entry.failure_reason,
        )

    async def get_dead_letter(self, signal_id: str) -> DeadLetterRecord | None:
        """Return one dead-letter record, or ``None`` when unknown."""
        conn = self._require_conn()
        async with conn.execute(
            f"{_SELECT_DEAD_LETTER_COLUMNS} WHERE signal_id = ?", (signal_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return self._dead_letter_from_row(cast(Sequence[object], row))

    async def list_dead_letters(
        self, offset: int = 0, limit: int = 100
    ) -> list[DeadLetterRecord]:
        """Return dead-letter records newest first."""
        conn = self._require_conn()
        if offset < 0 or limit <= 0:
            raise ValueError("offset must be >= 0 and limit must be > 0")
        async with conn.execute(
            f"""
{_SELECT_DEAD_LETTER_COLUMNS}
ORDER BY dead_lettered_at DESC, signal_id DESC
LIMIT ? OFFSET ?
""",
            (limit, offset),
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._dead_letter_from_row(cast(Sequence[object], r)) for r in rows]

    async def close(self) -> None:
        """Flush and close the connection. Idempotent."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
