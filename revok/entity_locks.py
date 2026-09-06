# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Revok Contributors

"""Keyed async locks providing the per-entity serialization boundary.

Concurrent signal processing performs read-modify-write on entity records with
suspension points in between, so two signals touching the same entity can lose
an update. Callers hold a lock per affected entity for the duration of the
read-modify-write.

Multi-key acquisition is always performed in sorted order. That canonical
ordering is what prevents deadlock between two signals whose entity sets
overlap in opposite directions.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field

_ENTITY_NAMESPACE = "entity:"
_SIGNAL_NAMESPACE = "signal:"


@dataclass
class _LockEntry:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    waiters: int = 0


class EntityLockRegistry:
    """Registry of per-key async locks with canonical multi-key acquisition.

    Entity keys and signal identifiers occupy separate namespaces, so an entity
    named like a signal identifier cannot collide with it.
    """

    def __init__(self) -> None:
        self._entries: dict[str, _LockEntry] = {}

    def _acquire_entry(self, key: str) -> _LockEntry:
        entry = self._entries.get(key)
        if entry is None:
            entry = _LockEntry()
            self._entries[key] = entry
        # Counted before awaiting so the entry is not evicted while we wait.
        entry.waiters += 1
        return entry

    def _release_entry(self, key: str, entry: _LockEntry) -> None:
        entry.waiters -= 1
        if entry.waiters <= 0:
            self._entries.pop(key, None)

    @asynccontextmanager
    async def _acquire_keys(self, keys: Iterable[str]) -> AsyncIterator[None]:
        ordered = sorted(set(keys))
        held: list[tuple[str, _LockEntry]] = []
        try:
            for key in ordered:
                entry = self._acquire_entry(key)
                held.append((key, entry))
                await entry.lock.acquire()
            yield
        finally:
            for key, entry in reversed(held):
                if entry.lock.locked():
                    entry.lock.release()
                self._release_entry(key, entry)

    def acquire_entities(
        self, entity_keys: Iterable[str]
    ) -> AbstractAsyncContextManager[None]:
        """Acquire locks for every entity key, in sorted order."""
        return self._acquire_keys(f"{_ENTITY_NAMESPACE}{k}" for k in entity_keys)

    def acquire_signal(self, signal_id: str) -> AbstractAsyncContextManager[None]:
        """Acquire the lock guarding one signal identifier."""
        return self._acquire_keys([f"{_SIGNAL_NAMESPACE}{signal_id}"])

    @property
    def tracked_key_count(self) -> int:
        """Number of keys currently held or awaited; zero when fully idle."""
        return len(self._entries)
