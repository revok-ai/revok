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

import dataclasses
import json
import logging
import time
import urllib.parse

import aiohttp
import aiohttp.hdrs
import aiohttp.web

from revok.config import Config, UpstreamConfig
from revok.entity_matcher import EntityMatcher
from revok.interfaces import StateStore
from revok.metadata_writer import enrich
from revok.models import EnrichedPayload, MemoryAdapterResponse, Signal
from revok.scoring import ScoringEngine

logger = logging.getLogger(__name__)

_502_BODY: bytes = json.dumps(
    {"error": "upstream_unavailable", "detail": "Mem0 endpoint is not reachable"}
).encode()
_502_HEADERS: dict[str, str] = {"Content-Type": "application/json"}

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


class Mem0Adapter:
    """Upstream Mem0 HTTP adapter implementing the MemoryAdapter Protocol.

    Uses ``aiohttp.ClientSession`` to forward enriched writes and raw
    pass-through requests to the configured Mem0 base URL.

    Attributes:
        _config: Upstream Mem0 configuration.
        _session: Shared aiohttp client session (caller-managed lifetime).
        _closed: Whether ``close()`` has already been called.
    """

    def __init__(self, config: UpstreamConfig, session: aiohttp.ClientSession) -> None:
        """Initialise the adapter.

        Args:
            config: Upstream Mem0 URL and write-detection settings.
            session: aiohttp client session to use for all upstream requests.
        """
        self._config = config
        self._session = session
        self._closed = False

    async def write(
        self,
        payload: EnrichedPayload,
        http_path: str = "/",
    ) -> MemoryAdapterResponse:
        """POST an enriched payload to Mem0 at ``mem0_url + http_path``.

        The payload is serialised to JSON via
        :meth:`~revok.models.EnrichedPayload.to_upstream_dict`.

        Args:
            payload: Enriched payload ready for serialisation and forwarding.
            http_path: Original request path (may include query string).

        Returns:
            :class:`~revok.models.MemoryAdapterResponse` from Mem0.
            Never raises on upstream HTTP or connection errors.
        """
        url = self._config.mem0_url.rstrip("/") + http_path
        body_bytes = json.dumps(payload.to_upstream_dict()).encode()
        try:
            async with self._session.post(
                url,
                data=body_bytes,
                headers={"Content-Type": "application/json"},
                allow_redirects=False,
            ) as resp:
                resp_body = await resp.read()
                return MemoryAdapterResponse(
                    status=resp.status,
                    body=resp_body,
                    headers=dict(resp.headers),
                    is_error=resp.status >= 400,
                )
        except aiohttp.ClientConnectorError:
            logger.error("Mem0 upstream unreachable at %s", url)
            return MemoryAdapterResponse(
                status=502,
                body=_502_BODY,
                headers=_502_HEADERS,
                is_error=True,
            )

    async def forward(self, signal: Signal) -> MemoryAdapterResponse:
        """Forward a raw signal to Mem0 unchanged.

        The original request body, method, and all headers (except ``Host``,
        which is rewritten to the upstream host) are forwarded verbatim.

        Args:
            signal: The original signal including method, path, headers, body.

        Returns:
            :class:`~revok.models.MemoryAdapterResponse` from Mem0.
            Never raises on upstream HTTP or connection errors.
        """
        url = self._config.mem0_url.rstrip("/") + signal.http_path
        headers = dict(signal.headers)
        # Rewrite Host to upstream host per proxy-api.md §Headers
        parsed = urllib.parse.urlparse(self._config.mem0_url)
        headers["Host"] = parsed.netloc

        body = signal.original_body if signal.original_body else None

        try:
            async with self._session.request(
                method=signal.http_method,
                url=url,
                data=body,
                headers=headers,
                allow_redirects=False,
            ) as resp:
                resp_body = await resp.read()
                return MemoryAdapterResponse(
                    status=resp.status,
                    body=resp_body,
                    headers=dict(resp.headers),
                    is_error=resp.status >= 400,
                )
        except aiohttp.ClientConnectorError:
            logger.error("Mem0 upstream unreachable at %s", url)
            return MemoryAdapterResponse(
                status=502,
                body=_502_BODY,
                headers=_502_HEADERS,
                is_error=True,
            )

    async def close(self) -> None:
        """Close the underlying HTTP session (idempotent)."""
        if not self._closed:
            await self._session.close()
            self._closed = True


def build_app(
    config: Config,
    store: StateStore,
    matcher: EntityMatcher,
    scorer: ScoringEngine,
) -> aiohttp.web.Application:
    """Build and return the Revok aiohttp proxy application.

    The application has a single catch-all route that:

    1. Normalises every incoming request into a
       :class:`~revok.models.Signal` (FR-004).
    2. If the method + path match a configured write trigger: enriches the
       signal via :func:`~revok.metadata_writer.enrich` and POSTs the
       enriched payload to Mem0.
    3. Otherwise: forwards the raw request to Mem0 unchanged.
    4. Returns Mem0's response (status + headers + body) to the caller.
    5. Returns ``502 Bad Gateway`` on
       ``aiohttp.ClientConnectorError`` (proxy-api.md §Error Semantics).

    Args:
        config: Full Revok configuration.
        store: Persistent entity state store.
        matcher: Named-regex entity extractor.
        scorer: Exponential decay scoring engine.

    Returns:
        :class:`aiohttp.web.Application` ready for
        :class:`aiohttp.web.AppRunner`.
    """
    write_methods: frozenset[str] = frozenset(
        m.upper() for m in config.upstream.write_methods
    )

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
        return aiohttp.web.Response(
            status=200,
            content_type="application/json",
            body=json.dumps(dataclasses.asdict(record)).encode(),
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

    async def _handle(request: aiohttp.web.Request) -> aiohttp.web.Response:
        """Catch-all request handler — intercept writes, pass through reads."""
        body_bytes: bytes = await request.read()
        http_path: str = str(request.rel_url)

        # FR-004: Normalise raw request into Signal before any other processing
        signal = Signal(
            raw_content=body_bytes.decode("utf-8", errors="replace"),
            source_id=request.headers.get("X-Agent-ID", "unknown"),
            timestamp=time.time(),
            http_method=request.method,
            http_path=http_path,
            original_body=body_bytes,
            headers=dict(request.headers),
        )

        async with aiohttp.ClientSession() as session:
            adapter = Mem0Adapter(config.upstream, session)

            is_write = signal.http_method.upper() in write_methods and any(
                request.path.startswith(p) for p in config.upstream.write_paths
            )

            if is_write:
                # enrich() is internally resilient and never raises (scenario 1.4)
                enriched = await enrich(signal, matcher, scorer, store)
                result = await adapter.write(enriched, http_path)
            else:
                result = await adapter.forward(signal)

        # Strip hop-by-hop headers before returning to client
        safe_headers = {
            k: v
            for k, v in result.headers.items()
            if k.lower() not in _HOP_BY_HOP
        }

        return aiohttp.web.Response(
            status=result.status,
            body=result.body,
            headers=safe_headers,
        )

    app = aiohttp.web.Application()
    app.router.add_get("/v1/entities/{entity_key}", _handle_get_entity)
    app.router.add_get("/v1/entities", _handle_list_entities)
    app.router.add_route(aiohttp.hdrs.METH_ANY, "/{path_info:.*}", _handle)
    return app
