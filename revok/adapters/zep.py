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

"""Zep Community Edition upstream adapter implementing the MemoryAdapter Protocol.

``ZepAdapter`` intercepts Zep memory write paths
(``POST /api/v1/sessions/{sessionId}/memory``), extracts entity references,
updates confidence scores, and proxies the request byte-identical to Zep CE.
All other paths are forwarded unchanged via ``forward()``.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse

import aiohttp

from revok.config import UpstreamConfig
from revok.models import EnrichedPayload, MemoryAdapterResponse, Signal

logger = logging.getLogger(__name__)

_502_BODY: bytes = json.dumps(
    {"error": "upstream_unavailable", "detail": "Zep endpoint is not reachable"}
).encode()
_502_HEADERS: dict[str, str] = {"Content-Type": "application/json"}

# Anchored regex for the Zep CE session-memory write endpoint.
_SESSION_MEMORY_RE: re.Pattern[str] = re.compile(r"^/api/v1/sessions/([^/]+)/memory$")


def _extract_session_id(path: str) -> str | None:
    """Extract the session ID from a Zep CE memory path.

    Only the exact path ``/api/v1/sessions/{sessionId}/memory`` matches.
    Any prefix, suffix, or query-string variant returns ``None``.

    Args:
        path: HTTP request path (with or without query string).

    Returns:
        The session ID string if the path is an exact match, else ``None``.
    """
    # Strip query string before matching
    bare = path.split("?")[0]
    match = _SESSION_MEMORY_RE.match(bare)
    return match.group(1) if match else None


class ZepAdapter:
    """Upstream Zep CE HTTP adapter implementing the MemoryAdapter Protocol.

    Intercepts ``POST /api/v1/sessions/{sessionId}/memory`` and forwards
    the original request bytes unchanged to Zep CE after the enrichment
    pipeline has scored entities.  All other requests are forwarded
    verbatim via :meth:`forward`.

    Attributes:
        _config: Zep CE upstream configuration.
        _session: Shared aiohttp client session (caller-managed lifetime).
        _closed: Whether ``close()`` has already been called.
    """

    def __init__(self, config: UpstreamConfig, session: aiohttp.ClientSession) -> None:
        """Initialise the adapter.

        Args:
            config: Zep CE URL and write-detection settings.
            session: aiohttp client session for all upstream requests.
        """
        self._config = config
        self._session = session
        self._closed = False

    async def write(
        self,
        payload: EnrichedPayload,
        http_path: str = "/",
    ) -> MemoryAdapterResponse:
        """Forward a Zep memory write byte-identical to the Zep CE upstream.

        The original request body (``payload.original_bytes``) is sent
        without any modification — Revok does NOT call
        :meth:`~revok.models.EnrichedPayload.to_upstream_dict`.

        Args:
            payload: Enriched payload whose ``original_bytes`` are forwarded.
            http_path: Original request path including any query string.

        Returns:
            :class:`~revok.models.MemoryAdapterResponse` from Zep CE.
            Never raises on upstream HTTP or connection errors.
        """
        session_id = _extract_session_id(http_path)
        logger.debug(
            "Intercepted Zep write",
            extra={"session_id": session_id, "http_path": http_path},
        )
        url = self._config.url.rstrip("/") + http_path
        try:
            async with self._session.post(
                url,
                data=payload.original_bytes,
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
            logger.error("Zep upstream unreachable at %s", url)
            return MemoryAdapterResponse(
                status=502,
                body=_502_BODY,
                headers=_502_HEADERS,
                is_error=True,
            )

    async def forward(self, signal: Signal) -> MemoryAdapterResponse:
        """Forward a raw signal to Zep CE unchanged.

        The original request body, method, and all headers (except ``Host``,
        which is rewritten to the Zep CE upstream host) are forwarded verbatim.

        Args:
            signal: The original signal including method, path, headers, body.

        Returns:
            :class:`~revok.models.MemoryAdapterResponse` from Zep CE.
            Never raises on upstream HTTP or connection errors.
        """
        url = self._config.url.rstrip("/") + signal.http_path
        headers = dict(signal.headers)
        parsed = urllib.parse.urlparse(self._config.url)
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
            logger.error("Zep upstream unreachable at %s", url)
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

    @classmethod
    def is_write_request(cls, method: str, path: str, config: UpstreamConfig) -> bool:
        """Return True if this request should trigger the enrichment pipeline.

        For Zep mode a request is a write when the path is the exact
        session-memory endpoint *and* the HTTP method is in
        ``config.write_methods``.
        """
        return _extract_session_id(path) is not None and method.upper() in {
            m.upper() for m in config.write_methods
        }

    @classmethod
    def extract_signal_context(
        cls, path: str, headers: dict[str, str], body_bytes: bytes
    ) -> tuple[str, str]:
        """Return ``(source_id, raw_content)`` for Signal construction.

        For Zep mode ``source_id`` is the session ID extracted from the path
        (or ``X-Agent-ID`` as a fallback).  ``raw_content`` is assembled by
        joining ``messages[].content`` fields when the body is a Zep memory
        payload, or falls back to the raw UTF-8 body.
        """
        sid = _extract_session_id(path)
        source_id = sid or headers.get("X-Agent-ID") or "unknown"
        raw_content: str
        try:
            if body_bytes:
                parsed = json.loads(body_bytes)
                if isinstance(parsed, dict) and isinstance(
                    parsed.get("messages"), list
                ):
                    raw_content = " ".join(
                        str(m["content"])
                        for m in parsed["messages"]
                        if isinstance(m, dict) and m.get("content")
                    )
                else:
                    raw_content = body_bytes.decode("utf-8", errors="replace")
            else:
                raw_content = ""
        except (json.JSONDecodeError, ValueError):
            raw_content = body_bytes.decode("utf-8", errors="replace")
        return source_id, raw_content
