#!/usr/bin/env python3
"""Revok + Mem0 end-to-end demo: Redis Enterprise pricing change.

Scenario
--------
1. An AI agent stores a memory: "Redis Enterprise pricing is $500/month".
   Revok intercepts the write, extracts the "Redis Enterprise" entity,
   and injects a confidence score of 0.80 into the payload forwarded to Mem0.

2. Ten seconds pass (one half-life in revok.yaml).  The entity score decays
   to ~0.40.  An agent reading the stored memory can query Revok's entity
   API and see the confidence has dropped — the fact may be stale.

3. A pricing-change event fires a second signal.  Revok registers the new
   signal, bumping signal_count to 2.  The agent now knows the entity has
   been referenced in a conflicting context and must re-verify the
   $500/month figure before acting on it.

Usage
-----
    # Start services first (see README.md)
    python demo.py
"""

from __future__ import annotations

import json
import sys
import time
import urllib.parse

try:
    import requests
except ModuleNotFoundError:
    print("Install requests first:  pip install requests", file=sys.stderr)
    sys.exit(1)

# ── Constants ─────────────────────────────────────────────────────────────────

PROXY = "http://localhost:7771"
ENTITY_KEY = "redis enterprise"  # normalised: lower-cased match of the regex
HALF_LIFE_S = 10  # must match revok.yaml scoring.half_life_seconds

# ── Helpers ───────────────────────────────────────────────────────────────────


def section(title: str) -> None:
    bar = "━" * 62
    print(f"\n{bar}\n  {title}\n{bar}")


def post_memory(content: str, role: str = "user") -> dict:
    """POST a memory through the Revok proxy to Mem0."""
    r = requests.post(
        f"{PROXY}/memories",
        json={
            "messages": [{"role": role, "content": content}],
            "user_id": "demo-agent",
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def get_entity(key: str = ENTITY_KEY) -> dict:
    """Read entity state from Revok's entity API (applies live decay)."""
    encoded = urllib.parse.quote(key, safe="")
    r = requests.get(f"{PROXY}/v1/entities/{encoded}", timeout=10)
    r.raise_for_status()
    return r.json()


def wait_with_countdown(seconds: int) -> None:
    for remaining in range(seconds, 0, -1):
        print(f"  ⏳  {remaining:2d}s remaining …", end="\r", flush=True)
        time.sleep(1)
    print(" " * 40, end="\r")  # clear countdown line


# ── Demo ──────────────────────────────────────────────────────────────────────


def main() -> None:
    # ------------------------------------------------------------------
    # Step 1: store the original pricing memory
    # ------------------------------------------------------------------
    section("Step 1 — Store memory: Redis Enterprise is $500 / month")

    resp = post_memory(
        "Redis Enterprise pricing is $500 per month for a 10 GB active-memory cluster."
    )
    print(f"Mem0 response:\n{json.dumps(resp, indent=2)}")

    entity = get_entity()
    initial_score: float = entity["score"]

    print(
        f"\nRevok entity  : {entity['entity_key']!r}\n"
        f"Score at t=0  : {initial_score:.4f}  (signal_strength = 0.80)\n"
        f"Signal count  : {entity['signal_count']}"
    )

    # ------------------------------------------------------------------
    # Step 2: let the score decay for one half-life
    # ------------------------------------------------------------------
    section(
        f"Step 2 — Waiting {HALF_LIFE_S}s (one half-life) to simulate time passing …"
    )

    wait_with_countdown(HALF_LIFE_S)

    entity = get_entity()
    decayed_score: float = entity["score"]
    drop_pct = (1.0 - decayed_score / initial_score) * 100.0

    print(
        f"Score at t={HALF_LIFE_S}s : {decayed_score:.4f}\n"
        f"Decay         : {initial_score:.4f} → {decayed_score:.4f}  "
        f"({drop_pct:.0f}% drop)\n"
        f"\n⚠  The '$500/month' memory is losing confidence — it may be stale."
    )

    # ------------------------------------------------------------------
    # Step 3: fire the pricing-change invalidation signal
    # ------------------------------------------------------------------
    section("Step 3 — Send pricing-change invalidation signal")

    resp = post_memory(
        "BREAKING: Redis Enterprise has changed their pricing model. "
        "The $500/month rate for 10 GB clusters is no longer valid. "
        "New pricing tiers have not yet been published.",
        role="system",
    )
    print(f"Mem0 response:\n{json.dumps(resp, indent=2)}")

    entity = get_entity()
    final_score: float = entity["score"]
    signal_count: int = entity["signal_count"]

    print(
        f"\nRevok entity  : {entity['entity_key']!r}\n"
        f"Score         : {final_score:.4f}  "
        f"(decayed baseline + 0.80 signal boost)\n"
        f"Signal count  : {signal_count}"
    )

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    section("Summary")
    print(
        f"  t=  0s  score={initial_score:.4f}  memory written with high confidence\n"
        f"  t={HALF_LIFE_S:3d}s  score={decayed_score:.4f}  "
        f"confidence decayed — memory is getting stale\n"
        f"  t={HALF_LIFE_S:3d}s+ score={final_score:.4f}  "
        f"pricing-change signal fired  (signal_count={signal_count})\n"
    )
    print(
        "Any agent reading the original memory should inspect\n"
        "x_revok.entities for 'redis enterprise' and see\n"
        f"signal_count={signal_count} — the $500/month figure needs re-verification."
    )


if __name__ == "__main__":
    main()
