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

"""Configuration dataclasses and YAML loader for Revok.

Config structs are defined here. ``load_config()`` and ``ConfigError`` are
also defined here (added in Phase 3, T010-T012).
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Config dataclasses (Phase 2 — structs only)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ServerConfig:
    """Inbound proxy server settings.

    Attributes:
        host: Bind address.
        port: TCP port (1–65535).
        startup_timeout_seconds: Seconds to wait for socket bind before aborting.
        max_signal_size_bytes: Maximum request body size (bytes) before returning 413.
            Defaults to 1 MiB (1_048_576). Must be > 0.
    """

    host: str
    port: int
    startup_timeout_seconds: float
    max_signal_size_bytes: int = 1_048_576


@dataclass(frozen=True)
class UpstreamConfig:
    """Mem0 upstream target and write-detection settings.

    Attributes:
        mem0_url: Validated HTTP/HTTPS base URL.
        write_methods: HTTP methods that trigger enrichment (e.g., ``["POST"]``).
        write_paths: URL path prefixes that trigger enrichment.
    """

    mem0_url: str
    write_methods: list[str]
    write_paths: list[str]


@dataclass(frozen=True)
class ZepUpstreamConfig:
    """Zep CE upstream target and write-detection settings.

    Attributes:
        zep_url: Validated HTTP/HTTPS base URL for the Zep CE instance.
        write_methods: HTTP methods that trigger entity enrichment.
        write_paths: Informational path list.  The active write gate is the
            session-path anchor in the proxy, so this defaults to ``[]``.
    """

    zep_url: str
    write_methods: list[str]
    write_paths: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PatternConfig:
    """A single named regex extraction pattern.

    Attributes:
        name: Human-readable pattern name (e.g., ``"person"``).
        regex: Regular expression string. Validated as compilable at startup.
    """

    name: str
    regex: str


@dataclass(frozen=True)
class EntityDef:
    """A named entity with human-readable aliases for catalog-based extraction.

    Attributes:
        id: Canonical entity identifier used as the store key (e.g., ``"apex_hoodie"``).
        display_name: Human-readable label. Defaults to *id* when omitted.
        aliases: Non-empty list of literal strings that identify this entity in text.
            Matching is case-insensitive and word-boundary aware; no regex knowledge
            required.
    """

    id: str
    display_name: str
    aliases: list[str]


@dataclass(frozen=True)
class EntityMatcherConfig:
    """Entity extraction configuration.

    Supports two extraction modes — both may be active simultaneously:

    * **Catalog mode** (``entities``): list entities with plain-text aliases.
      Revok compiles word-boundary patterns internally. Best for demos and
      small known sets (\u2264 50 entities).
    * **Pattern mode** (``patterns``): raw named regex patterns for advanced
      use cases. Legacy format; still fully supported.

    For large catalogs or production deployments, omit both and send
    ``X-Revok-Entity: <id>`` with each write request \u2014 zero YAML config needed.

    Attributes:
        entities: Catalog entities with literal aliases.
        patterns: Legacy named regex patterns.
    """

    entities: list[EntityDef] = field(default_factory=list)
    patterns: list[PatternConfig] = field(default_factory=list)


@dataclass(frozen=True)
class ScoringConfig:
    """Exponential decay scoring parameters.

    Attributes:
        half_life_seconds: Decay half-life (must be > 0).
        signal_strength: Score boost per signal (must be > 0).
        score_cap: Maximum entity score (must be > 0).
    """

    half_life_seconds: float
    signal_strength: float
    score_cap: float


@dataclass(frozen=True)
class StateStoreConfig:
    """SQLite state store configuration.

    Attributes:
        sqlite_path: Filesystem path to the SQLite database file.
        hot_layer_max_entries: Maximum entries in the in-memory LRU hot layer.
    """

    sqlite_path: str
    hot_layer_max_entries: int


@dataclass(frozen=True)
class LoggingConfig:
    """Python logging configuration.

    Attributes:
        level: Log level string (e.g., ``"INFO"``).
        format: ``logging.basicConfig`` format string.
    """

    level: str
    format: str


@dataclass(frozen=True)
class Config:
    """Root configuration object. Loaded once at startup; immutable thereafter.

    Attributes:
        server: Inbound server settings.
        upstream: Upstream Mem0 settings.
        entity_matcher: Entity extraction settings.
        scoring: Scoring / decay settings.
        state_store: SQLite persistence settings.
        logging: Logging settings.
    """

    server: ServerConfig
    upstream: UpstreamConfig
    entity_matcher: EntityMatcherConfig
    scoring: ScoringConfig
    state_store: StateStoreConfig
    logging: LoggingConfig
    adapter_type: str = "mem0"
    zep: ZepUpstreamConfig | None = None


# ---------------------------------------------------------------------------
# ConfigError and load_config (Phase 3 — T010–T012)
# ---------------------------------------------------------------------------


class ConfigError(ValueError):
    """Raised when a configuration file is invalid or incomplete.

    Attributes:
        message: Human-readable description of the violation.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _require(mapping: dict[str, Any], *keys: str, context: str = "") -> Any:
    """Extract nested keys from a mapping, raising ConfigError if any is missing.

    Args:
        mapping: The dict to extract from.
        *keys: Sequence of keys forming the path (e.g., ``"server"``, ``"port"``).
        context: Optional prefix for error messages.

    Returns:
        The value at the final key.

    Raises:
        ConfigError: If any key in the chain is absent.
    """
    current: Any = mapping
    path = ""
    for key in keys:
        path = f"{path}.{key}" if path else key
        if not isinstance(current, dict) or key not in current:
            prefix = f"{context}." if context else ""
            raise ConfigError(f"Missing required config key: {prefix}{path}")
        current = current[key]
    return current


