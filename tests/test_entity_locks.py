# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Revok Contributors

from __future__ import annotations

import asyncio

import pytest

from revok.entity_locks import EntityLockRegistry


async def test_entity_lock_serializes_same_key() -> None:
    registry = EntityLockRegistry()
    order: list[str] = []

    async def worker(name: str) -> None:
        async with registry.acquire_entities(["a"]):
            order.append(f"{name}-start")
            await asyncio.sleep(0)
            order.append(f"{name}-end")

    await asyncio.gather(worker("first"), worker("second"))

    assert order == ["first-start", "first-end", "second-start", "second-end"]


async def test_unrelated_keys_do_not_block_each_other() -> None:
    registry = EntityLockRegistry()
    started = asyncio.Event()

    async def holder() -> None:
        async with registry.acquire_entities(["a"]):
            started.set()
            await asyncio.sleep(0.05)

    async def other() -> str:
        await started.wait()
        async with registry.acquire_entities(["b"]):
            return "acquired"

    _, result = await asyncio.gather(holder(), other())
    assert result == "acquired"


async def test_overlapping_key_sets_from_both_directions_do_not_deadlock() -> None:
    registry = EntityLockRegistry()

    async def worker(keys: list[str]) -> None:
        async with registry.acquire_entities(keys):
            await asyncio.sleep(0)

    # Opposite declaration order; sorted acquisition must prevent a cycle.
    await asyncio.wait_for(
        asyncio.gather(
            worker(["a", "b", "c"]),
            worker(["c", "b", "a"]),
            worker(["b", "c"]),
        ),
        timeout=2.0,
    )


async def test_entity_and_signal_namespaces_are_independent() -> None:
    registry = EntityLockRegistry()
    async with registry.acquire_entities(["same"]):
        async with registry.acquire_signal("same"):
            pass


async def test_locks_release_on_exception() -> None:
    registry = EntityLockRegistry()

    with pytest.raises(RuntimeError):
        async with registry.acquire_entities(["a", "b"]):
            raise RuntimeError("boom")

    async with registry.acquire_entities(["a", "b"]):
        pass
    assert registry.tracked_key_count == 0


async def test_registry_does_not_leak_keys() -> None:
    registry = EntityLockRegistry()
    for index in range(100):
        async with registry.acquire_signal(f"signal-{index}"):
            pass
    assert registry.tracked_key_count == 0


async def test_duplicate_keys_are_acquired_once() -> None:
    registry = EntityLockRegistry()
    async with registry.acquire_entities(["a", "a", "a"]):
        assert registry.tracked_key_count == 1
