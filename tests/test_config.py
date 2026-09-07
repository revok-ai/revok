# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Revok Contributors
#
# Tests for revok.config — load_config() validation and ConfigError behaviour.

from __future__ import annotations

from pathlib import Path

import pytest

from revok.config import ConfigError, load_config


VALID_YAML = """\
server:
  host: "127.0.0.1"
  port: 8080
  startup_timeout_seconds: 10

upstream:
  url: "http://localhost:8000"
  write_methods: ["POST"]
  write_paths: ["/v1/memories"]

entity_matcher:
  patterns:
    - name: "person"
      regex: "\\\\b[A-Z][a-z]+ [A-Z][a-z]+\\\\b"

scoring:
  half_life_seconds: 86400
  signal_strength: 0.3
  score_cap: 1.0

state_store:
  sqlite_path: "./revok_state.db"
  hot_layer_max_entries: 1000

logging:
  level: "INFO"
  format: "%(asctime)s %(levelname)s %(message)s"
"""


def _write_yaml(tmp_path: Path, content: str) -> str:
    p = tmp_path / "revok.yaml"
    p.write_text(content, encoding="utf-8")
    return str(p)


class TestLoadConfigValid:
    def test_returns_config(self, tmp_path: Path):
        cfg = load_config(_write_yaml(tmp_path, VALID_YAML))
        assert cfg.server.host == "127.0.0.1"
        assert cfg.server.port == 8080
        assert cfg.server.startup_timeout_seconds == 10.0

    def test_upstream_url_stripped_of_trailing_slash(self, tmp_path: Path):
        yaml_content = VALID_YAML.replace(
            'url: "http://localhost:8000"',
            'url: "http://localhost:8000/"',
        )
        cfg = load_config(_write_yaml(tmp_path, yaml_content))
        assert not cfg.upstream.url.endswith("/")

    def test_write_methods_uppercased(self, tmp_path: Path):
        yaml_content = VALID_YAML.replace(
            'write_methods: ["POST"]', 'write_methods: ["post"]'
        )
        cfg = load_config(_write_yaml(tmp_path, yaml_content))
        assert cfg.upstream.write_methods == ["POST"]

    def test_patterns_loaded(self, tmp_path: Path):
        cfg = load_config(_write_yaml(tmp_path, VALID_YAML))
        assert len(cfg.entity_matcher.patterns) == 1
        assert cfg.entity_matcher.patterns[0].name == "person"

    def test_entities_catalog_loaded(self, tmp_path: Path):
        yaml = VALID_YAML.replace(
            "entity_matcher:\n"
            "  patterns:\n"
            '    - name: "person"\n'
            '      regex: "\\\\b[A-Z][a-z]+ [A-Z][a-z]+\\\\b"',
            "entity_matcher:\n"
            "  entities:\n"
            "    - id: apex_hoodie\n"
            "      display_name: Apex Hoodie\n"
            "      aliases:\n"
            "        - Apex Hoodie\n"
            "        - SKU-1042",
        )
        cfg = load_config(_write_yaml(tmp_path, yaml))
        assert len(cfg.entity_matcher.entities) == 1
        assert cfg.entity_matcher.entities[0].id == "apex_hoodie"
        assert cfg.entity_matcher.entities[0].display_name == "Apex Hoodie"
        assert cfg.entity_matcher.entities[0].aliases == ["Apex Hoodie", "SKU-1042"]
        assert cfg.entity_matcher.patterns == []

    def test_entity_display_name_defaults_to_id(self, tmp_path: Path):
        yaml = VALID_YAML.replace(
            "entity_matcher:\n"
            "  patterns:\n"
            '    - name: "person"\n'
            '      regex: "\\\\b[A-Z][a-z]+ [A-Z][a-z]+\\\\b"',
            "entity_matcher:\n"
            "  entities:\n"
            "    - id: apex_hoodie\n"
            "      aliases:\n"
            "        - Apex Hoodie",
        )
        cfg = load_config(_write_yaml(tmp_path, yaml))
        assert cfg.entity_matcher.entities[0].display_name == "apex_hoodie"

    def test_scoring_values(self, tmp_path: Path):
        cfg = load_config(_write_yaml(tmp_path, VALID_YAML))
        assert cfg.scoring.half_life_seconds == 86400.0
        assert cfg.scoring.signal_strength == 0.3
        assert cfg.scoring.score_cap == 1.0

    def test_scoring_config_contradiction_defaults(self, tmp_path: Path):
        cfg = load_config(_write_yaml(tmp_path, VALID_YAML))
        assert cfg.scoring.contradiction_window_seconds == 300.0
        assert cfg.scoring.contradiction_penalty == 0.15

    def test_scoring_config_contradiction_explicit(self, tmp_path: Path):
        yaml_content = VALID_YAML.replace(
            "scoring:\n"
            "  half_life_seconds: 86400\n"
            "  signal_strength: 0.3\n"
            "  score_cap: 1.0",
            "scoring:\n"
            "  half_life_seconds: 86400\n"
            "  signal_strength: 0.3\n"
            "  score_cap: 1.0\n"
            "  contradiction_window_seconds: 120.0\n"
            "  contradiction_penalty: 0.25",
        )
        cfg = load_config(_write_yaml(tmp_path, yaml_content))
        assert cfg.scoring.contradiction_window_seconds == 120.0
        assert cfg.scoring.contradiction_penalty == 0.25

    def test_state_store_values(self, tmp_path: Path):
        cfg = load_config(_write_yaml(tmp_path, VALID_YAML))
        assert cfg.state_store.sqlite_path == "./revok_state.db"
        assert cfg.state_store.hot_layer_max_entries == 1000


