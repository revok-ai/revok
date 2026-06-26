# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Revok Contributors

"""Contract tests for FalkorDBGraphBackend.

Validates that FalkorDBGraphBackend satisfies the full GraphReader + propagation
contract defined in GraphReaderContract.

Test isolation strategy: one shared FalkorDB process per class; each test gets
a unique graph name (UUID-prefixed) and the graph is deleted in teardown.
The suite is skipped automatically when falkordblite is not installed.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from revok.falkordb_backend import FalkorDBGraphBackend
from tests.contract.test_graph_reader_contract import GraphReaderContract

try:
    from redislite.falkordb_client import FalkorDB as _FalkorDB

    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False

pytestmark = pytest.mark.skipif(not _AVAILABLE, reason="falkordblite not installed")


class TestFalkorDBGraphBackendContract(GraphReaderContract):
    """Run the full GraphReaderContract suite against FalkorDBGraphBackend."""

    _shared_db: _FalkorDB | None = None  # type: ignore[type-arg]

    @classmethod
    def setup_class(cls) -> None:
        cls._shared_db = _FalkorDB()

    @classmethod
    def teardown_class(cls) -> None:
        if cls._shared_db is not None:
            cls._shared_db.close()
            cls._shared_db = None

    def setup_method(self) -> None:
        self._graph_name = f"t_{uuid4().hex}"

    def teardown_method(self) -> None:
        try:
            assert self._shared_db is not None
            self._shared_db.select_graph(self._graph_name).delete()
        except Exception:
            pass

    def make_backend(self) -> FalkorDBGraphBackend:
        return FalkorDBGraphBackend(db=self._shared_db, graph_name=self._graph_name)
