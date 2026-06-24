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
    """Upstream target and write-detection settings (Mem0 and Zep).

    Attributes:
        url: Validated HTTP/HTTPS base URL.
        write_methods: HTTP methods that trigger enrichment (e.g., ``["POST"]``).
        write_paths: URL path prefixes that trigger enrichment.
            Required (non-empty) for Mem0 mode. Optional for Zep mode
            (the active gate is the session-path anchor in the proxy).
    """

    url: str
    write_methods: list[str]
    write_paths: list[str] = field(default_factory=list)
    read_subpaths: list[str] = field(default_factory=list)


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
    fuzzy_match_threshold: float | None = None


@dataclass(frozen=True)
class ScoringConfig:
    """Exponential decay scoring parameters.

    Attributes:
        half_life_seconds: Decay half-life (must be > 0).
        signal_strength: Score boost per signal (must be > 0).
        score_cap: Maximum entity score (must be > 0).
        contradiction_window_seconds: Time window (seconds) within which two
            conflicting signals are treated as a contradiction (strict open
            interval: ``gap < window``). Defaults to 300.0 (5 minutes).
        contradiction_penalty: Additional score penalty applied on top of
            ``signal_strength`` when a contradiction is detected. Defaults to
            0.15.
    """

    half_life_seconds: float
    signal_strength: float
    score_cap: float
    contradiction_window_seconds: float = 300.0
    contradiction_penalty: float = 0.15
    signal_pressure: "SignalPressureConfig" | None = None


@dataclass(frozen=True)
class SignalPressureConfig:
    """Severity-to-pressure mapping and fallback defaults.

    Attributes:
        severity_weights: Mapping from severity label to pressure multiplier in [0, 1].
        default_severity: Severity label used when a signal omits or uses unknown severity.
    """

    severity_weights: dict[str, float] = field(default_factory=dict)
    default_severity: str = "medium"

    def resolve(self, severity: str | None) -> float:
        """Resolve a severity label to a pressure value.

        Unknown or empty severities fall back to ``default_severity`` and then to 0.3.
        """
        key = (severity or "").strip().lower()
        if key and key in self.severity_weights:
            return self.severity_weights[key]
        default_key = self.default_severity.strip().lower()
        if default_key in self.severity_weights:
            return self.severity_weights[default_key]
        return 0.3


@dataclass(frozen=True)
class CausalGraphConfig:
    """Causal propagation controls.

    Attributes:
        enabled: Enables async signal-driven propagation.
        max_hops: Maximum BFS depth from root entities.
        min_pressure: Minimum pressure retained during propagation.
        attenuation: Per-hop attenuation multiplier.
        processing_timeout_seconds: Bounded processing timeout for one signal batch.
        relationships: Directed weighted edges loaded at startup.
    """

    enabled: bool = False
    max_hops: int = 2
    min_pressure: float = 0.05
    attenuation: float = 0.8
    processing_timeout_seconds: float = 2.0
    relationships: list["CausalRelationshipConfig"] = field(default_factory=list)