class TestLoadConfigMissingKeys:
    def test_missing_server(self, tmp_path: Path):
        yaml = VALID_YAML.replace(
            'server:\n  host: "127.0.0.1"\n  port: 8080\n  startup_timeout_seconds: 10\n',
            "",
        )
        with pytest.raises(ConfigError, match="server"):
            load_config(_write_yaml(tmp_path, yaml))

    def test_missing_upstream_url(self, tmp_path: Path):
        yaml = VALID_YAML.replace('  url: "http://localhost:8000"\n', "")
        with pytest.raises(ConfigError, match="url"):
            load_config(_write_yaml(tmp_path, yaml))

    def test_missing_entity_matcher_is_valid(self, tmp_path: Path):
        """entity_matcher section is optional; absent means header-only mode."""
        yaml_no_em = VALID_YAML.replace(
            "entity_matcher:\n"
            "  patterns:\n"
            '    - name: "person"\n'
            '      regex: "\\\\b[A-Z][a-z]+ [A-Z][a-z]+\\\\b"\n',
            "",
        )
        cfg = load_config(_write_yaml(tmp_path, yaml_no_em))
        assert cfg.entity_matcher.patterns == []
        assert cfg.entity_matcher.entities == []


class TestLoadConfigValidationRules:
    def test_port_out_of_range(self, tmp_path: Path):
        yaml = VALID_YAML.replace("  port: 8080", "  port: 99999")
        with pytest.raises(ConfigError, match="port"):
            load_config(_write_yaml(tmp_path, yaml))

    def test_port_zero(self, tmp_path: Path):
        yaml = VALID_YAML.replace("  port: 8080", "  port: 0")
        with pytest.raises(ConfigError, match="port"):
            load_config(_write_yaml(tmp_path, yaml))

    def test_half_life_zero(self, tmp_path: Path):
        yaml = VALID_YAML.replace(
            "  half_life_seconds: 86400", "  half_life_seconds: 0"
        )
        with pytest.raises(ConfigError, match="half_life"):
            load_config(_write_yaml(tmp_path, yaml))

    def test_score_cap_zero(self, tmp_path: Path):
        yaml = VALID_YAML.replace("  score_cap: 1.0", "  score_cap: 0")
        with pytest.raises(ConfigError, match="score_cap"):
            load_config(_write_yaml(tmp_path, yaml))

    def test_invalid_upstream_url(self, tmp_path: Path):
        yaml = VALID_YAML.replace(
            '  url: "http://localhost:8000"', '  url: "not-a-url"'
        )
        with pytest.raises(ConfigError, match="url"):
            load_config(_write_yaml(tmp_path, yaml))

    def test_invalid_regex_pattern(self, tmp_path: Path):
        yaml = VALID_YAML.replace(
            'regex: "\\\\b[A-Z][a-z]+ [A-Z][a-z]+\\\\b"',
            'regex: "[invalid(regex"',
        )
        with pytest.raises(ConfigError, match="regex"):
            load_config(_write_yaml(tmp_path, yaml))

    def test_empty_patterns_list_is_valid(self, tmp_path: Path):
        """Empty patterns list is accepted; entity_matcher still usable via catalog or header."""
        yaml = VALID_YAML.replace(
            '  patterns:\n    - name: "person"\n      regex: "\\\\b[A-Z][a-z]+ [A-Z][a-z]+\\\\b"',
            "  patterns: []",
        )
        cfg = load_config(_write_yaml(tmp_path, yaml))
        assert cfg.entity_matcher.patterns == []

    def test_entity_missing_id_raises(self, tmp_path: Path):
        yaml = VALID_YAML.replace(
            "entity_matcher:\n"
            "  patterns:\n"
            '    - name: "person"\n'
            '      regex: "\\\\b[A-Z][a-z]+ [A-Z][a-z]+\\\\b"',
            "entity_matcher:\n  entities:\n    - aliases:\n        - Apex Hoodie",
        )
        with pytest.raises(ConfigError, match="id"):
            load_config(_write_yaml(tmp_path, yaml))

    def test_entity_empty_aliases_raises(self, tmp_path: Path):
        yaml = VALID_YAML.replace(
            "entity_matcher:\n"
            "  patterns:\n"
            '    - name: "person"\n'
            '      regex: "\\\\b[A-Z][a-z]+ [A-Z][a-z]+\\\\b"',
            "entity_matcher:\n  entities:\n    - id: apex_hoodie\n      aliases: []",
        )
        with pytest.raises(ConfigError, match="aliases"):
            load_config(_write_yaml(tmp_path, yaml))

    def test_file_not_found(self, tmp_path: Path):
        with pytest.raises(ConfigError, match="Cannot read"):
            load_config(str(tmp_path / "nonexistent.yaml"))

    def test_invalid_yaml(self, tmp_path: Path):
        p = tmp_path / "bad.yaml"
        p.write_text("key: [unclosed", encoding="utf-8")
        with pytest.raises(ConfigError, match="YAML"):
            load_config(str(p))


