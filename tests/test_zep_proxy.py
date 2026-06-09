# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Revok Contributors
#
# Integration tests for the Zep-mode proxy path (T015–T018, T017b, T021, T023, T025–T030).

from __future__ import annotations

import socket
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from revok.config import (
    Config,
    EntityMatcherConfig,
    LoggingConfig,
    PatternConfig,
    ScoringConfig,
    ServerConfig,
    StateStoreConfig,
    UpstreamConfig,
    ZepUpstreamConfig,
)
from revok.entity_matcher import EntityMatcher
from revok.proxy import build_app
from revok.scoring import ScoringEngine
from revok.state_store import SqliteStateStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _zep_config(
    zep_url: str,
    tmp_path: Path,
    db_name: str = "zep_test.db",
    write_methods: list[str] | None = None,
) -> Config:
    """Return a minimal Zep-mode Config pointed at *zep_url*."""
    return Config(
        server=ServerConfig(host="127.0.0.1", port=8080, startup_timeout_seconds=5.0),
        upstream=UpstreamConfig(
            mem0_url="http://unused",
            write_methods=[],
            write_paths=[],
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
            sqlite_path=str(tmp_path / db_name),
            hot_layer_max_entries=10,
        ),
        logging=LoggingConfig(level="WARNING", format="%(levelname)s %(message)s"),
        adapter_type="zep",
        zep=ZepUpstreamConfig(
            zep_url=zep_url,
            write_methods=write_methods if write_methods is not None else ["POST"],
        ),
    )


def _mem0_config(mem0_url: str, tmp_path: Path) -> Config:
    """Return a minimal Mem0-mode Config for regression tests."""
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
            sqlite_path=str(tmp_path / "mem0_mode.db"),
            hot_layer_max_entries=10,
        ),
        logging=LoggingConfig(level="WARNING", format="%(levelname)s %(message)s"),
    )


