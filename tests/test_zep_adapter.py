# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Revok Contributors
#
# Unit tests for revok.adapters.zep — _extract_session_id() and ZepAdapter.

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from revok.adapters.zep import ZepAdapter, _extract_session_id
from revok.config import UpstreamConfig
from revok.interfaces import MemoryAdapter
from revok.models import EnrichedPayload, Signal


# ---------------------------------------------------------------------------
# T014: _extract_session_id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/api/v1/sessions/abc123/memory", "abc123"),
        (
            "/api/v1/sessions/550e8400-e29b-41d4-a716-446655440000/memory",
            "550e8400-e29b-41d4-a716-446655440000",
        ),
        ("/api/v1/sessions/abc/memory?foo=bar", "abc"),  # query string stripped
    ],
)
def test_extract_session_id_valid(path: str, expected: str) -> None:
    """Valid session-memory paths return the session ID segment."""
    assert _extract_session_id(path) == expected


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/sessions//memory",  # empty segment
        "/api/v1/sessions/abc/memory/extra",  # extra suffix
        "/api/v1/sessions/abc/memories",  # wrong endpoint name
        "/api/v2/sessions/abc/memory",  # wrong API version
        "/api/v1/sessions/abc",  # missing /memory tail
        "",  # empty string
        "/api/v1/sessions/abc/memory/",  # trailing slash
    ],
)
def test_extract_session_id_no_match(path: str) -> None:
    """Non-matching paths return None."""
    assert _extract_session_id(path) is None


# ---------------------------------------------------------------------------
# T014: ZepAdapter Protocol structural check
# ---------------------------------------------------------------------------


def test_zep_adapter_implements_memory_adapter_protocol() -> None:
    """ZepAdapter satisfies the MemoryAdapter Protocol at runtime."""
    config = UpstreamConfig(url="http://localhost:8001", write_methods=["POST"])
    mock_session = MagicMock(spec=aiohttp.ClientSession)
    adapter = ZepAdapter(config, mock_session)
    assert isinstance(adapter, MemoryAdapter)


# ---------------------------------------------------------------------------
# T019: write() returns 502 on ClientConnectorError
# ---------------------------------------------------------------------------


async def test_write_502_on_connector_error() -> None:
    """write() wraps a ClientConnectorError in a 502 MemoryAdapterResponse."""
    config = UpstreamConfig(url="http://zep:8001", write_methods=["POST"])
    mock_session = MagicMock(spec=aiohttp.ClientSession)

    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(
        side_effect=aiohttp.ClientConnectorError(MagicMock(), OSError("refused"))
    )
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    mock_session.post.return_value = mock_cm

    adapter = ZepAdapter(config, mock_session)
    payload = EnrichedPayload(
        original_body={},
        entities=[],
        revok_version="0.0.1",
        processed_at=0.0,
        original_bytes=b'{"messages":[]}',
    )
    result = await adapter.write(payload, "/api/v1/sessions/abc/memory")

    assert result.status == 502
    assert result.is_error is True
    assert b"upstream_unavailable" in result.body


# ---------------------------------------------------------------------------
# T020: forward() returns 502 on ClientConnectorError
# ---------------------------------------------------------------------------


async def test_forward_502_on_connector_error() -> None:
    """forward() wraps a ClientConnectorError in a 502 MemoryAdapterResponse."""
    config = UpstreamConfig(url="http://zep:8001", write_methods=["POST"])
    mock_session = MagicMock(spec=aiohttp.ClientSession)

    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(
        side_effect=aiohttp.ClientConnectorError(MagicMock(), OSError("refused"))
    )
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    mock_session.request.return_value = mock_cm

    adapter = ZepAdapter(config, mock_session)
    signal = Signal(
        raw_content="test",
        source_id="unknown",
        timestamp=0.0,
        http_method="GET",
        http_path="/api/v1/sessions/abc/memory",
        original_body=b"",
        headers={},
    )
    result = await adapter.forward(signal)

    assert result.status == 502
    assert result.is_error is True
    assert b"upstream_unavailable" in result.body


# ---------------------------------------------------------------------------
# T024: write() emits DEBUG log with session_id and http_path
# ---------------------------------------------------------------------------