# ---------------------------------------------------------------------------
# T022: Zep adapter_type config tests
# ---------------------------------------------------------------------------

ZEP_BASE_YAML = """\
server:
  host: "127.0.0.1"
  port: 8080
  startup_timeout_seconds: 10

adapter_type: zep

upstream:
  url: "http://localhost:8001"
  write_methods: ["POST"]

entity_matcher:
  patterns:
    - name: "person"
      regex: "\\\\b[A-Z][a-z]+\\\\b"

scoring:
  half_life_seconds: 86400
  signal_strength: 0.3
  score_cap: 1.0

state_store:
  sqlite_path: "./revok_zep.db"
  hot_layer_max_entries: 1000

logging:
  level: "INFO"
  format: "%(asctime)s %(levelname)s %(message)s"
"""


class TestLoadConfigZepMode:
    def test_valid_zep_config_loads(self, tmp_path: Path) -> None:
        """A complete Zep config loads without error."""
        cfg = load_config(_write_yaml(tmp_path, ZEP_BASE_YAML))
        assert cfg.adapter_type == "zep"
        assert cfg.upstream.url == "http://localhost:8001"
        assert cfg.upstream.write_methods == ["POST"]
        assert cfg.upstream.write_paths == []

    def test_zep_url_trailing_slash_stripped(self, tmp_path: Path) -> None:
        yaml = ZEP_BASE_YAML.replace(
            'url: "http://localhost:8001"',
            'url: "http://localhost:8001/"',
        )
        cfg = load_config(_write_yaml(tmp_path, yaml))
        assert not cfg.upstream.url.endswith("/")

    def test_zep_write_methods_uppercased(self, tmp_path: Path) -> None:
        yaml = ZEP_BASE_YAML.replace(
            'write_methods: ["POST"]', 'write_methods: ["post"]'
        )
        cfg = load_config(_write_yaml(tmp_path, yaml))
        assert cfg.upstream.write_methods == ["POST"]

    def test_zep_write_paths_defaults_to_empty(self, tmp_path: Path) -> None:
        """write_paths is optional in Zep mode and defaults to []."""
        cfg = load_config(_write_yaml(tmp_path, ZEP_BASE_YAML))
        assert cfg.upstream.write_paths == []


