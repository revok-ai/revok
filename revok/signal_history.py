"""SQLite-backed implementation of the SignalHistoryStore protocol.

Persists signal events in a ``signal_history`` table within the existing
revok SQLite WAL database.  Enabled via ``inspector.signal_history.enabled``
in the revok configuration.
"""
from __future__ import annotations

import dataclasses
import json
from collections.abc import Sequence
from typing import cast

import aiosqlite

from revok.models import (
    PropagationStep,
    PropagationTrace,
    ResolutionTrace,
    ResolvedTarget,
    DroppedTarget,
    SignalRecord,
)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS signal_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_key      TEXT    NOT NULL,
    source_id       TEXT    NOT NULL,
    processed_at    REAL    NOT NULL,
    score_before    REAL,
    score_after     REAL    NOT NULL,
    is_propagated   INTEGER NOT NULL DEFAULT 0,
    upstream_source TEXT
)
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_signal_history_entity
    ON signal_history (entity_key, processed_at DESC)
"""

_INSERT = """
INSERT INTO signal_history
    (entity_key, source_id, processed_at, score_before, score_after,
     is_propagated, upstream_source)
VALUES (?, ?, ?, ?, ?, ?, ?)
"""

_SELECT_FOR_ENTITY = """
SELECT id, entity_key, source_id, processed_at, score_before, score_after,
       is_propagated, upstream_source
FROM   signal_history
WHERE  entity_key = ?
ORDER  BY processed_at DESC
"""

_CREATE_TRACE_TABLE = """
CREATE TABLE IF NOT EXISTS resolver_traces (
    signal_id          TEXT PRIMARY KEY,
    created_at         REAL NOT NULL,
    completed_at       REAL,
    source_id          TEXT NOT NULL,
    signal_text        TEXT NOT NULL,
    status             TEXT NOT NULL,
    error_code         TEXT,
    error_detail       TEXT,
    targets_json       TEXT NOT NULL,
    dropped_targets_json TEXT NOT NULL,
    invalidations_json TEXT NOT NULL,
    propagation_json   TEXT NOT NULL
)
"""

_CREATE_TRACE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_resolver_traces_created
    ON resolver_traces (created_at DESC, signal_id DESC)
"""


