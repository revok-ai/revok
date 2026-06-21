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

"""Mem0 upstream adapter implementing the MemoryAdapter Protocol.

``Mem0Adapter`` uses ``aiohttp.ClientSession`` to forward enriched writes and
raw pass-through requests to the configured Mem0 base URL (FR-009–FR-011).
"""

from __future__ import annotations

import json
import logging
import urllib.parse

import aiohttp

from revok.config import UpstreamConfig
from revok.models import EnrichedPayload, MemoryAdapterResponse, Signal

logger = logging.getLogger(__name__)

_502_BODY: bytes = json.dumps(
    {"error": "upstream_unavailable", "detail": "Mem0 endpoint is not reachable"}
).encode()
_502_HEADERS: dict[str, str] = {"Content-Type": "application/json"}


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
        url = self._config.url.rstrip("/") + http_path
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
        url = self._config.url.rstrip("/") + signal.http_path
        headers = dict(signal.headers)
        # Rewrite Host to upstream host per proxy-api.md §Headers
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

    @staticmethod
    def _normalize_path(value: str) -> str:
        """Normalize configured/request paths for write-route comparison."""
        if value == "/":
            return "/"
        return value.rstrip("/")

    @classmethod
    def is_write_request(cls, method: str, path: str, config: UpstreamConfig) -> bool:
        """Return True if this request should trigger the enrichment pipeline.

        A request is a write when **all** of the following hold:

        1. The HTTP method is in ``config.write_methods``.
        2. The path equals a configured write_path (trailing-slash-normalised),
           or starts with ``write_path + "/"`` (boundary-aware prefix — allows
           ``PUT /memories/{id}`` while rejecting ``/memories2``).
        3. The segment immediately after the write_path boundary is not listed
           in ``config.read_subpaths`` (opt-in exclusion, e.g. ``["search"]``).
        """
        if method.upper() not in {m.upper() for m in config.write_methods}:
            return False

        normalized_path = cls._normalize_path(path)
        for configured in config.write_paths:
            norm_configured = cls._normalize_path(configured)
            if normalized_path == norm_configured:
                return True
            if normalized_path.startswith(norm_configured + "/"):
                # Check opt-in read_subpath exclusions on the immediate
                # segment after the write_path boundary (e.g. "/search").
                remainder = normalized_path[len(norm_configured):]
                for excluded in config.read_subpaths:
                    if remainder == "/" + excluded.lstrip("/"):
                        return False
                return True
        return False

    @classmethod
    def extract_signal_context(
        cls, path: str, headers: dict[str, str], body_bytes: bytes
    ) -> tuple[str, str]:
        """Return ``(source_id, raw_content)`` for Signal construction.

        For Mem0 mode the agent identity comes from the ``X-Agent-ID`` header
        and ``raw_content`` is the request body decoded as UTF-8.
        """
        source_id = headers.get("X-Agent-ID", "unknown")
        raw_content = body_bytes.decode("utf-8", errors="replace")
        return source_id, raw_content
