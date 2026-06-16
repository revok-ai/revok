"""Persists demo state to ``demo_state.json``.

All mutations go through this module; ``server.py`` never serialises
state directly.  This keeps state shape in one place and makes it easy
to add fields without touching the server.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

_STATE_FILE: Path = Path(__file__).parent / "demo_state.json"
_MAX_LOG_ENTRIES: int = 50

#: Default / empty state.  Server resets to this on ``POST /actions/reset``.
_DEFAULTS: dict[str, Any] = {
    "db_price": None,
    "db_updated_at": None,
    "memory_content": None,
    "memory_loading": False,
    "confidence_score": None,
    "confidence_status": "unknown",
    "signal_count": 0,
    "last_signal_at": None,
    "answer_without_revok": None,
    "answer_with_revok": None,
    "event_log": [],
    "session_stats": {
        "questions": 0,
        "drift_caught": 0,
        "wrong_answers": 0,
        "cost_saved": 0.0,
    },
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load() -> dict[str, Any]:
    """Load state from disk, falling back to defaults if missing or corrupt."""
    if _STATE_FILE.exists():
        try:
            return json.loads(_STATE_FILE.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            _log.warning("Could not read state file (%s) — using defaults", exc)
    return dict(_DEFAULTS)


def save(state: dict[str, Any]) -> None:
    """Persist *state* to disk.

    Args:
        state: Mutable state dict to serialise.
    """
    _STATE_FILE.write_text(
        json.dumps(state, indent=2, default=str),
        encoding="utf-8",
    )


def add_event(state: dict[str, Any], message: str, kind: str = "info") -> None:
    """Append a timestamped event entry to ``state["event_log"]``.

    The log is capped at ``_MAX_LOG_ENTRIES``; oldest entries are discarded.

    Args:
        state:   Mutable state dict (modified in place).
        message: Human-readable description of the event.
        kind:    Category string — one of ``"memory"``, ``"db"``,
                 ``"signal"``, ``"answer"``, ``"info"``.
    """
    state.setdefault("event_log", []).append(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "msg": message,
            "kind": kind,
        }
    )
    state["event_log"] = state["event_log"][-_MAX_LOG_ENTRIES:]


def reset_state() -> dict[str, Any]:
    """Return and persist a clean default state dict.

    Returns:
        Fresh copy of ``_DEFAULTS``.
    """
    state = dict(_DEFAULTS)
    save(state)
    return state
