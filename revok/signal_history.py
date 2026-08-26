"""SQLite-backed implementation of the SignalHistoryStore protocol.

Persists signal events in a ``signal_history`` table within the existing
revok SQLite WAL database.  Enabled via ``inspector.signal_history.enabled``
in the revok configuration.
"""
from __future__ import annotations

import aiosqlite

from revok.models import SignalRecord

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

    async def open(self) -> None:
        """Open the database connection and create the schema if needed."""
        self._conn = await aiosqlite.connect(self._db_path)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute(_CREATE_TABLE)
        await self._conn.execute(_CREATE_INDEX)
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

    async def close(self) -> None:
        """Flush and close the database connection.  Idempotent."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