@dataclass(frozen=True)
class CausalRelationshipConfig:
    """Directed weighted relation between two entity keys."""

    source: str
    target: str
    weight: float = 1.0


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
class InspectorConfig:
    """Inspector API configuration.

    Attributes:
        enabled: Enables the Inspector read API endpoints.
        signal_history_enabled: Enables SQLite-backed signal event history.
        signal_history_max_rows: Maximum number of signal events retained per entity.
            Oldest rows are deleted automatically after each insert. Must be > 0.
        max_paths: Maximum number of propagation paths returned by the ``/paths`` endpoint.
            Must be > 0.
    """

    enabled: bool = True
    signal_history_enabled: bool = True
    signal_history_max_rows: int = 10_000
    max_paths: int = 100


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
    causal_graph: CausalGraphConfig = field(default_factory=CausalGraphConfig)
    inspector: InspectorConfig = field(default_factory=InspectorConfig)


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
    up_raw = data.get("upstream")
    if up_raw is None:
        raise ConfigError("Missing required config key: upstream")
    if not isinstance(up_raw, dict):
        raise ConfigError("'upstream' must be a YAML mapping.")

    up_url = _require(up_raw, "url", context="upstream")
    write_methods_up = _require(up_raw, "write_methods", context="upstream")

    parsed_up = urllib.parse.urlparse(str(up_url))
    if parsed_up.scheme not in ("http", "https") or not parsed_up.netloc:
        raise ConfigError(
            f"upstream.url must be a valid HTTP/HTTPS URL; got '{up_url}'."
        )
    if not isinstance(write_methods_up, list) or not write_methods_up:
        raise ConfigError("upstream.write_methods must be a non-empty list.")

    write_paths_up = up_raw.get("write_paths") or []
    if not isinstance(write_paths_up, list):
        raise ConfigError("upstream.write_paths must be a list.")
    if adapter_type == "mem0" and not write_paths_up:
        raise ConfigError("upstream.write_paths must be a non-empty list.")

    read_subpaths_up = up_raw.get("read_subpaths") or []
    if not isinstance(read_subpaths_up, list):
        raise ConfigError("upstream.read_subpaths must be a list.")

    upstream_cfg = UpstreamConfig(
        url=str(up_url).rstrip("/"),
        write_methods=[str(m).upper() for m in write_methods_up],
        write_paths=[str(p) for p in write_paths_up],
        read_subpaths=[str(p) for p in read_subpaths_up],
    )

    # --- entity_matcher (optional) ---
    # Absent → header-only mode; present → validate entities and/or patterns.
    em_raw = data.get("entity_matcher")
    entity_defs: list[EntityDef] = []
    pattern_cfgs: list[PatternConfig] = []
    fuzzy_threshold: float | None = None

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

        fuzzy_raw = em_raw.get("fuzzy_match_threshold")
        if fuzzy_raw is not None:
            if not isinstance(fuzzy_raw, (int, float)) or not (
                0 <= float(fuzzy_raw) <= 100
            ):
                raise ConfigError(
                    f"entity_matcher.fuzzy_match_threshold must be between 0 and "
                    f"100, got {fuzzy_raw}"
                )
            fuzzy_threshold = float(fuzzy_raw)

    entity_matcher_cfg = EntityMatcherConfig(
        entities=entity_defs,
        patterns=pattern_cfgs,
        fuzzy_match_threshold=fuzzy_threshold,
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

    contradiction_window = sc.get("contradiction_window_seconds", 300.0)
    contradiction_penalty_val = sc.get("contradiction_penalty", 0.15)

    signal_pressure_raw = sc.get("signal_pressure") or {}
    if not isinstance(signal_pressure_raw, dict):
        raise ConfigError("scoring.signal_pressure must be a mapping when provided.")
    severity_weights_raw = signal_pressure_raw.get("severity_weights") or {}
    if not isinstance(severity_weights_raw, dict):
        raise ConfigError("scoring.signal_pressure.severity_weights must be a mapping.")
    severity_weights: dict[str, float] = {}
    for k, v in severity_weights_raw.items():
        key = str(k).strip().lower()
        if not key:
            raise ConfigError("scoring.signal_pressure.severity_weights keys must be non-empty.")
        if not isinstance(v, (int, float)) or float(v) < 0.0 or float(v) > 1.0:
            raise ConfigError(
                "scoring.signal_pressure.severity_weights values must be numbers in [0, 1]."
            )
        severity_weights[key] = float(v)
    default_severity = str(signal_pressure_raw.get("default_severity") or "medium").strip().lower()
    if not default_severity:
        raise ConfigError("scoring.signal_pressure.default_severity must be non-empty.")

    signal_pressure_cfg = SignalPressureConfig(
        severity_weights=severity_weights,
        default_severity=default_severity,
    )

    scoring_cfg = ScoringConfig(
        half_life_seconds=float(half_life),
        signal_strength=float(signal_strength),
        score_cap=float(score_cap),
        contradiction_window_seconds=float(contradiction_window),
        contradiction_penalty=float(contradiction_penalty_val),
        signal_pressure=signal_pressure_cfg,
    )

    # --- causal_graph (optional) ---
    cg_raw = data.get("causal_graph") or {}
    if not isinstance(cg_raw, dict):
        raise ConfigError("causal_graph must be a mapping when provided.")

    cg_enabled = bool(cg_raw.get("enabled", False))
    cg_max_hops = cg_raw.get("max_hops", 2)
    cg_min_pressure = cg_raw.get("min_pressure", 0.05)
    cg_attenuation = cg_raw.get("attenuation", 0.8)
    cg_timeout = cg_raw.get("processing_timeout_seconds", 2.0)

    if not isinstance(cg_max_hops, int) or cg_max_hops < 0:
        raise ConfigError("causal_graph.max_hops must be an integer >= 0.")
    if not isinstance(cg_min_pressure, (int, float)) or not (0.0 <= float(cg_min_pressure) <= 1.0):
        raise ConfigError("causal_graph.min_pressure must be in [0, 1].")
    if not isinstance(cg_attenuation, (int, float)) or not (0.0 < float(cg_attenuation) <= 1.0):
        raise ConfigError("causal_graph.attenuation must be in (0, 1].")
    if not isinstance(cg_timeout, (int, float)) or float(cg_timeout) <= 0.0:
        raise ConfigError("causal_graph.processing_timeout_seconds must be > 0.")

    relationships_raw = cg_raw.get("relationships") or []
    if not isinstance(relationships_raw, list):
        raise ConfigError("causal_graph.relationships must be a list.")
    relationships: list[CausalRelationshipConfig] = []
    for i, rel in enumerate(relationships_raw):
        if not isinstance(rel, dict):
            raise ConfigError(f"causal_graph.relationships[{i}] must be a mapping.")
        source = rel.get("source")
        target = rel.get("target")
        if not source or not target:
            raise ConfigError(
                f"causal_graph.relationships[{i}] must include source and target."
            )
        weight = rel.get("weight", 1.0)
        if not isinstance(weight, (int, float)) or float(weight) <= 0.0 or float(weight) > 1.0:
            raise ConfigError(
                f"causal_graph.relationships[{i}].weight must be in (0, 1]."
            )
        relationships.append(
            CausalRelationshipConfig(
                source=str(source).strip().lower(),
                target=str(target).strip().lower(),
                weight=float(weight),
            )
        )

    causal_graph_cfg = CausalGraphConfig(
        enabled=cg_enabled,
        max_hops=int(cg_max_hops),
        min_pressure=float(cg_min_pressure),
        attenuation=float(cg_attenuation),
        processing_timeout_seconds=float(cg_timeout),
        relationships=relationships,
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

    # --- inspector (optional) ---
    insp_raw = data.get("inspector") or {}
    if not isinstance(insp_raw, dict):
        raise ConfigError("inspector must be a mapping when provided.")

    insp_enabled = bool(insp_raw.get("enabled", True))
    insp_history_enabled = bool(insp_raw.get("signal_history_enabled", True))
    insp_history_max_rows = insp_raw.get("signal_history_max_rows", 10_000)
    insp_max_paths = insp_raw.get("max_paths", 100)

    if not isinstance(insp_history_max_rows, int) or insp_history_max_rows <= 0:
        raise ConfigError("inspector.signal_history_max_rows must be a positive integer.")
    if not isinstance(insp_max_paths, int) or insp_max_paths <= 0:
        raise ConfigError("inspector.max_paths must be a positive integer.")

    inspector_cfg = InspectorConfig(
        enabled=insp_enabled,
        signal_history_enabled=insp_history_enabled,
        signal_history_max_rows=int(insp_history_max_rows),
        max_paths=int(insp_max_paths),
    )

    return Config(
        server=server_cfg,
        upstream=upstream_cfg,
        entity_matcher=entity_matcher_cfg,
        scoring=scoring_cfg,
        state_store=state_store_cfg,
        logging=logging_cfg,
        adapter_type=adapter_type,
        causal_graph=causal_graph_cfg,
        inspector=inspector_cfg,
    )
