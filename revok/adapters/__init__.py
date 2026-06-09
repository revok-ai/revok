# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Revok Contributors
"""Upstream memory-backend adapters (Mem0, Zep CE).

To add a new adapter:
1. Create ``revok/adapters/<name>.py`` implementing ``MemoryAdapter`` protocol
   plus ``is_write_request()`` and ``extract_signal_context()`` classmethods.
2. Register it in ``_REGISTRY`` below.  No other file needs to change.
"""
from __future__ import annotations

import aiohttp

from revok.adapters.mem0 import Mem0Adapter
from revok.adapters.zep import ZepAdapter
from revok.config import UpstreamConfig
from revok.interfaces import MemoryAdapter

# Registry maps adapter_type string → adapter class.
# build_app() resolves the class at startup; _handle() never branches on type.
_REGISTRY: dict[str, type] = {
    "mem0": Mem0Adapter,
    "zep": ZepAdapter,
}


def build_adapter(
    adapter_type: str,
    config: UpstreamConfig,
    session: aiohttp.ClientSession,
) -> MemoryAdapter:
    """Instantiate the adapter registered under *adapter_type*.

    Args:
        adapter_type: Key from ``_REGISTRY`` (e.g. ``"mem0"``, ``"zep"``).
        config: Upstream URL and write-detection settings.
        session: aiohttp client session for upstream requests.

    Returns:
        A :class:`~revok.interfaces.MemoryAdapter` instance.

    Raises:
        ValueError: If *adapter_type* is not in ``_REGISTRY``.
    """
    cls = _REGISTRY.get(adapter_type)
    if cls is None:
        raise ValueError(
            f"Unknown adapter_type: {adapter_type!r}. "
            f"Available: {sorted(_REGISTRY)}"
        )
    return cls(config, session)  # type: ignore[return-value]


__all__ = ["Mem0Adapter", "ZepAdapter", "build_adapter", "_REGISTRY"]