# ---------------------------------------------------------------------------
# T006: Fuzzy match threshold config tests
# ---------------------------------------------------------------------------

ENTITY_MATCHER_YAML = """\
server:
  host: "127.0.0.1"
  port: 8080
  startup_timeout_seconds: 10

upstream:
  url: "http://localhost:8000"
  write_methods: ["POST"]
  write_paths: ["/v1/memories"]

entity_matcher:
  entities:
    - id: apex_hoodie
      display_name: Apex Hoodie
      aliases:
        - Apex Hoodie
{fuzzy_line}
scoring:
  half_life_seconds: 86400
  signal_strength: 0.3
  score_cap: 1.0

state_store:
  sqlite_path: "./revok_state.db"
  hot_layer_max_entries: 1000

logging:
  level: "INFO"
  format: "%(asctime)s %(levelname)s %(message)s"
"""


class TestFuzzyMatchThresholdConfig:
    def test_entity_matcher_fuzzy_threshold_default_is_none(
        self, tmp_path: Path
    ) -> None:
        yaml = ENTITY_MATCHER_YAML.format(fuzzy_line="")
        cfg = load_config(_write_yaml(tmp_path, yaml))
        assert cfg.entity_matcher.fuzzy_match_threshold is None

    def test_entity_matcher_fuzzy_threshold_explicit_80(self, tmp_path: Path) -> None:
        yaml = ENTITY_MATCHER_YAML.format(fuzzy_line="  fuzzy_match_threshold: 80\n")
        cfg = load_config(_write_yaml(tmp_path, yaml))
        assert cfg.entity_matcher.fuzzy_match_threshold == 80.0

    def test_entity_matcher_fuzzy_threshold_zero_valid(self, tmp_path: Path) -> None:
        yaml = ENTITY_MATCHER_YAML.format(fuzzy_line="  fuzzy_match_threshold: 0\n")
        cfg = load_config(_write_yaml(tmp_path, yaml))
        assert cfg.entity_matcher.fuzzy_match_threshold == 0.0

    def test_entity_matcher_fuzzy_threshold_100_valid(self, tmp_path: Path) -> None:
        yaml = ENTITY_MATCHER_YAML.format(fuzzy_line="  fuzzy_match_threshold: 100\n")
        cfg = load_config(_write_yaml(tmp_path, yaml))
        assert cfg.entity_matcher.fuzzy_match_threshold == 100.0

    def test_entity_matcher_fuzzy_threshold_negative_raises(
        self, tmp_path: Path
    ) -> None:
        yaml = ENTITY_MATCHER_YAML.format(fuzzy_line="  fuzzy_match_threshold: -1\n")
        with pytest.raises(ConfigError, match="fuzzy_match_threshold"):
            load_config(_write_yaml(tmp_path, yaml))

    def test_entity_matcher_fuzzy_threshold_above_100_raises(
        self, tmp_path: Path
    ) -> None:
        yaml = ENTITY_MATCHER_YAML.format(fuzzy_line="  fuzzy_match_threshold: 101\n")
        with pytest.raises(ConfigError, match="fuzzy_match_threshold"):
            load_config(_write_yaml(tmp_path, yaml))

    def test_zep_write_paths_loaded_when_present(self, tmp_path: Path) -> None:
        yaml = ZEP_BASE_YAML.replace(
            'write_methods: ["POST"]',
            'write_methods: ["POST"]\n  write_paths: ["/api/v1/sessions"]',
        )
        cfg = load_config(_write_yaml(tmp_path, yaml))
        assert cfg.upstream.write_paths == ["/api/v1/sessions"]

    def test_missing_upstream_section_raises_in_zep_mode(self, tmp_path: Path) -> None:
        """adapter_type: zep without an upstream: section raises ConfigError."""
        yaml = ZEP_BASE_YAML.replace(
            'upstream:\n  url: "http://localhost:8001"\n  write_methods: ["POST"]\n',
            "",
        )
        with pytest.raises(ConfigError, match="upstream"):
            load_config(_write_yaml(tmp_path, yaml))

    def test_missing_upstream_url_raises_in_zep_mode(self, tmp_path: Path) -> None:
        """upstream: section without url raises ConfigError."""
        yaml = ZEP_BASE_YAML.replace('  url: "http://localhost:8001"\n', "")
        with pytest.raises(ConfigError, match="url"):
            load_config(_write_yaml(tmp_path, yaml))

    def test_invalid_upstream_url_raises_in_zep_mode(self, tmp_path: Path) -> None:
        """A non-HTTP upstream.url raises ConfigError."""
        yaml = ZEP_BASE_YAML.replace(
            'url: "http://localhost:8001"',
            'url: "not-a-url"',
        )
        with pytest.raises(ConfigError, match="url"):
            load_config(_write_yaml(tmp_path, yaml))

    def test_invalid_adapter_type_raises(self, tmp_path: Path) -> None:
        """An unknown adapter_type raises ConfigError."""
        yaml = VALID_YAML.replace("upstream:", "adapter_type: grpc\nupstream:")
        with pytest.raises(ConfigError, match="adapter_type"):
            load_config(_write_yaml(tmp_path, yaml))

    def test_mem0_default_when_adapter_type_absent(self, tmp_path: Path) -> None:
        """No adapter_type key → defaults to 'mem0'."""
        cfg = load_config(_write_yaml(tmp_path, VALID_YAML))
        assert cfg.adapter_type == "mem0"

    def test_mem0_write_paths_required(self, tmp_path: Path) -> None:
        """In Mem0 mode, upstream.write_paths must be non-empty."""
        yaml = VALID_YAML.replace('  write_paths: ["/v1/memories"]\n', "")
        with pytest.raises(ConfigError, match="write_paths"):
            load_config(_write_yaml(tmp_path, yaml))


