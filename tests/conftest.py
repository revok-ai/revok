# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Revok Contributors
#
# Shared pytest fixtures for the Revok test suite.

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# On Windows, ProactorEventLoop can hang during teardown when there are pending
# async I/O operations (e.g. from aiosqlite).  SelectorEventLoop closes cleanly.
if sys.platform == "win32":
    policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if policy is not None:
        asyncio.set_event_loop_policy(policy())

import pytest

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


# On Windows, Python's shutdown sequence blocks joining executor threads
# (via concurrent.futures.thread._python_exit atexit handler) which keeps
# the process alive in Git Bash / mintty.  Calling os._exit() in
# pytest_unconfigure fires BEFORE sys.exit() is called, so Python's own
# shutdown (thread-join, atexit handlers) never runs.
_pytest_exit_code: int = 0


def pytest_sessionfinish(
    session: pytest.Session,
    exitstatus: int | pytest.ExitCode,
) -> None:
    """Capture pytest exit status for use in pytest_unconfigure."""
    global _pytest_exit_code
    _pytest_exit_code = int(exitstatus)


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config: pytest.Config) -> None:
    """Force immediate process exit on Windows/Git Bash after all cleanup.

    trylast=True ensures all other plugins' pytest_unconfigure hooks (e.g.
    pytest-asyncio event-loop teardown) run first.  os._exit() then
    terminates the process hard, bypassing Python's thread-join phase.
    """
    if sys.platform == "win32" or os.environ.get("CI"):
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:  # noqa: BLE001
            pass
        os._exit(_pytest_exit_code)
