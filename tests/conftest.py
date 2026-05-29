# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Revok Contributors
#
# Shared pytest fixtures for the Revok test suite.

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import AsyncIterator

import pytest
import pytest_asyncio

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


@pytest.fixture
def minimal_config(tmp_path: Path) -> Config:
    """Return a minimal valid Config object wired to a temp SQLite DB.

    Args:
        tmp_path: pytest-provided temporary directory.

    Returns:
        Config: A fully populated Config suitable for unit tests.
    """
    return Config(
        server=ServerConfig(
            host="127.0.0.1",
            port=8080,
            startup_timeout_seconds=5.0,
        ),
        upstream=UpstreamConfig(
            mem0_url="http://localhost:8000",
            write_methods=["POST"],
            write_paths=["/v1/memories"],
        ),
        entity_matcher=EntityMatcherConfig(
            patterns=[
                PatternConfig(
                    name="person",
                    regex=r"\b[A-Z][a-z]+ [A-Z][a-z]+\b",
                )
            ]
        ),
        scoring=ScoringConfig(
            half_life_seconds=86400.0,
            signal_strength=0.3,
            score_cap=1.0,
        ),
        state_store=StateStoreConfig(
            sqlite_path=str(tmp_path / "test_revok.db"),
            hot_layer_max_entries=10,
        ),
        logging=LoggingConfig(
            level="WARNING",
            format="%(levelname)s %(message)s",
        ),
    )


@pytest.fixture
def tmp_db_path(tmp_path: Path) -> str:
    """Return a path to a non-existent SQLite file in a temp directory.

    Args:
        tmp_path: pytest-provided temporary directory.

    Returns:
        str: Path string for a new SQLite database.
    """
    return str(tmp_path / "revok_test.db")