class TestCausalGraphConfig:
    def test_causal_graph_defaults(self, tmp_path: Path) -> None:
        cfg = load_config(_write_yaml(tmp_path, VALID_YAML))
        assert cfg.causal_graph.enabled is False
        assert cfg.causal_graph.max_hops == 2
        assert cfg.causal_graph.min_pressure == 0.05
        assert cfg.causal_graph.attenuation == 0.8
        assert cfg.causal_graph.processing_timeout_seconds == 60.0
        assert cfg.causal_graph.resolver_timeout_seconds == 30.0
        assert cfg.causal_graph.relationships == []
        assert cfg.causal_graph.graph_backend == "networkx"
        assert cfg.causal_graph.graph_backend_db_path is None

    def test_resolver_timeout_seconds_loaded_from_yaml(self, tmp_path: Path) -> None:
        yaml = VALID_YAML + "\ncausal_graph:\n  resolver_timeout_seconds: 45.0\n"
        cfg = load_config(_write_yaml(tmp_path, yaml))
        assert cfg.causal_graph.resolver_timeout_seconds == 45.0
        # Loading resolver_timeout_seconds alone must not require the caller
        # to also set processing_timeout_seconds; the cross-field invariant is
        # only enforced once a resolver is actually configured, at build_app.
        assert cfg.causal_graph.processing_timeout_seconds == 60.0

    def test_resolver_timeout_seconds_non_positive_raises(self, tmp_path: Path) -> None:
        yaml = VALID_YAML + "\ncausal_graph:\n  resolver_timeout_seconds: 0\n"
        with pytest.raises(ConfigError, match="resolver_timeout_seconds"):
            load_config(_write_yaml(tmp_path, yaml))

    def test_processing_timeout_seconds_non_positive_raises(self, tmp_path: Path) -> None:
        yaml = VALID_YAML + "\ncausal_graph:\n  processing_timeout_seconds: -1\n"
        with pytest.raises(ConfigError, match="processing_timeout_seconds"):
            load_config(_write_yaml(tmp_path, yaml))

    def test_causal_graph_relationships_loaded(self, tmp_path: Path) -> None:
        yaml = (
            VALID_YAML
            + "\ncausal_graph:\n"
            + "  enabled: true\n"
            + "  max_hops: 3\n"
            + "  min_pressure: 0.1\n"
            + "  attenuation: 0.7\n"
            + "  processing_timeout_seconds: 1.5\n"
            + "  relationships:\n"
            + "    - source: a\n"
            + "      target: b\n"
            + "      weight: 0.6\n"
        )
        cfg = load_config(_write_yaml(tmp_path, yaml))
        assert cfg.causal_graph.enabled is True
        assert len(cfg.causal_graph.relationships) == 1
        rel = cfg.causal_graph.relationships[0]
        assert rel.source == "a"
        assert rel.target == "b"
        assert rel.weight == 0.6

    def test_causal_graph_invalid_weight_raises(self, tmp_path: Path) -> None:
        yaml = (
            VALID_YAML
            + "\ncausal_graph:\n"
            + "  relationships:\n"
            + "    - source: a\n"
            + "      target: b\n"
            + "      weight: 1.5\n"
        )
        with pytest.raises(ConfigError, match="weight"):
            load_config(_write_yaml(tmp_path, yaml))

    def test_graph_backend_falkordb_lite_loads(self, tmp_path: Path) -> None:
        yaml = VALID_YAML + "\ncausal_graph:\n  graph_backend: falkordb-lite\n"
        cfg = load_config(_write_yaml(tmp_path, yaml))
        assert cfg.causal_graph.graph_backend == "falkordb-lite"

    def test_graph_backend_db_path_loads(self, tmp_path: Path) -> None:
        yaml = (
            VALID_YAML
            + "\ncausal_graph:\n"
            + "  graph_backend: falkordb-lite\n"
            + "  graph_backend_db_path: /var/lib/revok/graph.db\n"
        )
        cfg = load_config(_write_yaml(tmp_path, yaml))
        assert cfg.causal_graph.graph_backend_db_path == "/var/lib/revok/graph.db"

    def test_graph_backend_unknown_raises(self, tmp_path: Path) -> None:
        yaml = VALID_YAML + "\ncausal_graph:\n  graph_backend: redis\n"
        with pytest.raises(ConfigError, match="graph_backend"):
            load_config(_write_yaml(tmp_path, yaml))

    def test_graph_backend_db_path_non_string_raises(self, tmp_path: Path) -> None:
        yaml = (
            VALID_YAML
            + "\ncausal_graph:\n"
            + "  graph_backend_db_path: 42\n"
        )
        with pytest.raises(ConfigError, match="graph_backend_db_path"):
            load_config(_write_yaml(tmp_path, yaml))