async def test_zep_write_emits_debug_log(caplog: pytest.LogCaptureFixture) -> None:
    """write() emits a DEBUG record with session_id and http_path in its extra fields."""
    config = UpstreamConfig(url="http://zep:8001", write_methods=["POST"])
    mock_session = MagicMock(spec=aiohttp.ClientSession)

    mock_resp = AsyncMock()
    mock_resp.status = 200

    mock_resp.read = AsyncMock(return_value=b"{}")
    mock_resp.headers = {}
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_resp)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    mock_session.post.return_value = mock_cm

    adapter = ZepAdapter(config, mock_session)
    payload = EnrichedPayload(
        original_body={},
        entities=[],
        revok_version="0.0.1",
        processed_at=0.0,
        original_bytes=b'{"messages":[]}',
    )

    with caplog.at_level(logging.DEBUG, logger="revok.adapters.zep"):
        await adapter.write(payload, "/api/v1/sessions/mysession/memory")

    debug_records = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert debug_records, (
        "Expected at least one DEBUG log record from ZepAdapter.write()"
    )
    record = debug_records[0]
    assert record.__dict__.get("session_id") == "mysession"
    assert record.__dict__.get("http_path") == "/api/v1/sessions/mysession/memory"


# ---------------------------------------------------------------------------
# Extra: close() is idempotent
# ---------------------------------------------------------------------------


async def test_close_is_idempotent() -> None:
    """Calling close() multiple times does not raise and closes session only once."""
    config = UpstreamConfig(url="http://zep:8001", write_methods=["POST"])
    mock_session = MagicMock(spec=aiohttp.ClientSession)
    mock_session.close = AsyncMock()

    adapter = ZepAdapter(config, mock_session)
    await adapter.close()
    await adapter.close()  # second call must be no-op

    mock_session.close.assert_called_once()


# ---------------------------------------------------------------------------
# ZepAdapter.is_write_request classmethod
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,path,expected",
    [
        ("POST", "/api/v1/sessions/abc/memory", True),
        ("post", "/api/v1/sessions/abc/memory", True),  # case-insensitive
        ("GET", "/api/v1/sessions/abc/memory", False),  # GET not in write_methods
        ("POST", "/api/v1/sessions/", False),  # no session ID
        ("POST", "/api/v1/other", False),  # unrelated path
    ],
)
def test_zep_is_write_request(method: str, path: str, expected: bool) -> None:
    config = UpstreamConfig(url="http://zep:8001", write_methods=["POST"])
    assert ZepAdapter.is_write_request(method, path, config) is expected


# ---------------------------------------------------------------------------
# ZepAdapter.extract_signal_context classmethod
# ---------------------------------------------------------------------------


def test_zep_extract_signal_context_uses_session_id_as_source() -> None:
    """source_id is the session ID extracted from the path."""
    body = json.dumps({"messages": [{"role": "user", "content": "hello"}]}).encode()
    source_id, raw_content = ZepAdapter.extract_signal_context(
        "/api/v1/sessions/sess-99/memory", {}, body
    )
    assert source_id == "sess-99"


def test_zep_extract_signal_context_parses_messages_content() -> None:
    """raw_content joins all messages[].content values."""
    body = json.dumps(
        {
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "World"},
            ]
        }
    ).encode()
    _, raw_content = ZepAdapter.extract_signal_context(
        "/api/v1/sessions/s1/memory", {}, body
    )
    assert "Hello" in raw_content
    assert "World" in raw_content


def test_zep_extract_signal_context_fallback_to_x_agent_id() -> None:
    """When path has no session, X-Agent-ID header is used as source_id."""
    source_id, _ = ZepAdapter.extract_signal_context(
        "/api/v1/other", {"X-Agent-ID": "agent-x"}, b"{}"
    )
    assert source_id == "agent-x"


def test_zep_extract_signal_context_fallback_to_unknown() -> None:
    """source_id is 'unknown' when path has no session and no X-Agent-ID."""
    source_id, _ = ZepAdapter.extract_signal_context("/api/v1/other", {}, b"{}")
    assert source_id == "unknown"


def test_zep_extract_signal_context_raw_fallback_on_bad_json() -> None:
    """raw_content is the raw UTF-8 body when JSON is malformed."""
    raw_bytes = b"not json at all"
    _, raw_content = ZepAdapter.extract_signal_context(
        "/api/v1/sessions/s1/memory", {}, raw_bytes
    )
    assert raw_content == "not json at all"
