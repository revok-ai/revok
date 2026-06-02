"""Async scenario runner for the CrewAI pricing stale-memory demo."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
from dotenv import load_dotenv

from agent import PricingSalesAgent

REPO_DIR = Path(__file__).parent
STATE_PATH = REPO_DIR / "demo_state.json"
MEM0_URL = "http://localhost:7770"
REVOK_URL = "http://localhost:7771"
HALF_LIFE_SECONDS = 12


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_state() -> dict[str, Any]:
    return {
        "phase": "initializing",
        "started_at": now_iso(),
        "updated_at": now_iso(),
        "done": False,
        "events": [],
        "without_revok": {
            "agent": "WITHOUT REVOK",
            "answer": "",
            "memory_quote": "",
            "status": "no confidence tracking",
            "signal_count": 0,
            "confidence_score": None,
            "confidence_status": "unknown",
            "last_updated": now_iso(),
        },
        "with_revok": {
            "agent": "WITH REVOK",
            "answer": "",
            "memory_quote": "",
            "status": "waiting",
            "signal_count": 0,
            "confidence_score": 0.0,
            "confidence_status": "unknown",
            "signal_fired": False,
            "score_history": [],
            "last_updated": now_iso(),
        },
    }


def save_state(state: dict[str, Any]) -> None:
    state["updated_at"] = now_iso()
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def add_event(state: dict[str, Any], message: str) -> None:
    state["events"].append({"at": now_iso(), "message": message})
    save_state(state)


async def record_with_revok_status(
    state: dict[str, Any],
    session: aiohttp.ClientSession,
    agent: PricingSalesAgent,
) -> None:
    entity = await agent.get_revok_entity(session)
    if not entity:
        return
    score = float(entity.get("score", 0.0))
    signal_count = int(entity.get("signal_count", 0))

    panel = state["with_revok"]
    panel["confidence_score"] = score
    panel["signal_count"] = signal_count
    panel["confidence_status"] = (
        "fresh" if score > 0.7 else "degraded" if score >= 0.3 else "stale"
    )
    panel["score_history"].append({"at": now_iso(), "score": score})
    panel["score_history"] = panel["score_history"][-90:]
    panel["last_updated"] = now_iso()
    save_state(state)


async def run_demo() -> None:
    load_dotenv(REPO_DIR / ".env")

    state = init_state()
    save_state(state)

    without_revok = PricingSalesAgent(
        name="WITHOUT REVOK",
        user_id="without-revok-agent",
        memory_base_url=MEM0_URL,
        revok_base_url=None,
        use_revok=False,
    )
    with_revok = PricingSalesAgent(
        name="WITH REVOK",
        user_id="with-revok-agent",
        memory_base_url=REVOK_URL,
        revok_base_url=REVOK_URL,
        use_revok=True,
    )

    async with aiohttp.ClientSession() as session:
        state["phase"] = "step_1_seed_memory"
        add_event(state, "Step 1: both agents stored '$500/month' memory.")

        memory_text = "Redis Enterprise costs $500/month for a 10 GB active-memory cluster."
        await without_revok.add_memory(session, memory_text)
        await with_revok.add_memory(session, memory_text)

        without_answer = await without_revok.answer_pricing(session)
        with_answer = await with_revok.answer_pricing(session)

        state["without_revok"].update(
            {
                "answer": without_answer.answer,
                "memory_quote": without_answer.memory_quote,
                "last_updated": now_iso(),
            }
        )
        state["with_revok"].update(
            {
                "answer": with_answer.answer,
                "memory_quote": with_answer.memory_quote,
                "confidence_score": with_answer.confidence_score,
                "confidence_status": with_answer.confidence_status,
                "signal_count": with_answer.signal_count,
                "status": "tracking confidence",
                "last_updated": now_iso(),
            }
        )
        save_state(state)

        state["phase"] = "step_2_wait_for_decay"
        add_event(state, "Step 2: waiting for confidence score decay.")

        for second in range(1, HALF_LIFE_SECONDS + 1):
            await asyncio.sleep(1)
            await record_with_revok_status(state, session, with_revok)
            state["phase"] = f"step_2_wait_for_decay ({second}/{HALF_LIFE_SECONDS}s)"
            save_state(state)

        without_after_decay = await without_revok.answer_pricing(session)
        with_after_decay = await with_revok.answer_pricing(session)
        state["without_revok"]["answer"] = without_after_decay.answer
        state["without_revok"]["memory_quote"] = without_after_decay.memory_quote
        state["without_revok"]["last_updated"] = now_iso()

        state["with_revok"].update(
            {
                "answer": with_after_decay.answer,
                "memory_quote": with_after_decay.memory_quote,
                "confidence_score": with_after_decay.confidence_score,
                "confidence_status": with_after_decay.confidence_status,
                "signal_count": with_after_decay.signal_count,
                "status": "degraded memory detected",
                "last_updated": now_iso(),
            }
        )
        save_state(state)

        state["phase"] = "step_3_fire_signal"
        add_event(state, "Step 3: pricing-change signal sent through Revok.")

        await with_revok.add_memory(
            session,
            "Signal: Redis Enterprise changed pricing. The old $500/month figure may be outdated.",
            role="system",
        )
        state["with_revok"]["signal_fired"] = True

        await asyncio.sleep(1)
        await record_with_revok_status(state, session, with_revok)

        without_after_signal = await without_revok.answer_pricing(session)
        with_after_signal = await with_revok.answer_pricing(session)

        state["without_revok"]["answer"] = without_after_signal.answer
        state["without_revok"]["memory_quote"] = without_after_signal.memory_quote
        state["without_revok"]["last_updated"] = now_iso()

        state["with_revok"].update(
            {
                "answer": with_after_signal.answer,
                "memory_quote": with_after_signal.memory_quote,
                "confidence_score": with_after_signal.confidence_score,
                "confidence_status": with_after_signal.confidence_status,
                "signal_count": with_after_signal.signal_count,
                "status": "signal received; re-verification required",
                "last_updated": now_iso(),
            }
        )

        state["phase"] = "complete"
        state["done"] = True
        add_event(state, "Demo complete: WITHOUT REVOK still quotes $500; WITH REVOK requests re-verification.")


if __name__ == "__main__":
    asyncio.run(run_demo())