class TestSignalPressureConfig:
    def test_signal_pressure_defaults(self, tmp_path: Path) -> None:
        cfg = load_config(_write_yaml(tmp_path, VALID_YAML))
        assert cfg.scoring.signal_pressure is not None
        assert cfg.scoring.signal_pressure.default_severity == "medium"

    def test_signal_pressure_loaded(self, tmp_path: Path) -> None:
        yaml = VALID_YAML.replace(
            "scoring:\n"
            "  half_life_seconds: 86400\n"
            "  signal_strength: 0.3\n"
            "  score_cap: 1.0",
            "scoring:\n"
            "  half_life_seconds: 86400\n"
            "  signal_strength: 0.3\n"
            "  score_cap: 1.0\n"
            "  signal_pressure:\n"
            "    severity_weights:\n"
            "      low: 0.2\n"
            "      medium: 0.4\n"
            "    default_severity: medium",
        )
        cfg = load_config(_write_yaml(tmp_path, yaml))
        assert cfg.scoring.signal_pressure is not None
        assert cfg.scoring.signal_pressure.severity_weights["low"] == 0.2
        assert cfg.scoring.signal_pressure.default_severity == "medium"

    def test_signal_pressure_out_of_range_weight_raises(self, tmp_path: Path) -> None:
        yaml = VALID_YAML.replace(
            "scoring:\n"
            "  half_life_seconds: 86400\n"
            "  signal_strength: 0.3\n"
            "  score_cap: 1.0",
            "scoring:\n"
            "  half_life_seconds: 86400\n"
            "  signal_strength: 0.3\n"
            "  score_cap: 1.0\n"
            "  signal_pressure:\n"
            "    severity_weights:\n"
            "      medium: 2.0\n"
            "    default_severity: medium",
        )
        with pytest.raises(ConfigError, match="severity_weights"):
            load_config(_write_yaml(tmp_path, yaml))


# ---------------------------------------------------------------------------
# T003: InspectorConfig tests
# ---------------------------------------------------------------------------


