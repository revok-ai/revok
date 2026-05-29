# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Revok Contributors
#
# Tests for revok.proxy — Mem0Adapter and build_app() catch-all handler.

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer, TestClient

from revok.config import (
    Config,
    EntityMatcherConfig,
    LoggingConfig,
    PatternConfig,
    ScoringConfig,
    ServerConfig,
    StateStoreConfig,
    UpstreamConfig,
)
from revok.entity_matcher import EntityMatcher
from revok.proxy import Mem0Adapter, build_app
from revok.scoring import ScoringEngine
from revok.state_store import SqliteStateStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config_with_upstream(mem0_url: str, tmp_path: Path) -> Config:
    """Return a minimal Config pointed at *mem0_url*."""
    return Config(
        server=ServerConfig(host="127.0.0.1", port=8080, startup_timeout_seconds=5.0),
        upstream=UpstreamConfig(
            mem0_url=mem0_url,
            write_methods=["POST"],
            write_paths=["/v1/memories"],
        ),
        entity_matcher=EntityMatcherConfig(
            patterns=[PatternConfig(name="person", regex=r"\b[A-Z][a-z]+\b")]
        ),
        scoring=ScoringConfig(
            half_life_seconds=86400.0,
            signal_strength=0.3,
            score_cap=1.0,
        ),
        state_store=StateStoreConfig(
            sqlite_path=str(tmp_path / "proxy_test.db"),
            hot_layer_max_entries=10,
        ),
        logging=LoggingConfig(level="WARNING", format="%(levelname)s %(message)s"),
    )


# ---------------------------------------------------------------------------
# T026: Proxy integration tests using TestServer as mock Mem0
# ---------------------------------------------------------------------------


async def test_write_request_produces_enriched_x_revok_block(tmp_path: Path) -> None:
    """Write request forwarded to upstream contains the x_revok metadata block."""
    captured: list[dict] = []

    async def _mock_mem0(request: web.Request) -> web.Response:
        body = await request.json()
        captured.append(body)
        return web.json_response({"result": "ok"}, status=201)

    mock_app = web.Application()
    mock_app.router.add_route("*", "/{path_info:.*}", _mock_mem0)

    async with TestServer(mock_app) as mock_server:
        mem0_url = f"http://127.0.0.1:{mock_server.port}"
        config = _config_with_upstream(mem0_url, tmp_path)
        matcher = EntityMatcher(config.entity_matcher)
        scorer = ScoringEngine(config.scoring)
        store = SqliteStateStore(config.state_store)
        await store.open()

        try:
            revok_app = build_app(config, store, matcher, scorer)
            async with TestClient(TestServer(revok_app)) as client:
                resp = await client.post(
                    "/v1/memories",
                    json={"content": "Alice visited the lab"},
                )
                assert resp.status == 201
        finally:
            await store.close()

    assert len(captured) == 1
    upstream_body = captured[0]
    assert "x_revok" in upstream_body
    x_revok = upstream_body["x_revok"]
    assert "version" in x_revok
    assert "processed_at" in x_revok
    assert "entities" in x_revok
    # Original content must be preserved
    assert upstream_body.get("content") == "Alice visited the lab"


async def test_read_request_forwarded_unchanged(tmp_path: Path) -> None:
    """Non-write request is forwarded to Mem0 without body modification."""
    received: list[dict] = []

    async def _mock_mem0(request: web.Request) -> web.Response:
        received.append({"method": request.method, "path": request.path})
        return web.json_response([{"memory_id": "abc123"}], status=200)

    mock_app = web.Application()
    mock_app.router.add_route("*", "/{path_info:.*}", _mock_mem0)

    async with TestServer(mock_app) as mock_server:
        mem0_url = f"http://127.0.0.1:{mock_server.port}"
        config = _config_with_upstream(mem0_url, tmp_path)
        matcher = EntityMatcher(config.entity_matcher)
        scorer = ScoringEngine(config.scoring)
        store = SqliteStateStore(config.state_store)
        await store.open()

        try:
            revok_app = build_app(config, store, matcher, scorer)
            async with TestClient(TestServer(revok_app)) as client:
                resp = await client.get("/v1/memories")
                assert resp.status == 200
        finally:
            await store.close()

    assert len(received) == 1
    assert received[0]["method"] == "GET"
    assert received[0]["path"] == "/v1/memories"


async def test_unreachable_upstream_returns_502(tmp_path: Path) -> None:
    """Requests to an unreachable upstream return 502 with upstream_unavailable."""
    # Acquire a free port then release it so it is definitely not listening
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    free_port = sock.getsockname()[1]
    sock.close()

    config = _config_with_upstream(f"http://127.0.0.1:{free_port}", tmp_path)
    matcher = EntityMatcher(config.entity_matcher)
    scorer = ScoringEngine(config.scoring)
    store = SqliteStateStore(config.state_store)
    await store.open()

    try:
        revok_app = build_app(config, store, matcher, scorer)
        async with TestClient(TestServer(revok_app)) as client:
            resp = await client.post(
                "/v1/memories",
                json={"content": "test signal"},
            )
            assert resp.status == 502
            body = await resp.json()
            assert body.get("error") == "upstream_unavailable"
    finally:
        await store.close()


async def test_write_unreachable_upstream_response_is_error(tmp_path: Path) -> None:
    """MemoryAdapterResponse.is_error is True when upstream is unreachable."""
    import socket

    import aiohttp

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    free_port = sock.getsockname()[1]
    sock.close()

    config = _config_with_upstream(f"http://127.0.0.1:{free_port}", tmp_path)
    from revok.models import EnrichedPayload

    payload = EnrichedPayload(
        original_body={"content": "test"},
        entities=[],
        revok_version="0.1.0",
        processed_at=0.0,
    )

    async with aiohttp.ClientSession() as session:
        adapter = Mem0Adapter(config.upstream, session)
        result = await adapter.write(payload, "/v1/memories")

    assert result.status == 502
    assert result.is_error is True
