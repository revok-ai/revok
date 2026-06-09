# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Revok Contributors
#
# Tests for the adapter registry and factory (adapters/__init__.py)
# and Mem0Adapter classmethods (is_write_request, extract_signal_context).

from __future__ import annotations

from unittest.mock import MagicMock

import aiohttp
import pytest

from revok.adapters import _REGISTRY, build_adapter
from revok.adapters.mem0 import Mem0Adapter
from revok.adapters.zep import ZepAdapter
from revok.config import UpstreamConfig
from revok.interfaces import MemoryAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _upstream(write_methods: list[str] | None = None, write_paths: list[str] | None = None) -> UpstreamConfig:
    return UpstreamConfig(
        url="http://localhost:8000",
        write_methods=write_methods or ["POST"],
        write_paths=write_paths or ["/v1/memories"],
    )


# ---------------------------------------------------------------------------
# build_adapter factory
# ---------------------------------------------------------------------------


def test_build_adapter_mem0_returns_mem0_adapter() -> None:
    """build_adapter('mem0', ...) returns a Mem0Adapter instance."""
    session = MagicMock(spec=aiohttp.ClientSession)
    adapter = build_adapter("mem0", _upstream(), session)
    assert isinstance(adapter, Mem0Adapter)


def test_build_adapter_zep_returns_zep_adapter() -> None:
    """build_adapter('zep', ...) returns a ZepAdapter instance."""
    session = MagicMock(spec=aiohttp.ClientSession)
    adapter = build_adapter("zep", _upstream(), session)
    assert isinstance(adapter, ZepAdapter)


def test_build_adapter_satisfies_memory_adapter_protocol() -> None:
    """Both adapters satisfy the MemoryAdapter Protocol at runtime."""
    session = MagicMock(spec=aiohttp.ClientSession)
    for key in _REGISTRY:
        adapter = build_adapter(key, _upstream(), session)
        assert isinstance(adapter, MemoryAdapter), f"{key} adapter does not satisfy MemoryAdapter"


def test_build_adapter_unknown_type_raises_value_error() -> None:
    """build_adapter raises ValueError for an unregistered adapter_type."""
    session = MagicMock(spec=aiohttp.ClientSession)
    with pytest.raises(ValueError, match="Unknown adapter_type"):
        build_adapter("langmem", _upstream(), session)


def test_registry_contains_mem0_and_zep() -> None:
    """_REGISTRY exposes at least mem0 and zep keys."""
    assert "mem0" in _REGISTRY
    assert "zep" in _REGISTRY


# ---------------------------------------------------------------------------
# Mem0Adapter.is_write_request
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path,expected",
    [
        ("POST", "/v1/memories", True),
        ("POST", "/v1/memories/extra", True),   # path prefix match
        ("post", "/v1/memories", True),          # method case-insensitive
        ("GET",  "/v1/memories", False),          # wrong method
        ("POST", "/v1/other",    False),          # wrong path
        ("DELETE", "/v1/memories", False),        # method not in write_methods
    ],
)
def test_mem0_is_write_request(method: str, path: str, expected: bool) -> None:
    config = _upstream(write_methods=["POST"], write_paths=["/v1/memories"])
    assert Mem0Adapter.is_write_request(method, path, config) is expected


# ---------------------------------------------------------------------------
# Mem0Adapter.extract_signal_context
# ---------------------------------------------------------------------------


def test_mem0_extract_signal_context_uses_x_agent_id() -> None:
    """source_id comes from X-Agent-ID header."""
    source_id, raw_content = Mem0Adapter.extract_signal_context(
        "/v1/memories",
        {"X-Agent-ID": "agent-42"},
        b'{"content": "Alice visited"}',
    )
    assert source_id == "agent-42"
    assert raw_content == '{"content": "Alice visited"}'


def test_mem0_extract_signal_context_defaults_source_id() -> None:
    """source_id defaults to 'unknown' when X-Agent-ID is absent."""
    source_id, _ = Mem0Adapter.extract_signal_context("/v1/memories", {}, b"hello")
    assert source_id == "unknown"


def test_mem0_extract_signal_context_raw_body_decoded() -> None:
    """raw_content is the UTF-8 decoded request body."""
    _, raw_content = Mem0Adapter.extract_signal_context(
        "/v1/memories", {}, b"some raw text"
    )
    assert raw_content == "some raw text"
