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
  mem0_url: "http://localhost:8000"
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
            'mem0_url: "http://localhost:8000"',
            'mem0_url: "http://localhost:8000/"',
        )
        cfg = load_config(_write_yaml(tmp_path, yaml_content))
        assert not cfg.upstream.mem0_url.endswith("/")

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

    def test_missing_upstream_mem0_url(self, tmp_path: Path):
        yaml = VALID_YAML.replace('  mem0_url: "http://localhost:8000"\n', "")
        with pytest.raises(ConfigError, match="mem0_url"):
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

    def test_invalid_mem0_url(self, tmp_path: Path):
        yaml = VALID_YAML.replace(
            '  mem0_url: "http://localhost:8000"', '  mem0_url: "not-a-url"'
        )
        with pytest.raises(ConfigError, match="mem0_url"):
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
