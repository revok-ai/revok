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

"""aiohttp HTTP proxy server and Mem0 upstream adapter.

``Mem0Adapter`` implements the ``MemoryAdapter`` Protocol (FR-009, FR-010, FR-011).
``build_app()`` builds the catch-all aiohttp application that intercepts write
requests, enriches them via the signal pipeline, and forwards all requests to
the configured Mem0 upstream.
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import hmac
import json
import logging
import os
import uuid
from pathlib import Path
import time
from typing import Any, cast

import aiohttp
import aiohttp.hdrs
import aiohttp.web

from revok.adapters import AdapterClass, _REGISTRY, build_adapter
from revok.causal_graph import CausalGraph
from revok.config import CausalGraphConfig, Config
from revok.entity_matcher import EntityMatcher
from revok.inspector import EntityNotFoundError, RevokInspector
from revok.interfaces import GraphBackend, GraphReader, Resolver, StateStore
from revok.metadata_writer import enrich
from revok.models import (
    GraphEdgeView,
    GraphNodeView,
    GraphTopologyResponse,
    ResolutionTrace,
    Signal,
)
from revok.resolver_runtime import invoke_resolver
from revok.scoring import ScoringEngine
from revok.signal_dedupe import SqliteIngestionStore
from revok.signal_history import SqliteSignalHistoryStore
from revok.signal_processor import SignalProcessor
from revok.signal_queue import AsyncioQueueBus
from revok.signal_sources import (
    HttpSignalSource,
    SourceRunner,
    SourceSupervisor,
)

logger = logging.getLogger(__name__)

# Hop-by-hop headers that MUST NOT be forwarded to the client (RFC 7230 §6.1)
_HOP_BY_HOP: frozenset[str] = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)

SIGNAL_PROCESSOR_TASK_KEY: aiohttp.web.AppKey[asyncio.Task[Any] | None] = (
    aiohttp.web.AppKey("signal_processor_task")
)


def _build_graph(cfg: CausalGraphConfig) -> GraphBackend:
    if cfg.graph_backend == "falkordb-lite":
        from revok.falkordb_backend import FalkorDBGraphBackend

        return FalkorDBGraphBackend(dbfilename=cfg.graph_backend_db_path)
    return CausalGraph()

@aiohttp.web.middleware
async def basic_auth_middleware(
    request: aiohttp.web.Request, handler: Any
) -> aiohttp.web.Response:
    """Optional HTTP basic auth, enabled only when both env vars are set.

    Used to gate the demo deployment; a no-op passthrough for local/OSS use
    where REVOK_DEMO_AUTH_USER / REVOK_DEMO_AUTH_PASS are unset.
    """

    user = os.environ.get("REVOK_DEMO_AUTH_USER")
    password = os.environ.get("REVOK_DEMO_AUTH_PASS")
    if not user or not password:
        return await handler(request)

    header = request.headers.get("Authorization", "")
    if header.startswith("Basic "):
        try:
            decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
            req_user, _, req_pass = decoded.partition(":")
            if hmac.compare_digest(req_user, user) and hmac.compare_digest(
                req_pass, password
            ):
                return await handler(request)
        except Exception:
            logger.debug("Invalid Basic Authorization header", exc_info=True)

    return aiohttp.web.Response(
        status=401,
        headers={"WWW-Authenticate": 'Basic realm="Revok Demo"'},
        body=b"Authentication required",
    )

def build_app(
    config: Config,
    store: StateStore,
    matcher: EntityMatcher,
    scorer: ScoringEngine,
    bus: AsyncioQueueBus | None = None,
    resolver: Resolver | None = None,
) -> aiohttp.web.Application:
    """Build and return the Revok aiohttp proxy application.

    The application has a catch-all route that:

    1. Normalises every incoming request into a
       :class:`~revok.models.Signal` (FR-004).
    2. If the method + path match a configured write trigger: enriches the
       signal via :func:`~revok.metadata_writer.enrich` and POSTs the
       enriched payload to Mem0.
    3. Otherwise: forwards the raw request to Mem0 unchanged.
    4. Returns Mem0's response (status + headers + body) to the caller.
    5. Returns ``502 Bad Gateway`` on
       ``aiohttp.ClientConnectorError`` (proxy-api.md §Error Semantics).

    A dedicated ``POST /signals`` route receives external world-signals and
    publishes them to *bus* without touching the Mem0 write path.

    Args:
        config: Full Revok configuration.
        store: Persistent entity state store.
        matcher: Named-regex entity extractor.
        scorer: Exponential decay scoring engine.
        bus: Optional signal bus for the ``POST /signals`` endpoint.
            A new :class:`~revok.signal_queue.AsyncioQueueBus` is created
            internally if *bus* is ``None``.
        resolver: Optional injected free-text resolver.

    Returns:
        :class:`aiohttp.web.Application` ready for
        :class:`aiohttp.web.AppRunner`.
    """
    adapter_cls: AdapterClass = _REGISTRY[config.adapter_type]

    _bus = bus if bus is not None else AsyncioQueueBus()
    graph: GraphBackend = _build_graph(config.causal_graph)
    for rel in config.causal_graph.relationships:
        graph.add_relation(rel.source, rel.target, rel.weight)

    history: SqliteSignalHistoryStore | None = None
    if config.inspector.signal_history_enabled:
        history = SqliteSignalHistoryStore(
            config.state_store.sqlite_path,
            config.inspector.signal_history_max_rows,
        )

    # Deliberately not gated by the history flag: disabling tracing must never
    # disable idempotency.
    ingestion = SqliteIngestionStore(
        config.state_store.sqlite_path,
        config.ingestion.dedupe_max_rows,
    )

    processor = SignalProcessor(
        _bus,
        store,
        scorer,
        graph,
        config.causal_graph,
        history=history,
        trace_store=history,
        dedupe=ingestion,
    )
    inspector = RevokInspector(store, cast(GraphReader, graph), history)

    async def _handle_get_inspector_entity(
        request: aiohttp.web.Request,
    ) -> aiohttp.web.Response:
        """GET /v1/inspector/entities/{entity_key} — return inspection report or 404/503."""
        if not config.inspector.enabled:
            return aiohttp.web.Response(
                status=503,
                content_type="application/json",
                body=json.dumps({"error": "inspector_disabled"}).encode(),
            )
        entity_key = request.match_info["entity_key"]
        report = await inspector.inspect_entity(entity_key)
        if report is None:
            return aiohttp.web.Response(
                status=404,
                content_type="application/json",
                body=json.dumps({"error": "not_found"}).encode(),
            )
        data = dataclasses.asdict(report)
        return aiohttp.web.Response(
            status=200,
            content_type="application/json",
            body=json.dumps(data).encode(),
        )

    async def _handle_get_inspector_downstream(
        request: aiohttp.web.Request,
    ) -> aiohttp.web.Response:
        """GET /v1/inspector/entities/{entity_key}/downstream."""
        if not config.inspector.enabled:
            return aiohttp.web.Response(
                status=503,
                content_type="application/json",
                body=json.dumps({"error": "inspector_disabled"}).encode(),
            )
        entity_key = request.match_info["entity_key"]
        record = await store.get(entity_key)
        if record is None:
            return aiohttp.web.Response(
                status=404,
                content_type="application/json",
                body=json.dumps({"error": "not_found"}).encode(),
            )
        downstream = await inspector.get_downstream(
            entity_key,
            max_hops=config.causal_graph.max_hops,
            min_pressure=config.causal_graph.min_pressure,
            attenuation=config.causal_graph.attenuation,
        )
        data = [dataclasses.asdict(item) for item in downstream]
        return aiohttp.web.Response(
            status=200,
            content_type="application/json",
            body=json.dumps(data).encode(),
        )

    async def _handle_get_inspector_paths(
        request: aiohttp.web.Request,
    ) -> aiohttp.web.Response:
        """GET /v1/inspector/entities/{entity_key}/paths."""
        if not config.inspector.enabled:
            return aiohttp.web.Response(
                status=503,
                content_type="application/json",
                body=json.dumps({"error": "inspector_disabled"}).encode(),
            )
        entity_key = request.match_info["entity_key"]
        record = await store.get(entity_key)
        if record is None:
            return aiohttp.web.Response(
                status=404,
                content_type="application/json",
                body=json.dumps({"error": "not_found"}).encode(),
            )
        paths = await inspector.get_paths(
            entity_key,
            max_hops=config.causal_graph.max_hops,
            min_pressure=config.causal_graph.min_pressure,
            attenuation=config.causal_graph.attenuation,
            max_paths=config.inspector.max_paths,
        )
        data = [dataclasses.asdict(item) for item in paths]
        return aiohttp.web.Response(
            status=200,
            content_type="application/json",
            body=json.dumps(data).encode(),
        )

    async def _handle_get_inspector_signals(
        request: aiohttp.web.Request,
    ) -> aiohttp.web.Response:
        """GET /v1/inspector/entities/{entity_key}/signals."""
        if not config.inspector.enabled:
            return aiohttp.web.Response(
                status=503,
                content_type="application/json",
                body=json.dumps({"error": "inspector_disabled"}).encode(),
            )
        entity_key = request.match_info["entity_key"]
        try:
            records = await inspector.get_signals(entity_key)
        except EntityNotFoundError:
            return aiohttp.web.Response(
                status=404,
                content_type="application/json",
                body=json.dumps({"error": "not_found"}).encode(),
            )
        if records is None:
            return aiohttp.web.Response(
                status=501,
                content_type="application/json",
                body=json.dumps({"error": "signal_history_disabled"}).encode(),
            )
        data = [dataclasses.asdict(item) for item in records]
        return aiohttp.web.Response(
            status=200,
            content_type="application/json",
            body=json.dumps(data).encode(),
        )

    async def _handle_get_inspector_graph(
        request: aiohttp.web.Request,
    ) -> aiohttp.web.Response:
        """GET /v1/inspector/graph — full causal graph topology snapshot."""
        if not config.inspector.enabled:
            return aiohttp.web.Response(
                status=503,
                content_type="application/json",
                body=json.dumps({"error": "inspector_disabled"}).encode(),
            )
        _reader = cast(GraphReader, graph)

        # FR-016: Synchronous snapshot — materialize nodes AND edges before any await.
        # No coroutine can mutate the graph between these synchronous calls.
        snapshot_keys = _reader.nodes()
        snapshot_key_set = set(snapshot_keys)
        edges_raw: list[GraphEdgeView] = []
        for src in snapshot_keys:
            for tgt in _reader.successors(src):
                if tgt not in snapshot_key_set:
                    continue  # edge to key added after snapshot — skip
                try:
                    w = _reader.edge_weight(src, tgt)
                except KeyError:
                    continue
                edges_raw.append(GraphEdgeView(source=src, target=tgt, weight=w))

        # Async score resolution over frozen node list (FR-014 priority chain)
        nodes: list[GraphNodeView] = []
        now = time.time()
        for key in snapshot_keys:
            record = await store.get(key)
            if record is not None:
                resolved_score = scorer.decay_at(record, now)
            else:
                try:
                    resolved_score = _reader.node_score(key)
                except KeyError:
                    resolved_score = 1.0
            nodes.append(GraphNodeView(key=key, score=resolved_score))

        topology = GraphTopologyResponse(nodes=nodes, edges=edges_raw)
        return aiohttp.web.Response(
            status=200,
            content_type="application/json",
            body=json.dumps(dataclasses.asdict(topology)).encode(),
        )

    async def _handle_get_inspector_viewer(
        _: aiohttp.web.Request,
    ) -> aiohttp.web.FileResponse:
        """GET /inspector — serve the read-only inspector placeholder page."""
        inspector_index = Path(__file__).parent / "inspector" / "index.html"
        if not inspector_index.is_file():
            raise aiohttp.web.HTTPNotFound()
        return aiohttp.web.FileResponse(path=inspector_index)

    async def _handle_get_entity(
        request: aiohttp.web.Request,
    ) -> aiohttp.web.Response:
        """GET /v1/entities/{entity_key} — return the entity record or 404."""
        entity_key = request.match_info["entity_key"]
        record = await store.get(entity_key)
        if record is None:
            return aiohttp.web.Response(
                status=404,
                content_type="application/json",
                body=json.dumps({"error": "not_found"}).encode(),
            )
        data = dataclasses.asdict(record)
        data.pop("last_value_fingerprint", None)
        data["score"] = scorer.decay_at(record, time.time())
        return aiohttp.web.Response(
            status=200,
            content_type="application/json",
            body=json.dumps(data).encode(),
        )

    async def _handle_delete_entity(
        request: aiohttp.web.Request,
    ) -> aiohttp.web.Response:
        """DELETE /v1/entities/{entity_key} — remove the entity record."""
        entity_key = request.match_info["entity_key"]
        deleted = await store.delete(entity_key)
        if not deleted:
            return aiohttp.web.Response(
                status=404,
                content_type="application/json",
                body=json.dumps({"error": "not_found"}).encode(),
            )
        return aiohttp.web.Response(
            status=200,
            content_type="application/json",
            body=json.dumps({"deleted": entity_key}).encode(),
        )

    async def _handle_list_entities(
        request: aiohttp.web.Request,
    ) -> aiohttp.web.Response:
        """GET /v1/entities — paginated list of all entity records."""
        try:
            offset = max(0, int(request.rel_url.query.get("offset", "0")))
            limit = min(max(1, int(request.rel_url.query.get("limit", "100"))), 500)
        except ValueError:
            return aiohttp.web.Response(
                status=400,
                content_type="application/json",
                body=json.dumps({"error": "invalid_query_params"}).encode(),
            )
        records = await store.list_all(offset=offset, limit=limit)
        return aiohttp.web.Response(
            status=200,
            content_type="application/json",
            body=json.dumps([dataclasses.asdict(r) for r in records]).encode(),
        )

    async def _handle_signal(
        request: aiohttp.web.Request,
    ) -> aiohttp.web.Response:
        """POST /signals — accept an external world-signal and publish to the bus.

        Accepts JSON body::

            {"entity_refs": [...], "severity": "high", "source": "webhook", "payload": {}}

        Returns 202 immediately.  Processing is fully async via *_bus*.
        Does NOT call ``enrich()`` or touch the Mem0 write path.
        """
        body_bytes = await request.read()
        try:
            parsed: dict = json.loads(body_bytes) if body_bytes else {}
        except (json.JSONDecodeError, ValueError) as exc:
            return aiohttp.web.Response(
                status=400,
                content_type="application/json",
                body=json.dumps({"error": "invalid_body", "detail": str(exc)}).encode(),
            )

        if not isinstance(parsed, dict):
            return aiohttp.web.Response(
                status=400,
                content_type="application/json",
                body=json.dumps({"error": "invalid_body"}).encode(),
            )
        entity_refs_value = parsed.get("entity_refs")
        if entity_refs_value is not None and not isinstance(entity_refs_value, list):
            return aiohttp.web.Response(
                status=400,
                content_type="application/json",
                body=json.dumps({"error": "invalid_entity_refs"}).encode(),
            )
        entity_refs: list[str] = entity_refs_value or []
        signal_text = str(parsed.get("signal_text") or "").strip()
        source: str = str(parsed.get("source") or "webhook")
        signal_id = str(parsed.get("signal_id") or uuid.uuid4())
        resolved_targets = []
        dropped_targets = []
        created_at = time.time()

        async def _write_trace(trace: ResolutionTrace) -> None:
            if history is None:
                return
            await history.start_trace(trace)

        if signal_text and not entity_refs:
            pending_trace = ResolutionTrace(
                signal_id=signal_id,
                created_at=created_at,
                completed_at=None,
                source_id=source,
                signal_text=signal_text,
                status="pending",
                error_code=None,
                error_detail=None,
                targets=[],
                dropped_targets=[],
                invalidations=[],
                propagation=[],
            )
            await _write_trace(pending_trace)
            if resolver is None:
                failed = dataclasses.replace(
                    pending_trace,
                    completed_at=time.time(),
                    status="failed",
                    error_code="resolver_not_configured",
                )
                if history is not None:
                    await history.finish_trace(failed)
                return aiohttp.web.Response(
                    status=400,
                    content_type="application/json",
                    body=json.dumps(
                        {"error": "resolver_not_configured", "signal_id": signal_id}
                    ).encode(),
                )
            try:
                validation = await invoke_resolver(
                    resolver,
                    signal_text,
                    cast(GraphReader, graph),
                )
                resolved_targets = validation.targets
                dropped_targets = validation.dropped_targets
            except Exception as exc:
                failed = dataclasses.replace(
                    pending_trace,
                    completed_at=time.time(),
                    status="failed",
                    error_code="resolver_failed",
                    error_detail=str(exc),
                )
                if history is not None:
                    await history.finish_trace(failed)
                return aiohttp.web.Response(
                    status=502,
                    content_type="application/json",
                    body=json.dumps(
                        {"error": "resolver_failed", "signal_id": signal_id}
                    ).encode(),
                )
            if not resolved_targets:
                completed = dataclasses.replace(
                    pending_trace,
                    completed_at=time.time(),
                    status="completed",
                    dropped_targets=dropped_targets,
                )
                if history is not None:
                    await history.finish_trace(completed)
                return aiohttp.web.Response(
                    status=202,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "accepted": True,
                            "signal_id": signal_id,
                            "resolved_targets": 0,
                        }
                    ).encode(),
                )

        raw_content = (
            " ".join(entity_refs)
            if entity_refs
            else signal_text or body_bytes.decode("utf-8", errors="replace")
        )

        signal = Signal(
            raw_content=raw_content,
            source_id=source,
            timestamp=time.time(),
            http_method="POST",
            http_path="/signals",
            original_body=body_bytes,
            headers=dict(request.headers),
            signal_id=signal_id,
            signal_text=signal_text or None,
            resolved_targets=tuple(resolved_targets),
            dropped_targets=tuple(dropped_targets),
        )
        await _bus.publish(signal)
        return aiohttp.web.Response(
            status=202,
            content_type="application/json",
            body=json.dumps(
                {
                    "accepted": True,
                    "signal_id": signal_id,
                    "resolved_targets": len(resolved_targets) if signal_text else len(entity_refs),
                }
            ).encode(),
        )

    async def _handle_relationship(request: aiohttp.web.Request) -> aiohttp.web.Response:
        """POST /v1/graph/relationships — idempotently register a causal edge."""
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return aiohttp.web.json_response({"error": "invalid_body"}, status=400)
        if not isinstance(body, dict):
            return aiohttp.web.json_response({"error": "invalid_body"}, status=400)
        source = str(body.get("source") or "").strip().lower()
        target = str(body.get("target") or "").strip().lower()
        weight_raw = body.get("weight")
        if not isinstance(weight_raw, (int, float, str)):
            return aiohttp.web.json_response({"error": "invalid_weight"}, status=400)
        try:
            weight = float(weight_raw)
        except (TypeError, ValueError):
            return aiohttp.web.json_response({"error": "invalid_weight"}, status=400)
        if not source or not target or not 0.0 < weight <= 1.0:
            return aiohttp.web.json_response({"error": "invalid_relationship"}, status=400)
        graph.add_relation(source, target, weight)
        return aiohttp.web.json_response(
            {"source": source, "target": target, "weight": weight}, status=200
        )

    async def _handle_list_resolver_traces(request: aiohttp.web.Request) -> aiohttp.web.Response:
        """GET /v1/resolver/traces — list durable resolver traces."""
        if not config.inspector.enabled:
            return aiohttp.web.json_response({"error": "inspector_disabled"}, status=503)
        if history is None:
            return aiohttp.web.json_response({"error": "signal_history_disabled"}, status=501)
        try:
            offset = max(0, int(request.rel_url.query.get("offset", "0")))
            limit = min(max(1, int(request.rel_url.query.get("limit", "100"))), 500)
        except ValueError:
            return aiohttp.web.json_response({"error": "invalid_query_params"}, status=400)
        traces = await history.list_traces(offset=offset, limit=limit + 1)
        has_more = len(traces) > limit
        items = traces[:limit]
        return aiohttp.web.json_response(
            {
                "items": [dataclasses.asdict(trace) for trace in items],
                "offset": offset,
                "limit": limit,
                "has_more": has_more,
            }
        )

    async def _handle_get_resolver_trace(request: aiohttp.web.Request) -> aiohttp.web.Response:
        """GET /v1/resolver/traces/{signal_id} — return one resolver trace."""
        if not config.inspector.enabled:
            return aiohttp.web.json_response({"error": "inspector_disabled"}, status=503)
        if history is None:
            return aiohttp.web.json_response({"error": "signal_history_disabled"}, status=501)
        trace = await history.get_trace(request.match_info["signal_id"])
        if trace is None:
            return aiohttp.web.json_response({"error": "not_found"}, status=404)
        return aiohttp.web.json_response(dataclasses.asdict(trace))

    async def _handle(request: aiohttp.web.Request) -> aiohttp.web.Response:
        """Catch-all request handler — intercept writes, pass through reads."""
        body_bytes: bytes = await request.read()
        http_path: str = str(request.rel_url)

        # T038: Reject payloads that exceed the configured inbound size limit (SC-010)
        if len(body_bytes) > config.server.max_signal_size_bytes:
            logger.warning(
                "Signal payload too large: %d bytes (limit=%d); returning 413 "
                "(path=%s, method=%s)",
                len(body_bytes),
                config.server.max_signal_size_bytes,
                http_path,
                request.method,
            )
            return aiohttp.web.Response(
                status=413,
                content_type="application/json",
                body=json.dumps(
                    {
                        "error": "payload_too_large",
                        "detail": (
                            f"Request body exceeds maximum size of "
                            f"{config.server.max_signal_size_bytes} bytes."
                        ),
                    }
                ).encode(),
            )

        # FR-004: Normalise raw request into Signal before any other processing
        source_id, raw_content = adapter_cls.extract_signal_context(
            request.path, dict(request.headers), body_bytes
        )

        signal = Signal(
            raw_content=raw_content,
            source_id=source_id,
            timestamp=time.time(),
            http_method=request.method,
            http_path=http_path,
            original_body=body_bytes,
            headers=dict(request.headers),
        )

        async with aiohttp.ClientSession() as session:
            _adapter = build_adapter(config.adapter_type, config.upstream, session)
            is_write = adapter_cls.is_write_request(
                signal.http_method, request.path, config.upstream
            )

            if is_write:
                # T041: Guard — only enrich if the body is parseable as JSON
                try:
                    json.loads(body_bytes)
                except (json.JSONDecodeError, ValueError):
                    logger.warning(
                        "Write request body is not valid JSON; forwarding raw to upstream "
                        "(path=%s, size=%d bytes)",
                        http_path,
                        len(body_bytes),
                    )
                    result = await _adapter.forward(signal)
                else:
                    # enrich() is internally resilient and never raises (scenario 1.4)
                    enriched = await enrich(signal, matcher, scorer, store)
                    result = await _adapter.write(enriched, http_path)
            else:
                result = await _adapter.forward(signal)

        # Strip hop-by-hop headers before returning to client
        safe_headers = {
            k: v for k, v in result.headers.items() if k.lower() not in _HOP_BY_HOP
        }

        return aiohttp.web.Response(
            status=result.status,
            body=result.body,
            headers=safe_headers,
        )

    async def _handle_list_dead_letters(
        request: aiohttp.web.Request,
    ) -> aiohttp.web.Response:
        """GET /v1/signals/dead-letters — signals set aside after the attempt bound."""
        if not config.inspector.enabled:
            return aiohttp.web.json_response({"error": "inspector_disabled"}, status=503)
        try:
            offset = max(0, int(request.rel_url.query.get("offset", "0")))
            limit = min(max(1, int(request.rel_url.query.get("limit", "100"))), 500)
        except ValueError:
            return aiohttp.web.json_response({"error": "invalid_query_params"}, status=400)
        records = await ingestion.list_dead_letters(offset=offset, limit=limit + 1)
        has_more = len(records) > limit
        return aiohttp.web.json_response(
            {
                "items": [dataclasses.asdict(r) for r in records[:limit]],
                "offset": offset,
                "limit": limit,
                "has_more": has_more,
            }
        )

    async def _handle_get_dead_letter(
        request: aiohttp.web.Request,
    ) -> aiohttp.web.Response:
        """GET /v1/signals/dead-letters/{signal_id} — one set-aside signal."""
        if not config.inspector.enabled:
            return aiohttp.web.json_response({"error": "inspector_disabled"}, status=503)
        record = await ingestion.get_dead_letter(request.match_info["signal_id"])
        if record is None:
            return aiohttp.web.json_response({"error": "not_found"}, status=404)
        return aiohttp.web.json_response(dataclasses.asdict(record))

    app = aiohttp.web.Application(middlewares=[basic_auth_middleware])
    app[SIGNAL_PROCESSOR_TASK_KEY] = None

    runners: list[SourceRunner] = []
    if config.sources.http.enabled:
        runners.append(
            SourceRunner(
                HttpSignalSource(_bus),
                processor,
                name="http",
                max_concurrent=config.ingestion.max_concurrent_signals,
            )
        )
    if config.sources.redis_streams.enabled:
        try:
            from revok.redis_source import RedisStreamsSignalSource

            runners.append(
                SourceRunner(
                    RedisStreamsSignalSource(config.sources.redis_streams, ingestion),
                    processor,
                    name="redis_streams",
                    max_concurrent=config.ingestion.max_concurrent_signals,
                    resolver=resolver,
                    graph=cast(GraphReader, graph),
                )
            )
        except ImportError as exc:
            logger.error(
                "Durable signal source disabled; HTTP ingestion continues. %s", exc
            )
    supervisor = SourceSupervisor(runners)

    async def _on_startup(_: aiohttp.web.Application) -> None:
        if history is not None:
            await history.open()
        await ingestion.open()
        app[SIGNAL_PROCESSOR_TASK_KEY] = asyncio.create_task(supervisor.run())

    async def _on_cleanup(_: aiohttp.web.Application) -> None:
        task = app.get(SIGNAL_PROCESSOR_TASK_KEY)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await supervisor.close()
        await ingestion.close()
        if history is not None:
            await history.close()
        try:
            graph.close()
        except Exception:
            logger.exception("Failed to close graph backend")
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    app.router.add_get("/v1/entities/{entity_key}", _handle_get_entity)
    app.router.add_delete("/v1/entities/{entity_key}", _handle_delete_entity)
    app.router.add_get("/v1/entities", _handle_list_entities)
    app.router.add_get("/v1/inspector/entities/{entity_key}", _handle_get_inspector_entity)
    app.router.add_get(
        "/v1/inspector/entities/{entity_key}/downstream",
        _handle_get_inspector_downstream,
    )
    app.router.add_get(
        "/v1/inspector/entities/{entity_key}/paths",
        _handle_get_inspector_paths,
    )
    app.router.add_get(
        "/v1/inspector/entities/{entity_key}/signals",
        _handle_get_inspector_signals,
    )
    app.router.add_get("/v1/inspector/graph", _handle_get_inspector_graph)
    app.router.add_post("/v1/graph/relationships", _handle_relationship)
    app.router.add_get("/v1/resolver/traces", _handle_list_resolver_traces)
    app.router.add_get(
        "/v1/resolver/traces/{signal_id}",
        _handle_get_resolver_trace,
    )
    app.router.add_get("/v1/signals/dead-letters", _handle_list_dead_letters)
    app.router.add_get(
        "/v1/signals/dead-letters/{signal_id}",
        _handle_get_dead_letter,
    )
    app.router.add_get("/inspector", _handle_get_inspector_viewer)
    app.router.add_static(
        "/inspector/vendor",
        path=Path(__file__).parent / "inspector" / "vendor",
        show_index=False,
        follow_symlinks=False,
        append_version=False,
    )
    app.router.add_post("/signals", _handle_signal)
    app.router.add_route(aiohttp.hdrs.METH_ANY, "/{path_info:.*}", _handle)
    return app