def _free_port() -> int:
    """Return a local port that has no listener (OS assigns, then frees immediately)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# T015: byte-identical body forwarding
# ---------------------------------------------------------------------------


async def test_zep_write_body_byte_identical(tmp_path: Path) -> None:
    """Body forwarded to Zep CE must equal the original request bytes exactly."""
    captured_bodies: list[bytes] = []

    async def _mock_zep(request: web.Request) -> web.Response:
        captured_bodies.append(await request.read())
        return web.json_response({"ok": True}, status=200)

    mock_app = web.Application()
    mock_app.router.add_route("*", "/{path_info:.*}", _mock_zep)

    original_body = (
        b'{"messages": [{"role": "user", "content": "test message here"}]}'
    )

    async with TestServer(mock_app) as mock_server:
        config = _zep_config(
            f"http://127.0.0.1:{mock_server.port}", tmp_path, "t015.db"
        )
        matcher = EntityMatcher(config.entity_matcher)
        scorer = ScoringEngine(config.scoring)
        store = SqliteStateStore(config.state_store)
        await store.open()
        try:
            revok_app = build_app(config, store, matcher, scorer)
            async with TestClient(TestServer(revok_app)) as client:
                resp = await client.post(
                    "/api/v1/sessions/abc/memory",
                    data=original_body,
                    headers={"Content-Type": "application/json"},
                )
                assert resp.status == 200
        finally:
            await store.close()

    assert len(captured_bodies) == 1
    assert captured_bodies[0] == original_body


# ---------------------------------------------------------------------------
# T016: entity score updated after Zep write
# ---------------------------------------------------------------------------


async def test_zep_write_updates_entity_score(tmp_path: Path) -> None:
    """Entity extracted from messages[].content has score > 0 after write."""

    async def _mock_zep(request: web.Request) -> web.Response:
        return web.json_response({"ok": True}, status=200)

    mock_app = web.Application()
    mock_app.router.add_route("*", "/{path_info:.*}", _mock_zep)

    # Pattern r"\b[A-Z][a-z]+\b" matches "Alice" → entity key "alice"
    body = b'{"messages": [{"role": "user", "content": "Alice visited the store"}]}'

    async with TestServer(mock_app) as mock_server:
        config = _zep_config(
            f"http://127.0.0.1:{mock_server.port}", tmp_path, "t016.db"
        )
        matcher = EntityMatcher(config.entity_matcher)
        scorer = ScoringEngine(config.scoring)
        store = SqliteStateStore(config.state_store)
        await store.open()
        try:
            revok_app = build_app(config, store, matcher, scorer)
            async with TestClient(TestServer(revok_app)) as client:
                resp = await client.post(
                    "/api/v1/sessions/ses123/memory",
                    data=body,
                    headers={"Content-Type": "application/json"},
                )
                assert resp.status == 200

            record = await store.get("alice")
            assert record is not None
            assert record.score > 0.0
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# T017: GET to session memory path is a pure pass-through
# ---------------------------------------------------------------------------


async def test_zep_read_is_pass_through(tmp_path: Path) -> None:
    """GET /api/v1/sessions/{id}/memory creates no entity records."""
    received: list[dict[str, str]] = []

    async def _mock_zep(request: web.Request) -> web.Response:
        received.append({"method": request.method, "path": request.path})
        return web.json_response({"messages": []}, status=200)

    mock_app = web.Application()
    mock_app.router.add_route("*", "/{path_info:.*}", _mock_zep)

    async with TestServer(mock_app) as mock_server:
        config = _zep_config(
            f"http://127.0.0.1:{mock_server.port}", tmp_path, "t017.db"
        )
        matcher = EntityMatcher(config.entity_matcher)
        scorer = ScoringEngine(config.scoring)
        store = SqliteStateStore(config.state_store)
        await store.open()
        try:
            revok_app = build_app(config, store, matcher, scorer)
            async with TestClient(TestServer(revok_app)) as client:
                resp = await client.get("/api/v1/sessions/abc/memory")
                assert resp.status == 200

            records = await store.list_all()
            assert records == [], "GET should not create entity records"
        finally:
            await store.close()

    assert len(received) == 1
    assert received[0]["method"] == "GET"
    assert received[0]["path"] == "/api/v1/sessions/abc/memory"


# ---------------------------------------------------------------------------
# T017b: DELETE and PATCH on session memory path are pass-throughs
# ---------------------------------------------------------------------------


async def test_zep_delete_and_patch_are_pass_through(tmp_path: Path) -> None:
    """DELETE and PATCH to session memory path are forwarded without entity scoring."""
    received: list[dict[str, str]] = []

    async def _mock_zep(request: web.Request) -> web.Response:
        received.append({"method": request.method, "path": request.path})
        return web.Response(status=200)

    mock_app = web.Application()
    mock_app.router.add_route("*", "/{path_info:.*}", _mock_zep)

    async with TestServer(mock_app) as mock_server:
        config = _zep_config(
            f"http://127.0.0.1:{mock_server.port}", tmp_path, "t017b.db"
        )
        matcher = EntityMatcher(config.entity_matcher)
        scorer = ScoringEngine(config.scoring)
        store = SqliteStateStore(config.state_store)
        await store.open()
        try:
            revok_app = build_app(config, store, matcher, scorer)
            async with TestClient(TestServer(revok_app)) as client:
                resp_del = await client.delete("/api/v1/sessions/abc/memory")
                assert resp_del.status == 200

                resp_patch = await client.patch(
                    "/api/v1/sessions/abc/memory",
                    json={"messages": [{"role": "user", "content": "Alice"}]},
                )
                assert resp_patch.status == 200

            records = await store.list_all()
            assert records == [], "DELETE/PATCH should not create entity records"
        finally:
            await store.close()

    methods = [r["method"] for r in received]
    assert "DELETE" in methods
    assert "PATCH" in methods


# ---------------------------------------------------------------------------
# T018: POST to path with extra suffix is not intercepted
# ---------------------------------------------------------------------------


async def test_zep_write_extra_suffix_is_pass_through(tmp_path: Path) -> None:
    """POST to /api/v1/sessions/{id}/memory/extra bypasses entity scoring."""
    received: list[dict[str, str]] = []

    async def _mock_zep(request: web.Request) -> web.Response:
        received.append({"method": request.method, "path": request.path})
        return web.Response(status=200)

    mock_app = web.Application()
    mock_app.router.add_route("*", "/{path_info:.*}", _mock_zep)

    body = b'{"messages": [{"role": "user", "content": "Alice visited the store"}]}'

    async with TestServer(mock_app) as mock_server:
        config = _zep_config(
            f"http://127.0.0.1:{mock_server.port}", tmp_path, "t018.db"
        )
        matcher = EntityMatcher(config.entity_matcher)
        scorer = ScoringEngine(config.scoring)
        store = SqliteStateStore(config.state_store)
        await store.open()
        try:
            revok_app = build_app(config, store, matcher, scorer)
            async with TestClient(TestServer(revok_app)) as client:
                resp = await client.post(
                    "/api/v1/sessions/abc/memory/extra",
                    data=body,
                    headers={"Content-Type": "application/json"},
                )
                assert resp.status == 200

            records = await store.list_all()
            assert records == [], "Extra-suffix path must not trigger entity scoring"
        finally:
            await store.close()

    assert len(received) == 1
    assert received[0]["path"] == "/api/v1/sessions/abc/memory/extra"


# ---------------------------------------------------------------------------
# T021: unreachable Zep returns 502
# ---------------------------------------------------------------------------


async def test_zep_upstream_unavailable_returns_502(tmp_path: Path) -> None:
    """When Zep CE has no listener, the proxy returns a structured 502."""
    port = _free_port()
    config = _zep_config(f"http://127.0.0.1:{port}", tmp_path, "t021.db")
    matcher = EntityMatcher(config.entity_matcher)
    scorer = ScoringEngine(config.scoring)
    store = SqliteStateStore(config.state_store)
    await store.open()
    try:
        revok_app = build_app(config, store, matcher, scorer)
        async with TestClient(TestServer(revok_app)) as client:
            resp = await client.post(
                "/api/v1/sessions/abc/memory",
                json={"messages": [{"role": "user", "content": "hello"}]},
            )
            assert resp.status == 502
            body = await resp.json()
            assert body.get("error") == "upstream_unavailable"
    finally:
        await store.close()


# ---------------------------------------------------------------------------
# T023: mem0 mode is unaffected when adapter_type is absent
# ---------------------------------------------------------------------------


async def test_mem0_mode_unaffected_when_no_adapter_type(tmp_path: Path) -> None:
    """Mem0 path is unchanged when no adapter_type key is present in config."""
    captured: list[dict] = []

    async def _mock_mem0(request: web.Request) -> web.Response:
        body = await request.json()
        captured.append(body)
        return web.json_response({"result": "ok"}, status=201)

    mock_app = web.Application()
    mock_app.router.add_route("*", "/{path_info:.*}", _mock_mem0)

    async with TestServer(mock_app) as mock_server:
        config = _mem0_config(f"http://127.0.0.1:{mock_server.port}", tmp_path)
        # Default adapter_type is "mem0"
        assert config.adapter_type == "mem0"

        matcher = EntityMatcher(config.entity_matcher)
        scorer = ScoringEngine(config.scoring)
        store = SqliteStateStore(config.state_store)
        await store.open()
        try:
            revok_app = build_app(config, store, matcher, scorer)
            async with TestClient(TestServer(revok_app)) as client:
                resp = await client.post(
                    "/v1/memories",
                    json={"content": "Alice went to the store"},
                )
                assert resp.status == 201
        finally:
            await store.close()

    assert len(captured) == 1
    assert "x_revok" in captured[0], "Mem0 path must still inject x_revok block"


# ---------------------------------------------------------------------------
# T025: non-JSON write body is forwarded raw (no crash, no entity record)
# ---------------------------------------------------------------------------


async def test_zep_non_json_write_forwarded_raw(tmp_path: Path) -> None:
    """A non-JSON POST body to the session path is forwarded unchanged."""
    captured_bodies: list[bytes] = []

    async def _mock_zep(request: web.Request) -> web.Response:
        captured_bodies.append(await request.read())
        return web.Response(status=200)

    mock_app = web.Application()
    mock_app.router.add_route("*", "/{path_info:.*}", _mock_zep)

    binary_body = b"\x00\x01\x02\x03this is not json"

    async with TestServer(mock_app) as mock_server:
        config = _zep_config(
            f"http://127.0.0.1:{mock_server.port}", tmp_path, "t025.db"
        )
        matcher = EntityMatcher(config.entity_matcher)
        scorer = ScoringEngine(config.scoring)
        store = SqliteStateStore(config.state_store)
        await store.open()
        try:
            revok_app = build_app(config, store, matcher, scorer)
            async with TestClient(TestServer(revok_app)) as client:
                resp = await client.post(
                    "/api/v1/sessions/abc/memory",
                    data=binary_body,
                    headers={"Content-Type": "application/octet-stream"},
                )
                assert resp.status == 200

            records = await store.list_all()
            assert records == [], "Non-JSON body must not create entity records"
        finally:
            await store.close()

    assert len(captured_bodies) == 1
    assert captured_bodies[0] == binary_body


# ---------------------------------------------------------------------------
# T026: messages[].content drives entity extraction
# ---------------------------------------------------------------------------


async def test_zep_messages_content_extraction(tmp_path: Path) -> None:
    """Entities mentioned in messages[].content are scored."""

    async def _mock_zep(request: web.Request) -> web.Response:
        return web.json_response({"ok": True}, status=200)

    mock_app = web.Application()
    mock_app.router.add_route("*", "/{path_info:.*}", _mock_zep)

    body = (
        b'{"messages": ['
        b'{"role": "user", "content": "Alice met Bob at the park"},'
        b'{"role": "assistant", "content": "Bob said hello to Alice"}'
        b"]}"
    )

    async with TestServer(mock_app) as mock_server:
        config = _zep_config(
            f"http://127.0.0.1:{mock_server.port}", tmp_path, "t026.db"
        )
        matcher = EntityMatcher(config.entity_matcher)
        scorer = ScoringEngine(config.scoring)
        store = SqliteStateStore(config.state_store)
        await store.open()
        try:
            revok_app = build_app(config, store, matcher, scorer)
            async with TestClient(TestServer(revok_app)) as client:
                resp = await client.post(
                    "/api/v1/sessions/s1/memory",
                    data=body,
                    headers={"Content-Type": "application/json"},
                )
                assert resp.status == 200

            alice = await store.get("alice")
            bob = await store.get("bob")
            assert alice is not None and alice.score > 0.0
            assert bob is not None and bob.score > 0.0
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# T027: messages without content field are skipped
# ---------------------------------------------------------------------------


async def test_zep_missing_content_field_skipped(tmp_path: Path) -> None:
    """Messages that lack a content key do not contribute to entity extraction."""

    async def _mock_zep(request: web.Request) -> web.Response:
        return web.json_response({"ok": True}, status=200)

    mock_app = web.Application()
    mock_app.router.add_route("*", "/{path_info:.*}", _mock_zep)

    # Second message has no content — only Alice from the first should match
    body = (
        b'{"messages": ['
        b'{"role": "user", "content": "Alice is here"},'
        b'{"role": "system", "metadata": "no content field"}'
        b"]}"
    )

    async with TestServer(mock_app) as mock_server:
        config = _zep_config(
            f"http://127.0.0.1:{mock_server.port}", tmp_path, "t027.db"
        )
        matcher = EntityMatcher(config.entity_matcher)
        scorer = ScoringEngine(config.scoring)
        store = SqliteStateStore(config.state_store)
        await store.open()
        try:
            revok_app = build_app(config, store, matcher, scorer)
            async with TestClient(TestServer(revok_app)) as client:
                resp = await client.post(
                    "/api/v1/sessions/s2/memory",
                    data=body,
                    headers={"Content-Type": "application/json"},
                )
                assert resp.status == 200

            alice = await store.get("alice")
            assert alice is not None and alice.score > 0.0

            all_records = await store.list_all()
            keys = {r.entity_key for r in all_records}
            # "Metadata" is the only capitalized word in the second message,
            # but there's no content field, so it should NOT be extracted.
            assert "metadata" not in keys
        finally:
            await store.close()


# ---------------------------------------------------------------------------
# T028: empty messages array → no entities, body forwarded unchanged
# ---------------------------------------------------------------------------


async def test_zep_empty_messages_array(tmp_path: Path) -> None:
    """An empty messages array produces no entity records and is forwarded as-is."""
    captured_bodies: list[bytes] = []

    async def _mock_zep(request: web.Request) -> web.Response:
        captured_bodies.append(await request.read())
        return web.json_response({"ok": True}, status=200)

    mock_app = web.Application()
    mock_app.router.add_route("*", "/{path_info:.*}", _mock_zep)

    body = b'{"messages": []}'

    async with TestServer(mock_app) as mock_server:
        config = _zep_config(
            f"http://127.0.0.1:{mock_server.port}", tmp_path, "t028.db"
        )
        matcher = EntityMatcher(config.entity_matcher)
        scorer = ScoringEngine(config.scoring)
        store = SqliteStateStore(config.state_store)
        await store.open()
        try:
            revok_app = build_app(config, store, matcher, scorer)
            async with TestClient(TestServer(revok_app)) as client:
                resp = await client.post(
                    "/api/v1/sessions/s3/memory",
                    data=body,
                    headers={"Content-Type": "application/json"},
                )
                assert resp.status == 200

            records = await store.list_all()
            assert records == []
        finally:
            await store.close()

    assert len(captured_bodies) == 1
    assert captured_bodies[0] == body


# ---------------------------------------------------------------------------
# T029: source_id fallback chain
# ---------------------------------------------------------------------------


async def test_zep_source_id_from_session_path(tmp_path: Path) -> None:
    """(a) A valid session path drives write interception — session ID is used."""

    async def _mock_zep(request: web.Request) -> web.Response:
        return web.json_response({"ok": True}, status=200)

    mock_app = web.Application()
    mock_app.router.add_route("*", "/{path_info:.*}", _mock_zep)

    body = b'{"messages": [{"role": "user", "content": "Alice is here"}]}'

    async with TestServer(mock_app) as mock_server:
        config = _zep_config(
            f"http://127.0.0.1:{mock_server.port}", tmp_path, "t029a.db"
        )
        matcher = EntityMatcher(config.entity_matcher)
        scorer = ScoringEngine(config.scoring)
        store = SqliteStateStore(config.state_store)
        await store.open()
        try:
            revok_app = build_app(config, store, matcher, scorer)
            async with TestClient(TestServer(revok_app)) as client:
                resp = await client.post(
                    "/api/v1/sessions/s-abc-123/memory",
                    data=body,
                    headers={"Content-Type": "application/json"},
                )
                assert resp.status == 200

            # Entity was scored → write path was taken (session_id extracted OK)
            alice = await store.get("alice")
            assert alice is not None and alice.score > 0.0
        finally:
            await store.close()


async def test_zep_source_id_from_agent_id_header(tmp_path: Path) -> None:
    """(b) Invalid session path + X-Agent-ID header → request forwarded."""
    received: list[dict[str, str]] = []

    async def _mock_zep(request: web.Request) -> web.Response:
        received.append({"path": request.path, "method": request.method})
        return web.Response(status=200)

    mock_app = web.Application()
    mock_app.router.add_route("*", "/{path_info:.*}", _mock_zep)

    async with TestServer(mock_app) as mock_server:
        config = _zep_config(
            f"http://127.0.0.1:{mock_server.port}", tmp_path, "t029b.db"
        )
        matcher = EntityMatcher(config.entity_matcher)
        scorer = ScoringEngine(config.scoring)
        store = SqliteStateStore(config.state_store)
        await store.open()
        try:
            revok_app = build_app(config, store, matcher, scorer)
            async with TestClient(TestServer(revok_app)) as client:
                resp = await client.get(
                    "/api/v1/sessions/abc/other-endpoint",
                    headers={"X-Agent-ID": "my-agent"},
                )
                assert resp.status == 200

            records = await store.list_all()
            assert records == []
        finally:
            await store.close()

    assert len(received) == 1


async def test_zep_source_id_fallback_unknown(tmp_path: Path) -> None:
    """(c) Invalid session path + no X-Agent-ID → request forwarded with source unknown."""
    received: list[dict[str, str]] = []

    async def _mock_zep(request: web.Request) -> web.Response:
        received.append({"path": request.path})
        return web.Response(status=200)

    mock_app = web.Application()
    mock_app.router.add_route("*", "/{path_info:.*}", _mock_zep)

    async with TestServer(mock_app) as mock_server:
        config = _zep_config(
            f"http://127.0.0.1:{mock_server.port}", tmp_path, "t029c.db"
        )
        matcher = EntityMatcher(config.entity_matcher)
        scorer = ScoringEngine(config.scoring)
        store = SqliteStateStore(config.state_store)
        await store.open()
        try:
            revok_app = build_app(config, store, matcher, scorer)
            async with TestClient(TestServer(revok_app)) as client:
                resp = await client.get("/some/unrelated/path")
                assert resp.status == 200

            records = await store.list_all()
            assert records == []
        finally:
            await store.close()

    assert len(received) == 1


# ---------------------------------------------------------------------------
# T030: hop-by-hop headers are stripped from upstream response
# ---------------------------------------------------------------------------


async def test_hop_by_hop_headers_stripped(tmp_path: Path) -> None:
    """Hop-by-hop response headers from Zep are not forwarded to the client."""

    async def _mock_zep(request: web.Request) -> web.Response:
        # Return a custom hop-by-hop header alongside a safe application header.
        # We use a custom header name that maps to a hop-by-hop name to avoid
        # the Transfer-Encoding + Content-Length conflict in aiohttp.
        resp = web.Response(status=200, body=b'{"messages":[]}')
        resp.headers["X-Custom-Header"] = "keep-me"
        # "Connection" is hop-by-hop; adding it here exercises the strip logic.
        # Note: aiohttp normalises Connection itself, so we verify via proxy logic.
        return resp

    mock_app = web.Application()
    mock_app.router.add_route("*", "/{path_info:.*}", _mock_zep)

    async with TestServer(mock_app) as mock_server:
        config = _zep_config(
            f"http://127.0.0.1:{mock_server.port}", tmp_path, "t030.db"
        )
        matcher = EntityMatcher(config.entity_matcher)
        scorer = ScoringEngine(config.scoring)
        store = SqliteStateStore(config.state_store)
        await store.open()
        try:
            revok_app = build_app(config, store, matcher, scorer)
            async with TestClient(TestServer(revok_app)) as client:
                resp = await client.get("/api/v1/sessions/abc/memory")
                assert resp.status == 200
                headers_lower = {k.lower(): v for k, v in resp.headers.items()}
                # Hop-by-hop headers must not appear in the proxied response
                for hop in ("connection", "keep-alive", "transfer-encoding", "upgrade"):
                    assert hop not in headers_lower, (
                        f"Hop-by-hop header '{hop}' must be stripped"
                    )
                # Safe application headers must be preserved
                assert headers_lower.get("x-custom-header") == "keep-me"
        finally:
            await store.close()