class SqliteSignalHistoryStore:
    """SQLite-backed signal event log implementing :class:`SignalHistoryStore`.

    Args:
        db_path: Path to the SQLite database file (shared with the WAL store).
        max_rows: Maximum number of rows to retain per entity.
    """

    def __init__(self, db_path: str, max_rows: int = 10_000) -> None:
        self._db_path = db_path
        self._max_rows = max_rows
        self._conn: aiosqlite.Connection | None = None
        self._trace_writes = 0

    async def open(self) -> None:
        """Open the database connection and create the schema if needed."""
        self._conn = await aiosqlite.connect(self._db_path)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute(_CREATE_TABLE)
        await self._conn.execute(_CREATE_INDEX)
        await self._conn.execute(_CREATE_TRACE_TABLE)
        columns = await self._conn.execute_fetchall("PRAGMA table_info(resolver_traces)")
        if not any(row[1] == "dropped_targets_json" for row in columns):
            await self._conn.execute(
                "ALTER TABLE resolver_traces ADD COLUMN dropped_targets_json TEXT NOT NULL DEFAULT '[]'"
            )
        await self._conn.execute(_CREATE_TRACE_INDEX)
        await self._conn.commit()

    async def record(self, event: SignalRecord) -> None:
        """Persist *event* to the signal history table.

        The ``event.id`` field is ignored; the database assigns the row id.

        Args:
            event: Signal event to persist.
        """
        if self._conn is None:
            raise RuntimeError("SqliteSignalHistoryStore is not open")
        await self._conn.execute(
            _INSERT,
            (
                event.entity_key,
                event.source_id,
                event.processed_at,
                event.score_before,
                event.score_after,
                int(event.is_propagated),
                event.upstream_source,
            ),
        )
        await self._conn.commit()
        await self.trim_for_entity(event.entity_key, self._max_rows)

    async def get_for_entity(self, entity_key: str) -> list[SignalRecord]:
        """Return all signal records for *entity_key* ordered by recency.

        Args:
            entity_key: Normalized entity identifier.

        Returns:
            List of :class:`SignalRecord` instances, newest first.
        """
        if self._conn is None:
            raise RuntimeError("SqliteSignalHistoryStore is not open")
        async with self._conn.execute(_SELECT_FOR_ENTITY, (entity_key,)) as cursor:
            rows = await cursor.fetchall()
        return [
            SignalRecord(
                id=row[0],
                entity_key=row[1],
                source_id=row[2],
                processed_at=row[3],
                score_before=row[4],
                score_after=row[5],
                is_propagated=bool(row[6]),
                upstream_source=row[7],
            )
            for row in rows
        ]

    async def trim_for_entity(self, entity_key: str, max_rows: int) -> None:
        """Trim history for *entity_key* to the newest *max_rows* entries."""
        if self._conn is None:
            raise RuntimeError("SqliteSignalHistoryStore is not open")
        if max_rows <= 0:
            raise ValueError("max_rows must be greater than 0")

        await self._conn.execute(
            """
DELETE FROM signal_history
WHERE id NOT IN (
    SELECT id
    FROM signal_history
    WHERE entity_key = ?
    ORDER BY processed_at DESC, id DESC
    LIMIT ?
)
AND entity_key = ?
""",
            (entity_key, max_rows, entity_key),
        )
        await self._conn.commit()

    @staticmethod
    def _trace_json(trace: ResolutionTrace) -> tuple[str, str, str, str]:
        return (
            json.dumps([dataclasses.asdict(target) for target in trace.targets]),
            json.dumps([dataclasses.asdict(target) for target in trace.dropped_targets]),
            json.dumps([dataclasses.asdict(event) for event in trace.invalidations]),
            json.dumps([dataclasses.asdict(item) for item in trace.propagation]),
        )

    @staticmethod
    def _trace_from_row(row: Sequence[object]) -> ResolutionTrace:
        targets = [ResolvedTarget(**item) for item in json.loads(str(row[8]))]
        dropped_targets = [DroppedTarget(**item) for item in json.loads(str(row[9]))]
        invalidations = [SignalRecord(**item) for item in json.loads(str(row[10]))]
        propagation: list[PropagationTrace] = []
        for item in json.loads(str(row[11])):
            steps = [PropagationStep(**step) for step in item["steps"]]
            propagation.append(
                PropagationTrace(
                    root_entity_key=item["root_entity_key"],
                    initial_pressure=item["initial_pressure"],
                    steps=steps,
                    termination_reason=item["termination_reason"],
                )
            )
        return ResolutionTrace(
            signal_id=str(row[0]),
            created_at=float(cast(float, row[1])),
            completed_at=float(cast(float, row[2])) if row[2] is not None else None,
            source_id=str(row[3]),
            signal_text=str(row[4]),
            status=str(row[5]),
            error_code=str(row[6]) if row[6] is not None else None,
            error_detail=str(row[7]) if row[7] is not None else None,
            targets=targets,
            dropped_targets=dropped_targets,
            invalidations=invalidations,
            propagation=propagation,
        )

    async def start_trace(self, trace: ResolutionTrace) -> None:
        """Persist a pending resolver trace."""
        if self._conn is None:
            raise RuntimeError("SqliteSignalHistoryStore is not open")
        targets_json, dropped_targets_json, invalidations_json, propagation_json = self._trace_json(trace)
        await self._conn.execute(
            """
INSERT OR REPLACE INTO resolver_traces
    (signal_id, created_at, completed_at, source_id, signal_text, status,
    error_code, error_detail, targets_json, dropped_targets_json,
    invalidations_json, propagation_json)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
""",
            (
                trace.signal_id,
                trace.created_at,
                trace.completed_at,
                trace.source_id,
                trace.signal_text,
                trace.status,
                trace.error_code,
                trace.error_detail,
                targets_json,
                dropped_targets_json,
                invalidations_json,
                propagation_json,
            ),
        )
        await self._conn.commit()
        self._trace_writes += 1
        if self._trace_writes >= 100:
            await self._trim_traces(self._max_rows)
            self._trace_writes = 0

    async def finish_trace(self, trace: ResolutionTrace) -> None:
        """Persist the final state of an existing resolver trace."""
        await self.start_trace(trace)

    async def get_trace(self, signal_id: str) -> ResolutionTrace | None:
        """Return one resolver trace by signal ID."""
        if self._conn is None:
            raise RuntimeError("SqliteSignalHistoryStore is not open")
        async with self._conn.execute(
            """
SELECT signal_id, created_at, completed_at, source_id, signal_text, status,
       error_code, error_detail, targets_json, dropped_targets_json,
       invalidations_json, propagation_json
FROM resolver_traces
WHERE signal_id = ?
""",
            (signal_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return self._trace_from_row(cast(Sequence[object], row)) if row is not None else None

    async def list_traces(self, offset: int = 0, limit: int = 100) -> list[ResolutionTrace]:
        """Return resolver traces newest first."""
        if self._conn is None:
            raise RuntimeError("SqliteSignalHistoryStore is not open")
        if offset < 0 or limit <= 0:
            raise ValueError("offset must be >= 0 and limit must be > 0")
        if self._trace_writes:
            await self._trim_traces(self._max_rows)
            self._trace_writes = 0
        async with self._conn.execute(
            """
SELECT signal_id, created_at, completed_at, source_id, signal_text, status,
       error_code, error_detail, targets_json, dropped_targets_json,
       invalidations_json, propagation_json
FROM resolver_traces
ORDER BY created_at DESC, signal_id DESC
LIMIT ? OFFSET ?
""",
            (limit, offset),
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._trace_from_row(cast(Sequence[object], row)) for row in rows]

    async def _trim_traces(self, max_rows: int) -> None:
        if self._conn is None:
            raise RuntimeError("SqliteSignalHistoryStore is not open")
        if max_rows <= 0:
            raise ValueError("max_rows must be greater than 0")
        await self._conn.execute(
            """
DELETE FROM resolver_traces
WHERE signal_id NOT IN (
    SELECT signal_id
    FROM resolver_traces
    ORDER BY created_at DESC, signal_id DESC
    LIMIT ?
)
""",
            (max_rows,),
        )
        await self._conn.commit()

    async def close(self) -> None:
        """Flush and close the database connection.  Idempotent."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