class TestInspectorConfig:
    def test_inspector_defaults_when_absent(self, tmp_path: Path) -> None:
        cfg = load_config(_write_yaml(tmp_path, VALID_YAML))
        assert cfg.inspector.enabled is True
        assert cfg.inspector.signal_history_enabled is True
        assert cfg.inspector.signal_history_max_rows == 10_000
        assert cfg.inspector.max_paths == 100

    def test_inspector_explicit_values_loaded(self, tmp_path: Path) -> None:
        yaml = (
            VALID_YAML
            + "\ninspector:\n"
            + "  enabled: false\n"
            + "  signal_history_enabled: false\n"
            + "  signal_history_max_rows: 500\n"
            + "  max_paths: 50\n"
        )
        cfg = load_config(_write_yaml(tmp_path, yaml))
        assert cfg.inspector.enabled is False
        assert cfg.inspector.signal_history_enabled is False
        assert cfg.inspector.signal_history_max_rows == 500
        assert cfg.inspector.max_paths == 50

    def test_inspector_signal_history_max_rows_zero_raises(self, tmp_path: Path) -> None:
        yaml = (
            VALID_YAML
            + "\ninspector:\n"
            + "  signal_history_max_rows: 0\n"
        )
        with pytest.raises(ConfigError, match="signal_history_max_rows"):
            load_config(_write_yaml(tmp_path, yaml))

    def test_inspector_max_paths_zero_raises(self, tmp_path: Path) -> None:
        yaml = (
            VALID_YAML
            + "\ninspector:\n"
            + "  max_paths: 0\n"
        )
        with pytest.raises(ConfigError, match="max_paths"):
            load_config(_write_yaml(tmp_path, yaml))

    def test_inspector_not_a_mapping_raises(self, tmp_path: Path) -> None:
        yaml = VALID_YAML + "\ninspector: not_a_mapping\n"
        with pytest.raises(ConfigError, match="inspector"):
            load_config(_write_yaml(tmp_path, yaml))


class TestSignalSourcesConfig:
    def test_defaults_enable_http_only(self, tmp_path: Path) -> None:
        cfg = load_config(_write_yaml(tmp_path, VALID_YAML))
        assert cfg.sources.http.enabled is True
        assert cfg.sources.redis_streams.enabled is False
        assert cfg.ingestion.dedupe_max_rows == 100_000
        assert cfg.ingestion.max_concurrent_signals == 16

    def test_redis_streams_values_are_loaded(self, tmp_path: Path) -> None:
        yaml = (
            VALID_YAML
            + "\nsources:\n"
            + "  redis_streams:\n"
            + "    enabled: true\n"
            + '    url: "redis://localhost:6379"\n'
            + "    max_delivery_attempts: 5\n"
        )
        cfg = load_config(_write_yaml(tmp_path, yaml))
        assert cfg.sources.redis_streams.enabled is True
        assert cfg.sources.redis_streams.url == "redis://localhost:6379"
        assert cfg.sources.redis_streams.max_delivery_attempts == 5
        assert cfg.sources.redis_streams.stream == "revok:signals"

    def test_enabled_redis_streams_requires_url(self, tmp_path: Path) -> None:
        yaml = VALID_YAML + "\nsources:\n  redis_streams:\n    enabled: true\n"
        with pytest.raises(ConfigError, match="url is required"):
            load_config(_write_yaml(tmp_path, yaml))

    def test_non_positive_attempt_bound_raises(self, tmp_path: Path) -> None:
        yaml = (
            VALID_YAML
            + "\nsources:\n"
            + "  redis_streams:\n"
            + "    max_delivery_attempts: 0\n"
        )
        with pytest.raises(ConfigError, match="max_delivery_attempts"):
            load_config(_write_yaml(tmp_path, yaml))

    def test_sources_not_a_mapping_raises(self, tmp_path: Path) -> None:
        yaml = VALID_YAML + "\nsources: not_a_mapping\n"
        with pytest.raises(ConfigError, match="sources"):
            load_config(_write_yaml(tmp_path, yaml))

    def test_non_positive_dedupe_rows_raises(self, tmp_path: Path) -> None:
        yaml = VALID_YAML + "\ningestion:\n  dedupe_max_rows: 0\n"
        with pytest.raises(ConfigError, match="dedupe_max_rows"):
            load_config(_write_yaml(tmp_path, yaml))
