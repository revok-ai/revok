# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Revok Contributors
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""SQLite-backed state store with LRU hot layer for Revok entity records.

``SqliteStateStore`` implements the :class:`~revok.interfaces.StateStore`
Protocol using:
- **aiosqlite** for async WAL SQLite persistence (FR-007, FR-012).
- **collections.OrderedDict** as an in-process LRU hot layer (FR-008,
  research.md § 5 — no Redis in MVP).
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections import OrderedDict
from typing import TYPE_CHECKING

import aiosqlite

from revok.config import StateStoreConfig
from revok.models import EntityRecord

if TYPE_CHECKING:
    from revok.scoring import ScoringEngine

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS entity_records (
    entity_key   TEXT    PRIMARY KEY,
    score        REAL    NOT NULL DEFAULT 0.0,
    last_seen    REAL    NOT NULL,
    signal_count INTEGER NOT NULL DEFAULT 1,
    pattern_name TEXT    NOT NULL DEFAULT ''
);
"""


class SqliteStateStore:
    """Implements ``StateStore`` backed by SQLite WAL + LRU in-memory hot layer.

    Args:
        config: ``StateStoreConfig`` with ``sqlite_path`` and
            ``hot_layer_max_entries``.
    """

    def __init__(
        self,
        config: StateStoreConfig,
        scorer: ScoringEngine | None = None,
    ) -> None:
        self._config = config
        self._scorer = scorer
        self._db: aiosqlite.Connection | None = None
        self._hot: OrderedDict[str, EntityRecord] = OrderedDict()

    async def open(self) -> None:
        """Open the SQLite connection, apply WAL pragma, and create the table.

        Raises:
            RuntimeError: If the database file is corrupted (SC-009).
        """
        try:
            self._db = await aiosqlite.connect(self._config.sqlite_path)
            await self._db.execute("PRAGMA journal_mode=WAL;")
            await self._db.execute("PRAGMA synchronous=NORMAL;")
            await self._db.execute(_DDL)
            await self._db.commit()
        except sqlite3.DatabaseError as exc:
            logger.critical(
                "State store database at '%s' is corrupted or unreadable: %s. "
                "Delete or restore the file and restart.",
                self._config.sqlite_path,
                exc,
            )
            raise RuntimeError(
                f"State store database is corrupted at '{self._config.sqlite_path}'. "
                "Delete or restore the file and restart Revok."
            ) from exc
        except Exception as exc:
            logger.critical(
                "Failed to open state store at %s: %s", self._config.sqlite_path, exc
            )
            raise

    async def get(self, entity_key: str) -> EntityRecord | None:
        """Fetch the current record for *entity_key*.

        Checks the LRU hot layer first; falls back to SQLite on a miss.

        Args:
            entity_key: Normalized entity identifier.

        Returns:
            :class:`~revok.models.EntityRecord` if found, ``None`` otherwise.
        """
        # Hot layer hit
        if entity_key in self._hot:
            self._hot.move_to_end(entity_key)
            return self._apply_decay(self._hot[entity_key])

        # SQLite fallback
        assert self._db is not None, (
            "SqliteStateStore.open() must be called before get()"
        )
        async with self._db.execute(
            "SELECT entity_key, score, last_seen, signal_count, pattern_name "
            "FROM entity_records WHERE entity_key = ?",
            (entity_key,),
        ) as cursor:
            row = await cursor.fetchone()

        if row is None:
            return None

        record = EntityRecord(
            entity_key=row[0],
            score=row[1],
            last_seen=row[2],
            signal_count=row[3],
            pattern_name=row[4],
        )
        # Warm the hot layer with the raw (undecayed) record
        self._hot[entity_key] = record
        self._hot.move_to_end(entity_key)
        self._evict()
        return self._apply_decay(record)

    async def put(self, record: EntityRecord) -> None:
        """Persist *record* to SQLite and update the hot layer.

        Uses ``INSERT … ON CONFLICT … DO UPDATE`` (upsert) so existing records
        are overwritten atomically.

        Args:
            record: :class:`~revok.models.EntityRecord` to store.
        """
        assert self._db is not None, (
            "SqliteStateStore.open() must be called before put()"
        )
        await self._db.execute(
            "INSERT INTO entity_records "
            "  (entity_key, score, last_seen, signal_count, pattern_name) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(entity_key) DO UPDATE SET "
            "  score        = excluded.score, "
            "  last_seen    = excluded.last_seen, "
            "  signal_count = excluded.signal_count, "
            "  pattern_name = excluded.pattern_name",
            (
                record.entity_key,
                record.score,
                record.last_seen,
                record.signal_count,
                record.pattern_name,
            ),
        )
        await self._db.commit()

        # Update hot layer
        self._hot[record.entity_key] = record
        self._hot.move_to_end(record.entity_key)
        self._evict()

    def _apply_decay(self, record: EntityRecord) -> EntityRecord:
        """Return *record* with its score decayed to ``now`` if scorer is configured.

        The persisted/hot-layer record is never modified — a new
        :class:`~revok.models.EntityRecord` is returned with the decayed score.
        """
        if self._scorer is None:
            return record
        decayed = self._scorer.decay_at(record, time.time())
        return EntityRecord(
            entity_key=record.entity_key,
            score=decayed,
            last_seen=record.last_seen,
            signal_count=record.signal_count,
            pattern_name=record.pattern_name,
        )

    def _evict(self) -> None:
        """Remove the oldest entry when the hot layer exceeds its max capacity."""
        while len(self._hot) > self._config.hot_layer_max_entries:
            self._hot.popitem(last=False)

    async def list_all(self, offset: int = 0, limit: int = 100) -> list[EntityRecord]:
        """Return a paginated list of all stored entity records.

        Records are returned in ascending ``entity_key`` order with read-time
        decay applied when a scorer is configured.

        Args:
            offset: Number of records to skip.
            limit:  Maximum number of records to return (capped by caller).

        Returns:
            List of :class:`~revok.models.EntityRecord` with current scores.
        """
        assert self._db is not None, (
            "SqliteStateStore.open() must be called before list_all()"
        )
        async with self._db.execute(
            "SELECT entity_key, score, last_seen, signal_count, pattern_name "
            "FROM entity_records ORDER BY entity_key LIMIT ? OFFSET ?",
            (limit, offset),
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            self._apply_decay(
                EntityRecord(
                    entity_key=row[0],
                    score=row[1],
                    last_seen=row[2],
                    signal_count=row[3],
                    pattern_name=row[4],
                )
            )
            for row in rows
        ]

    async def delete(self, entity_key: str) -> bool:
        """Delete the record for an entity key.

        Removes from both the SQLite store and the hot-cache layer.

        Args:
            entity_key: Normalized entity identifier.

        Returns:
            True if a row was deleted, False if it did not exist.
        """
        assert self._db is not None, (
            "SqliteStateStore.open() must be called before delete()"
        )
        async with self._db.execute(
            "DELETE FROM entity_records WHERE entity_key = ?",
            (entity_key,),
        ) as cursor:
            deleted = cursor.rowcount > 0
        await self._db.commit()
        self._hot.pop(entity_key, None)
        return deleted

    async def close(self) -> None:
        """Close the SQLite connection. Idempotent."""
        if self._db is not None:
            await self._db.close()
            self._db = None
