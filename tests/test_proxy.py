# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Revok Contributors
#
# Tests for revok.proxy — Mem0Adapter and build_app() catch-all handler.

from __future__ import annotations

from pathlib import Path

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
from revok.adapters import Mem0Adapter
from revok.proxy import build_app
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
            url=mem0_url,
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


def _config_with_size_limit(mem0_url: str, tmp_path: Path, max_bytes: int) -> Config:
    """Return a Config with a custom max_signal_size_bytes limit."""
    return Config(
        server=ServerConfig(
            host="127.0.0.1",
            port=8080,
            startup_timeout_seconds=5.0,
            max_signal_size_bytes=max_bytes,
        ),
        upstream=UpstreamConfig(
            url=mem0_url,
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
            sqlite_path=str(tmp_path / "size_limit_test.db"),
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


# ---------------------------------------------------------------------------
# T029 / T031: Entity read routes
# ---------------------------------------------------------------------------


async def test_get_entity_returns_record_after_write(tmp_path: Path) -> None:
    """GET /v1/entities/{entity_key} returns 200 + JSON after a matching write."""

    async def _mock_mem0(request: web.Request) -> web.Response:
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
                # First trigger entity creation via a write
                await client.post("/v1/memories", json={"content": "Alice arrived"})
                # Now fetch the entity record
                resp = await client.get("/v1/entities/alice")
                assert resp.status == 200
                body = await resp.json()
                assert body["entity_key"] == "alice"
                assert "score" in body
        finally:
            await store.close()


async def test_get_entity_returns_404_when_not_found(tmp_path: Path) -> None:
    """GET /v1/entities/{entity_key} returns 404 JSON for unknown keys."""

    async def _mock_mem0(request: web.Request) -> web.Response:
        return web.json_response({}, status=200)

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
                resp = await client.get("/v1/entities/nobody")
                assert resp.status == 404
                body = await resp.json()
                assert body.get("error") == "not_found"
        finally:
            await store.close()


async def test_list_entities_returns_paginated_results(tmp_path: Path) -> None:
    """GET /v1/entities returns a JSON list with offset/limit support."""

    async def _mock_mem0(request: web.Request) -> web.Response:
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
                # Create two entities
                await client.post("/v1/memories", json={"content": "Alice met Bob"})
                resp = await client.get("/v1/entities?offset=0&limit=10")
                assert resp.status == 200
                body = await resp.json()
                assert isinstance(body, list)
                assert len(body) >= 1
                # Each item must have entity_key
                for item in body:
                    assert "entity_key" in item
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# DELETE /v1/entities/{entity_key}
# ---------------------------------------------------------------------------


async def test_delete_entity_returns_200_and_removes_record(tmp_path: Path) -> None:
    """DELETE /v1/entities/{entity_key} returns 200 and the record is gone."""

    async def _mock_mem0(request: web.Request) -> web.Response:
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
                # Create entity via a write
                await client.post("/v1/memories", json={"content": "Alice arrived"})
                assert (await client.get("/v1/entities/alice")).status == 200

                # Delete it
                resp = await client.delete("/v1/entities/alice")
                assert resp.status == 200
                body = await resp.json()
                assert body["deleted"] == "alice"

                # Should now be gone
                assert (await client.get("/v1/entities/alice")).status == 404
        finally:
            await store.close()


async def test_delete_entity_returns_404_when_not_found(tmp_path: Path) -> None:
    """DELETE /v1/entities/{entity_key} returns 404 when the key does not exist."""
    config = _config_with_upstream("http://127.0.0.1:1", tmp_path)
    matcher = EntityMatcher(config.entity_matcher)
    scorer = ScoringEngine(config.scoring)
    store = SqliteStateStore(config.state_store)
    await store.open()

    try:
        revok_app = build_app(config, store, matcher, scorer)
        async with TestClient(TestServer(revok_app)) as client:
            resp = await client.delete("/v1/entities/nobody")
            assert resp.status == 404
            body = await resp.json()
            assert body["error"] == "not_found"
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# T038: 413 when payload exceeds max_signal_size_bytes
# ---------------------------------------------------------------------------


async def test_oversized_payload_returns_413(tmp_path: Path) -> None:
    """Proxy returns 413 and does NOT forward when body exceeds size limit."""
    forwarded: list[bytes] = []

    async def _mock_mem0(request: web.Request) -> web.Response:
        forwarded.append(await request.read())
        return web.json_response({"result": "ok"}, status=201)

    mock_app = web.Application()
    mock_app.router.add_route("*", "/{path_info:.*}", _mock_mem0)

    async with TestServer(mock_app) as mock_server:
        mem0_url = f"http://127.0.0.1:{mock_server.port}"
        # Set a very small limit so any real payload exceeds it
        config = _config_with_size_limit(mem0_url, tmp_path, max_bytes=10)
        matcher = EntityMatcher(config.entity_matcher)
        scorer = ScoringEngine(config.scoring)
        store = SqliteStateStore(config.state_store)
        await store.open()

        try:
            revok_app = build_app(config, store, matcher, scorer)
            async with TestClient(TestServer(revok_app)) as client:
                resp = await client.post(
                    "/v1/memories",
                    data=b"x" * 100,  # 100 bytes > 10 byte limit
                    headers={"Content-Type": "application/json"},
                )
                assert resp.status == 413
                body = await resp.json()
                assert body.get("error") == "payload_too_large"
        finally:
            await store.close()

    # Upstream must NOT have received the request
    assert len(forwarded) == 0


# ---------------------------------------------------------------------------
# T041: Non-JSON write body is forwarded raw without enrichment
# ---------------------------------------------------------------------------


async def test_non_json_write_is_forwarded_raw(tmp_path: Path) -> None:
    """Write with non-JSON body is forwarded unchanged; no x_revok block added."""
    received: list[dict] = []

    async def _mock_mem0(request: web.Request) -> web.Response:
        raw = await request.read()
        received.append({"raw": raw, "path": request.path})
        return web.json_response({"result": "ok"}, status=200)

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
                raw_body = b"not-json-at-all"
                resp = await client.post(
                    "/v1/memories",
                    data=raw_body,
                    headers={"Content-Type": "text/plain"},
                )
                # Should be forwarded; upstream returned 200
                assert resp.status == 200
        finally:
            await store.close()

    # Body must reach upstream unchanged
    assert len(received) == 1
    assert received[0]["raw"] == raw_body
    # Must NOT contain x_revok enrichment
    assert b"x_revok" not in received[0]["raw"]


# ---------------------------------------------------------------------------
# FIX 1: GET /v1/entities/{key} returns time-recovered score via decay_at()
# ---------------------------------------------------------------------------


async def test_get_entity_returns_time_recovered_score(tmp_path: Path) -> None:
    """GET /v1/entities/{entity_key} returns decay_at() score, not frozen stored score."""
    import time as _time

    from revok.models import EntityRecord

    # Short half-life so 3-hour-old record shows clearly distinguishable recovery.
    config = Config(
        server=ServerConfig(host="127.0.0.1", port=8080, startup_timeout_seconds=5.0),
        upstream=UpstreamConfig(
            url="http://127.0.0.1:1",
            write_methods=["POST"],
            write_paths=["/v1/memories"],
        ),
        entity_matcher=EntityMatcherConfig(
            patterns=[PatternConfig(name="person", regex=r"\b[A-Z][a-z]+\b")]
        ),
        scoring=ScoringConfig(
            half_life_seconds=3600.0,  # 1-hour half-life — recovery is substantial
            signal_strength=0.3,
            score_cap=1.0,
        ),
        state_store=StateStoreConfig(
            sqlite_path=str(tmp_path / "decay_test.db"),
            hot_layer_max_entries=10,
        ),
        logging=LoggingConfig(level="WARNING", format="%(levelname)s %(message)s"),
    )
    matcher = EntityMatcher(config.entity_matcher)
    scorer = ScoringEngine(config.scoring)
    store = SqliteStateStore(config.state_store)
    await store.open()

    try:
        # Plant a record with frozen score=0.0, last_seen 3 hours ago.
        # decay_at will compute ~0.875 (1.0 - 1.0*exp(-ln2/3600*10800)).
        frozen_score = 0.0
        old_time = _time.time() - 10800.0  # 3 hours ago
        old_record = EntityRecord(
            entity_key="testentity",
            score=frozen_score,
            valid_time=old_time,
            transaction_time=old_time,
            signal_count=1,
            pattern_name="test",
        )
        await store.put(old_record)

        revok_app = build_app(config, store, matcher, scorer)
        async with TestClient(TestServer(revok_app)) as client:
            resp = await client.get("/v1/entities/testentity")
            assert resp.status == 200
            body = await resp.json()
            assert body["entity_key"] == "testentity"
            # Returned score must be greater than the frozen 0.0
            assert body["score"] > frozen_score
            # Must closely match what decay_at computes right now
            expected = scorer.decay_at(old_record, _time.time())
            assert abs(body["score"] - expected) < 0.01
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# FIX 2: POST /signals — dedicated endpoint separate from the write path
# ---------------------------------------------------------------------------


async def test_signal_endpoint_returns_202(tmp_path: Path) -> None:
    """POST /signals with valid JSON body returns 202 Accepted."""
    config = _config_with_upstream("http://127.0.0.1:1", tmp_path)
    matcher = EntityMatcher(config.entity_matcher)
    scorer = ScoringEngine(config.scoring)
    store = SqliteStateStore(config.state_store)
    await store.open()

    try:
        revok_app = build_app(config, store, matcher, scorer)
        async with TestClient(TestServer(revok_app)) as client:
            resp = await client.post(
                "/signals",
                json={
                    "entity_refs": ["redis-enterprise-pricing"],
                    "severity": "high",
                    "source": "webhook",
                    "payload": {},
                },
            )
            assert resp.status == 202
            body = await resp.json()
            assert body.get("accepted") is True
    finally:
        await store.close()


async def test_signal_endpoint_published_to_queue(tmp_path: Path) -> None:
    """POST /signals publishes a Signal to the bus; enrich() is never called."""
    import asyncio

    from revok.signal_queue import AsyncioQueueBus

    config = _config_with_upstream("http://127.0.0.1:1", tmp_path)
    matcher = EntityMatcher(config.entity_matcher)
    scorer = ScoringEngine(config.scoring)
    store = SqliteStateStore(config.state_store)
    await store.open()

    bus = AsyncioQueueBus()
    try:
        revok_app = build_app(config, store, matcher, scorer, bus=bus)
        async with TestClient(TestServer(revok_app)) as client:
            await client.post(
                "/signals",
                json={
                    "entity_refs": ["redis-enterprise-pricing"],
                    "severity": "high",
                    "source": "webhook",
                    "payload": {},
                },
            )
        # Bus must have received exactly one signal
        signal = await asyncio.wait_for(bus.consume(), timeout=1.0)
        assert "redis-enterprise-pricing" in signal.raw_content
        assert signal.source_id == "webhook"
        assert signal.http_path == "/signals"
    finally:
        await store.close()
        await bus.close()


async def test_memory_write_still_goes_through_enrich(tmp_path: Path) -> None:
    """Memory writes through the proxy still call enrich() when a bus is present."""
    from revok.signal_queue import AsyncioQueueBus

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

        bus = AsyncioQueueBus()
        try:
            revok_app = build_app(config, store, matcher, scorer, bus=bus)
            async with TestClient(TestServer(revok_app)) as client:
                resp = await client.post(
                    "/v1/memories",
                    json={"content": "Alice visited the lab"},
                )
                assert resp.status == 201
        finally:
            await store.close()
            await bus.close()

    # enrich() must have run — x_revok block must be present in upstream body
    assert len(captured) == 1
    assert "x_revok" in captured[0]


# ---------------------------------------------------------------------------
# Contradiction state in GET /v1/entities/{key} (T024)
# ---------------------------------------------------------------------------


async def test_get_entity_response_includes_contradiction_fields(
    tmp_path: Path,
) -> None:
    """GET response body contains contradiction_count and last_contradiction_time."""
    from revok.models import EntityRecord

    config = _config_with_upstream("http://127.0.0.1:1", tmp_path)
    matcher = EntityMatcher(config.entity_matcher)
    scorer = ScoringEngine(config.scoring)
    store = SqliteStateStore(config.state_store)
    await store.open()

    try:
        rec = EntityRecord(
            entity_key="orion_cache",
            score=0.5,
            valid_time=1000.0,
            transaction_time=1000.0,
            signal_count=3,
            pattern_name="product",
            contradiction_count=2,
            last_contradiction_time=999.0,
            last_value_fingerprint="450.0",
        )
        await store.put(rec)

        revok_app = build_app(config, store, matcher, scorer)
        async with TestClient(TestServer(revok_app)) as client:
            resp = await client.get("/v1/entities/orion_cache")
            assert resp.status == 200
            body = await resp.json()
            assert body["contradiction_count"] == 2
            assert body["last_contradiction_time"] == 999.0
    finally:
        await store.close()


async def test_get_entity_response_excludes_last_value_fingerprint(
    tmp_path: Path,
) -> None:
    """GET response must NOT include the internal last_value_fingerprint field."""
    from revok.models import EntityRecord

    config = _config_with_upstream("http://127.0.0.1:1", tmp_path)
    matcher = EntityMatcher(config.entity_matcher)
    scorer = ScoringEngine(config.scoring)
    store = SqliteStateStore(config.state_store)
    await store.open()

    try:
        rec = EntityRecord(
            entity_key="orion_cache",
            score=0.5,
            valid_time=1000.0,
            transaction_time=1000.0,
            signal_count=1,
            pattern_name="product",
            last_value_fingerprint="500.0",
        )
        await store.put(rec)

        revok_app = build_app(config, store, matcher, scorer)
        async with TestClient(TestServer(revok_app)) as client:
            resp = await client.get("/v1/entities/orion_cache")
            assert resp.status == 200
            body = await resp.json()
            assert "last_value_fingerprint" not in body
    finally:
        await store.close()