def load_config(path: str) -> Config:
    """Load and validate a Revok YAML configuration file.

    Args:
        path: Filesystem path to the YAML config file.

    Returns:
        Config: Fully populated, validated configuration object.

    Raises:
        ConfigError: On any validation failure or missing required key.
        OSError: If the file cannot be read.
    """
    raw_path = Path(path)
    try:
        text = raw_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Cannot read config file '{path}': {exc}") from exc

    try:
        data: Any = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"YAML parse error in '{path}': {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(
            f"Config file '{path}' must be a YAML mapping at the top level."
        )

    # --- adapter_type ---
    adapter_type = str(data.get("adapter_type") or "mem0").lower()
    if adapter_type not in ("mem0", "zep"):
        raise ConfigError(
            f"adapter_type must be 'mem0' or 'zep'; got '{adapter_type}'."
        )

    # --- server ---
    srv = _require(data, "server")
    host = _require(srv, "host", context="server")
    port = _require(srv, "port", context="server")
    startup_timeout = _require(srv, "startup_timeout_seconds", context="server")

    if not isinstance(port, int) or not (1 <= port <= 65535):
        raise ConfigError("server.port must be an integer between 1 and 65535.")
    if not isinstance(startup_timeout, (int, float)) or float(startup_timeout) <= 0:
        raise ConfigError("server.startup_timeout_seconds must be a positive number.")

    max_signal_size = srv.get("max_signal_size_bytes", 1_048_576)
    if not isinstance(max_signal_size, int) or max_signal_size <= 0:
        raise ConfigError("server.max_signal_size_bytes must be a positive integer.")

    server_cfg = ServerConfig(
        host=str(host),
        port=int(port),
        startup_timeout_seconds=float(startup_timeout),
        max_signal_size_bytes=int(max_signal_size),
    )

    # --- upstream ---
    # In Zep mode upstream is optional; a stub is built when the section is absent.
    up_raw = data.get("upstream")
    if up_raw is None and adapter_type == "mem0":
        raise ConfigError("Missing required config key: upstream")
    if up_raw is not None:
        mem0_url = _require(up_raw, "mem0_url", context="upstream")
        write_methods_up = _require(up_raw, "write_methods", context="upstream")
        write_paths_up = _require(up_raw, "write_paths", context="upstream")

        parsed_up = urllib.parse.urlparse(str(mem0_url))
        if parsed_up.scheme not in ("http", "https") or not parsed_up.netloc:
            raise ConfigError(
                f"upstream.mem0_url must be a valid HTTP/HTTPS URL; got '{mem0_url}'."
            )
        if not isinstance(write_methods_up, list) or not write_methods_up:
            raise ConfigError("upstream.write_methods must be a non-empty list.")
        if not isinstance(write_paths_up, list) or not write_paths_up:
            raise ConfigError("upstream.write_paths must be a non-empty list.")

        upstream_cfg = UpstreamConfig(
            mem0_url=str(mem0_url).rstrip("/"),
            write_methods=[str(m).upper() for m in write_methods_up],
            write_paths=[str(p) for p in write_paths_up],
        )
    else:
        # Zep mode with no upstream section: build a stub
        upstream_cfg = UpstreamConfig(
            mem0_url="http://unused",
            write_methods=[],
            write_paths=[],
        )

    # --- entity_matcher (optional) ---
    # Absent → header-only mode; present → validate entities and/or patterns.
    em_raw = data.get("entity_matcher")
    entity_defs: list[EntityDef] = []
    pattern_cfgs: list[PatternConfig] = []

    if em_raw is not None:
        if not isinstance(em_raw, dict):
            raise ConfigError("'entity_matcher' must be a YAML mapping.")

        # Catalog mode: alias entities
        entities_raw = em_raw.get("entities") or []
        if not isinstance(entities_raw, list):
            raise ConfigError("entity_matcher.entities must be a list.")
        for i, ent in enumerate(entities_raw):
            if not isinstance(ent, dict):
                raise ConfigError(f"entity_matcher.entities[{i}] must be a mapping.")
            eid = ent.get("id")
            if not eid:
                raise ConfigError(f"entity_matcher.entities[{i}].id is required.")
            aliases_raw = ent.get("aliases") or []
            if not isinstance(aliases_raw, list) or not aliases_raw:
                raise ConfigError(
                    f"entity_matcher.entities[{i}].aliases must be a non-empty list."
                )
            display_name = str(ent.get("display_name") or eid)
            entity_defs.append(
                EntityDef(
                    id=str(eid),
                    display_name=display_name,
                    aliases=[str(a) for a in aliases_raw],
                )
            )

        # Pattern mode: legacy named regex patterns
        patterns_raw = em_raw.get("patterns") or []
        if not isinstance(patterns_raw, list):
            raise ConfigError("entity_matcher.patterns must be a list.")
        for i, pat in enumerate(patterns_raw):
            if not isinstance(pat, dict):
                raise ConfigError(f"entity_matcher.patterns[{i}] must be a mapping.")
            name = pat.get("name")
            regex = pat.get("regex")
            if not name:
                raise ConfigError(f"entity_matcher.patterns[{i}].name is required.")
            if not regex:
                raise ConfigError(f"entity_matcher.patterns[{i}].regex is required.")
            try:
                re.compile(str(regex))
            except re.error as exc:
                raise ConfigError(
                    f"entity_matcher.patterns[{i}].regex is not a valid regex: {exc}"
                ) from exc
            pattern_cfgs.append(PatternConfig(name=str(name), regex=str(regex)))

    entity_matcher_cfg = EntityMatcherConfig(
        entities=entity_defs, patterns=pattern_cfgs
    )

    # --- scoring ---
    sc = _require(data, "scoring")
    half_life = _require(sc, "half_life_seconds", context="scoring")
    signal_strength = _require(sc, "signal_strength", context="scoring")
    score_cap = _require(sc, "score_cap", context="scoring")

    if not isinstance(half_life, (int, float)) or float(half_life) <= 0:
        raise ConfigError("scoring.half_life_seconds must be > 0.")
    if not isinstance(signal_strength, (int, float)) or float(signal_strength) <= 0:
        raise ConfigError("scoring.signal_strength must be > 0.")
    if not isinstance(score_cap, (int, float)) or float(score_cap) <= 0:
        raise ConfigError("scoring.score_cap must be > 0.")

    scoring_cfg = ScoringConfig(
        half_life_seconds=float(half_life),
        signal_strength=float(signal_strength),
        score_cap=float(score_cap),
    )

    # --- state_store ---
    ss = _require(data, "state_store")
    sqlite_path = _require(ss, "sqlite_path", context="state_store")
    hot_max = _require(ss, "hot_layer_max_entries", context="state_store")

    if not isinstance(hot_max, int) or hot_max <= 0:
        raise ConfigError(
            "state_store.hot_layer_max_entries must be a positive integer."
        )

    state_store_cfg = StateStoreConfig(
        sqlite_path=str(sqlite_path),
        hot_layer_max_entries=int(hot_max),
    )

    # --- logging ---
    lg = _require(data, "logging")
    level = _require(lg, "level", context="logging")
    fmt = _require(lg, "format", context="logging")

    logging_cfg = LoggingConfig(level=str(level), format=str(fmt))

    # --- zep ---
    zep_cfg: ZepUpstreamConfig | None = None
    if adapter_type == "zep":
        zep_raw = data.get("zep")
        if not isinstance(zep_raw, dict):
            raise ConfigError(
                "A 'zep' section is required when adapter_type is 'zep'."
            )
        zep_url = zep_raw.get("zep_url")
        if not zep_url:
            raise ConfigError("zep.zep_url is required when adapter_type is 'zep'.")
        parsed_zep = urllib.parse.urlparse(str(zep_url))
        if parsed_zep.scheme not in ("http", "https") or not parsed_zep.netloc:
            raise ConfigError(
                f"zep.zep_url must be a valid HTTP/HTTPS URL; got '{zep_url}'."
            )
        zep_wm = zep_raw.get("write_methods") or []
        if not isinstance(zep_wm, list) or not zep_wm:
            raise ConfigError("zep.write_methods must be a non-empty list.")
        zep_wp = zep_raw.get("write_paths") or []
        if not isinstance(zep_wp, list):
            raise ConfigError("zep.write_paths must be a list.")
        zep_cfg = ZepUpstreamConfig(
            zep_url=str(zep_url).rstrip("/"),
            write_methods=[str(m).upper() for m in zep_wm],
            write_paths=[str(p) for p in zep_wp],
        )

    return Config(
        server=server_cfg,
        upstream=upstream_cfg,
        entity_matcher=entity_matcher_cfg,
        scoring=scoring_cfg,
        state_store=state_store_cfg,
        logging=logging_cfg,
        adapter_type=adapter_type,
        zep=zep_cfg,
    )
