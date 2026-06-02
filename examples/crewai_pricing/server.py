"""FastAPI dashboard server for the CrewAI stale-memory pricing demo."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.parse
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

import database as db_module
import demo_state as state_module
from agent import PricingSalesAgent, _score_to_status

APP_DIR = Path(__file__).parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Runtime globals (populated in lifespan)
# ---------------------------------------------------------------------------

_without_revok: PricingSalesAgent | None = None
_with_revok: PricingSalesAgent | None = None
_demo_state: dict[str, Any] = {}
_entity_key: str = ""


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    """Initialise DB, agents and state on startup."""
    global _without_revok, _with_revok, _demo_state, _entity_key

    load_dotenv(APP_DIR / ".env")

    mem0_url = os.getenv("MEM0_URL", "http://localhost:7770")
    revok_url = os.getenv("REVOK_URL", "http://localhost:7771")
    _entity_key = os.getenv("REVOK_ENTITY", "redis enterprise")

    _without_revok = PricingSalesAgent(
        name="WITHOUT REVOK",
        user_id="demo-without-revok",
        memory_base_url=mem0_url,
        revok_base_url=None,
        use_revok=False,
    )
    _with_revok = PricingSalesAgent(
        name="WITH REVOK",
        user_id="demo-with-revok",
        memory_base_url=revok_url,
        revok_base_url=revok_url,
        use_revok=True,
    )

    await db_module.init_db()
    _demo_state = state_module.load()

    # Sync live DB price into state
    db_row = await db_module.get_price(db_module.PRODUCT_NAME)
    if db_row:
        _demo_state[state_module._DEFAULTS.keys() and "db_price"] = db_row[db_module.COL_PRICE]
        _demo_state["db_price"] = db_row[db_module.COL_PRICE]
        _demo_state["db_updated_at"] = db_row[db_module.COL_UPDATED]

    _log.info("Demo server ready.  entity_key=%r  product=%r", _entity_key, db_module.PRODUCT_NAME)
    yield


app = FastAPI(title="Revok Pricing Demo", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _fetch_revok_entity() -> dict[str, Any] | None:
    """Query Revok entity API; return ``None`` on error or 404."""
    revok_url = os.getenv("REVOK_URL", "http://localhost:7771")
    encoded = urllib.parse.quote(_entity_key, safe="")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{revok_url}/v1/entities/{encoded}",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 404:
                    return None
                resp.raise_for_status()
                return await resp.json()
    except Exception as exc:
        _log.debug("Revok entity query failed: %s", exc)
        return None


async def _revok_reachable() -> bool:
    """Return ``True`` if the Revok proxy responds on its entities endpoint."""
    revok_url = os.getenv("REVOK_URL", "http://localhost:7771")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{revok_url}/v1/entities",
                timeout=aiohttp.ClientTimeout(total=3),
            ) as resp:
                return resp.status < 500
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/")
async def index() -> HTMLResponse:
    """Serve the dashboard HTML."""
    content = (APP_DIR / "dashboard.html").read_text(encoding="utf-8")
    return HTMLResponse(content=content, media_type="text/html; charset=utf-8")


@app.get("/calculator")
async def calculator() -> HTMLResponse:
    """Serve the standalone ROI calculator page."""
    content = (APP_DIR / "calculator.html").read_text(encoding="utf-8")
    return HTMLResponse(content=content, media_type="text/html; charset=utf-8")


@app.get("/state")
async def get_state() -> JSONResponse:
    """Return full live demo state: DB price + Revok confidence + event log."""
    db_row = await db_module.get_price(db_module.PRODUCT_NAME)
    entity = await _fetch_revok_entity()
    reachable = await _revok_reachable()

    score: float | None = float(entity["score"]) if entity else None
    sig_count: int = int(entity["signal_count"]) if entity else _demo_state.get("signal_count", 0)
    conf_status: str = _score_to_status(score)

    if db_row:
        _demo_state["db_price"] = db_row[db_module.COL_PRICE]
        _demo_state["db_updated_at"] = db_row[db_module.COL_UPDATED]
    _demo_state["confidence_score"] = score
    _demo_state["confidence_status"] = conf_status
    _demo_state["signal_count"] = sig_count

    return JSONResponse(
        {
            **_demo_state,
            "entity_key": _entity_key,
            "product_name": db_module.PRODUCT_NAME,
            "revok_reachable": reachable,
        }
    )


# ---------------------------------------------------------------------------
# Action request models
# ---------------------------------------------------------------------------


class ChangePriceRequest(BaseModel):
    new_price: float


# ---------------------------------------------------------------------------
# Action endpoints
# ---------------------------------------------------------------------------


@app.post("/actions/load-memory")
async def load_memory() -> JSONResponse:
    """Agent reads current price from SQLite and stores it as long-term memory in Mem0."""
    if _without_revok is None or _with_revok is None:
        return JSONResponse({"error": "agents not ready"}, status_code=503)

    db_row = await db_module.get_price(db_module.PRODUCT_NAME)
    if not db_row:
        return JSONResponse(
            {"error": f"Product {db_module.PRODUCT_NAME!r} not found in database"},
            status_code=404,
        )

    price = float(db_row[db_module.COL_PRICE])
    product = str(db_row[db_module.COL_NAME])

    async with aiohttp.ClientSession() as session:
        await asyncio.gather(
            _without_revok.store_pricing_belief(session, product, price),
            _with_revok.store_pricing_belief(session, product, price),
        )

    content = (
        f"Customer budget approved {product} at ${price:.0f}/month. "
        "Verified pricing from database."
    )
    _demo_state["memory_content"] = content
    state_module.add_event(
        _demo_state,
        f"Memory loaded: {product} at ${price:.0f}/month",
        kind="memory",
    )
    state_module.save(_demo_state)

    return JSONResponse({"stored": True, "product": product, "price": price, "memory_content": content})


@app.post("/actions/change-price")
async def change_price(body: ChangePriceRequest) -> JSONResponse:
    """Update price in SQLite (simulates a SQL change in production)."""
    updated = await db_module.update_price(db_module.PRODUCT_NAME, body.new_price)
    _demo_state["db_price"] = updated[db_module.COL_PRICE]
    _demo_state["db_updated_at"] = updated[db_module.COL_UPDATED]
    state_module.add_event(
        _demo_state,
        f"Price changed to ${body.new_price:.0f}/month in database",
        kind="db",
    )
    state_module.save(_demo_state)
    return JSONResponse(
        {
            "updated": True,
            "product": db_module.PRODUCT_NAME,
            "new_price": body.new_price,
            "updated_at": updated[db_module.COL_UPDATED],
        }
    )


@app.post("/actions/trigger-signal")
async def trigger_signal() -> JSONResponse:
    """Simulate an Azure Function CDC event: the Function detects a DB price change
    and writes the new price into the WITH-Revok agent's memory via the Revok proxy.

    This is the correct CDC model:
      DB change → Azure Function → POST to Revok proxy (agent user_id) → Mem0 updated

    The WITHOUT-Revok agent's memory is NOT updated (it has no CDC integration).
    """
    if _with_revok is None:
        return JSONResponse({"error": "agents not ready"}, status_code=503)

    product = db_module.PRODUCT_NAME
    db_row = await db_module.get_price(product)
    if not db_row:
        return JSONResponse({"error": f"Product {product!r} not found"}, status_code=404)

    new_price = float(db_row[db_module.COL_PRICE])
    price_str = f"${new_price:.0f}/month"

    entity: dict[str, Any] | None = None
    try:
        mem0_url = os.getenv("MEM0_URL", "http://localhost:7770")
        async with aiohttp.ClientSession() as session:
            # Delete WITH-Revok memories directly via Mem0 (not through Revok proxy)
            # to avoid proxy latency during cleanup.  Qdrant deletions are eventually
            # consistent, so we sleep 3 s to let the index settle before writing.
            memories = await _with_revok._list_memories(session)
            for item in memories:
                mem_id = item.get("id")
                if not mem_id:
                    continue
                try:
                    async with session.delete(
                        f"{mem0_url}/memories/{mem_id}",
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as dresp:
                        _log.debug("Deleted memory %s → %s", mem_id, dresp.status)
                except Exception as de:
                    _log.warning("Could not delete memory %s: %s", mem_id, de)

            # Wait for Qdrant to settle so Mem0's LLM sees an empty index
            # and doesn't merge the new price with the old one.
            await asyncio.sleep(3)

            # CDC write: push new price into WITH-Revok agent's memory through Revok
            # proxy — this is what triggers the entity score update in Revok.
            await _with_revok.store_pricing_belief(session, product, new_price)

            # Brief pause so Mem0 commits the new memory to Qdrant before any
            # subsequent GET /memories call reads it back.
            await asyncio.sleep(1)

            # Query updated entity score
            encoded = urllib.parse.quote(_entity_key, safe="")
            revok_url = os.getenv("REVOK_URL", "http://localhost:7771")
            async with session.get(
                f"{revok_url}/v1/entities/{encoded}",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as eresp:
                if eresp.status == 200:
                    entity = await eresp.json()
    except Exception as exc:
        _log.error("trigger-signal failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=502)

    now_iso = datetime.now(timezone.utc).isoformat()
    score: float | None = float(entity["score"]) if entity else None
    sig_count: int = int(entity["signal_count"]) if entity else 0
    conf_status = _score_to_status(score)

    _demo_state["last_signal_at"] = now_iso
    _demo_state["confidence_score"] = score
    _demo_state["confidence_status"] = conf_status
    _demo_state["signal_count"] = sig_count

    # Keep memory_content in sync so the alignment badge in the UI reflects
    # the WITH-Revok agent's latest belief (written by the CDC trigger).
    _demo_state["memory_content"] = (
        f"Customer budget approved {product} at ${new_price:.0f}/month. "
        "Verified pricing from database."
    )

    score_str = f"{score:.2f}" if score is not None else "N/A"
    state_module.add_event(
        _demo_state,
        f"External write — WITH-Revok memory updated to {price_str}, confidence now {conf_status} (score={score_str})",
        kind="signal",
    )
    state_module.save(_demo_state)

    return JSONResponse(
        {
            "fired": True,
            "entity_key": _entity_key,
            "confidence_score": score,
            "confidence_status": conf_status,
            "signal_count": sig_count,
            "last_signal_at": now_iso,
        }
    )


@app.post("/actions/ask-agent")
async def ask_agent() -> JSONResponse:
    """Run the sales agent in both modes; store answers in state."""
    if _without_revok is None or _with_revok is None:
        return JSONResponse({"error": "agents not ready"}, status_code=503)

    product = db_module.PRODUCT_NAME

    async with aiohttp.ClientSession() as session:
        # Run sequentially so intermediate events appear in the live log.
        state_module.add_event(_demo_state, "Asking agent WITHOUT Revok…", kind="info")
        state_module.save(_demo_state)
        ans_without = await _without_revok.answer_budget_question(session, product, None)

        without_mode = "re-verified" if ans_without.re_verified else "from memory"
        state_module.add_event(
            _demo_state,
            f"Without Revok answered ({without_mode}): {ans_without.answer[:80]}",
            kind="answer",
        )
        _demo_state["answer_without_revok"] = {
            "answer": ans_without.answer,
            "memory_quote": ans_without.memory_quote,
            "re_verified": ans_without.re_verified,
            "live_price": ans_without.live_price,
        }
        state_module.save(_demo_state)

        state_module.add_event(_demo_state, "Asking agent WITH Revok…", kind="info")
        state_module.save(_demo_state)
        ans_with = await _with_revok.answer_budget_question(session, product, _entity_key)

        with_mode = ans_with.confidence_status + (" → re-verified" if ans_with.re_verified else "")
        state_module.add_event(
            _demo_state,
            f"With Revok answered ({with_mode}): {ans_with.answer[:80]}",
            kind="answer",
        )
        _demo_state["answer_with_revok"] = {
            "answer": ans_with.answer,
            "memory_quote": ans_with.memory_quote,
            "confidence_score": ans_with.confidence_score,
            "confidence_status": ans_with.confidence_status,
            "signal_count": ans_with.signal_count,
            "re_verified": ans_with.re_verified,
            "live_price": ans_with.live_price,
        }
        state_module.save(_demo_state)

    return JSONResponse(
        {
            "without_revok": _demo_state["answer_without_revok"],
            "with_revok": _demo_state["answer_with_revok"],
        }
    )


@app.get("/memories")
async def get_memories() -> JSONResponse:
    """Return raw Mem0 memories for both agents."""
    if _without_revok is None or _with_revok is None:
        return JSONResponse({"error": "agents not ready"}, status_code=503)
    async with aiohttp.ClientSession() as session:
        mems_without, mems_with = await asyncio.gather(
            _without_revok.check_memory_tool(session),
            _with_revok.check_memory_tool(session),
        )
    def _slim(m: dict) -> dict:
        return {
            "id": m.get("id", ""),
            "memory": m.get("memory", str(m)),
            "created_at": m.get("created_at", ""),
            "updated_at": m.get("updated_at", ""),
        }
    return JSONResponse({
        "without_revok": [_slim(m) for m in mems_without],
        "with_revok": [_slim(m) for m in mems_with],
    })


@app.get("/health")
async def health_check() -> JSONResponse:
    """Check health of Mem0, Revok, SQLite, and LLM config."""
    mem0_url = os.getenv("MEM0_URL", "http://localhost:7770")
    revok_url = os.getenv("REVOK_URL", "http://localhost:7771")
    services: dict[str, str] = {}

    # SQLite
    try:
        row = await db_module.get_price(db_module.PRODUCT_NAME)
        services["sqlite"] = "ok" if row is not None else "degraded"
    except Exception:
        services["sqlite"] = "error"

    # Mem0
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{mem0_url}/memories",
                params={"user_id": "health-check"},
                timeout=aiohttp.ClientTimeout(total=3),
            ) as r:
                services["mem0"] = "ok" if r.status < 500 else "error"
    except Exception:
        services["mem0"] = "error"

    # Revok
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{revok_url}/v1/entities",
                timeout=aiohttp.ClientTimeout(total=3),
            ) as r:
                services["revok"] = "ok" if r.status < 500 else "error"
    except Exception:
        services["revok"] = "error"

    # LLM (configuration check only — no live call)
    services["llm"] = (
        "configured"
        if (os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY"))
        else "not_configured"
    )

    overall = (
        "ok"
        if all(v in ("ok", "configured") for v in services.values())
        else "degraded"
    )
    return JSONResponse({"status": overall, "services": services})


@app.get("/stream/ask-agent")
async def stream_ask_agent(
    q: str = Query(default="What is the current price for Redis Enterprise per month?"),
) -> StreamingResponse:
    """SSE endpoint — runs both agents concurrently and multiplexes their events.

    Event types (all carry an ``agent`` field — ``"without_revok"`` or
    ``"with_revok"``):

    * ``agent_started``
    * ``memory_loaded``
    * ``confidence_checked``
    * ``db_reverified``
    * ``token``
    * ``agent_finished``   — includes latency_ms, llm_tokens, path, answer
    * ``done``
    """

    async def generate():
        def evt(data: dict) -> str:
            return f"data: {json.dumps(data)}\n\n"

        if _without_revok is None or _with_revok is None:
            yield evt({"type": "error", "message": "agents not ready"})
            return

        product = db_module.PRODUCT_NAME
        queue: asyncio.Queue[dict] = asyncio.Queue()
        finished: dict[str, dict] = {}

        async def drain(
            agent: PricingSalesAgent, entity_key: str | None
        ) -> None:
            agent_id = "with_revok" if agent.use_revok else "without_revok"
            try:
                async with aiohttp.ClientSession() as sess:
                    async for event in agent.answer_budget_question_streaming(
                        sess, product, entity_key, question=q
                    ):
                        await queue.put(event)
            except Exception as exc:
                await queue.put(
                    {"type": "error", "agent": agent_id, "message": str(exc)}
                )
            finally:
                await queue.put({"type": "_done"})

        t1 = asyncio.create_task(drain(_without_revok, None))
        t2 = asyncio.create_task(drain(_with_revok, _entity_key))

        done_count = 0
        while done_count < 2:
            event = await queue.get()
            if event["type"] == "_done":
                done_count += 1
                continue
            if event["type"] == "agent_finished":
                finished[event["agent"]] = event
            yield evt(event)

        for t in (t1, t2):
            if not t.done():
                t.cancel()

        # ── Update demo state from finished results ─────────────────────
        without = finished.get("without_revok", {})
        with_r = finished.get("with_revok", {})

        if without:
            _demo_state["answer_without_revok"] = {
                "answer": without.get("answer", ""),
                "memory_quote": without.get("memory_quote", ""),
                "re_verified": without.get("re_verified", False),
                "live_price": without.get("live_price"),
                "latency_ms": without.get("latency_ms"),
                "llm_tokens": without.get("llm_tokens"),
                "path": without.get("path"),
            }
            state_module.add_event(
                _demo_state,
                f"Without Revok answered ({without.get('path', 'memory')}): "
                f"{without.get('answer', '')[:80]}",
                kind="answer",
            )

        if with_r:
            with_mode = with_r.get("confidence_status", "unknown")
            if with_r.get("re_verified"):
                with_mode += " → re-verified"
            _demo_state["answer_with_revok"] = {
                "answer": with_r.get("answer", ""),
                "memory_quote": with_r.get("memory_quote", ""),
                "confidence_score": with_r.get("confidence_score"),
                "confidence_status": with_r.get("confidence_status", "unknown"),
                "signal_count": with_r.get("signal_count", 0),
                "re_verified": with_r.get("re_verified", False),
                "live_price": with_r.get("live_price"),
                "latency_ms": with_r.get("latency_ms"),
                "llm_tokens": with_r.get("llm_tokens"),
                "path": with_r.get("path"),
            }
            state_module.add_event(
                _demo_state,
                f"With Revok answered ({with_mode}): {with_r.get('answer', '')[:80]}",
                kind="answer",
            )

        # ── Session scoreboard ──────────────────────────────────────────
        stats: dict = _demo_state.setdefault(
            "session_stats",
            {"questions": 0, "drift_caught": 0, "wrong_answers": 0, "cost_saved": 0.0},
        )
        stats["questions"] += 1
        if with_r.get("re_verified"):
            # Revok detected drift and re-verified — naive agent would have been wrong
            stats["drift_caught"] += 1
            stats["wrong_answers"] += 1
            stats["cost_saved"] = round(stats.get("cost_saved", 0.0) + 500.0, 2)

        state_module.save(_demo_state)
        yield evt({"type": "done"})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/actions/reset")
async def reset_demo() -> JSONResponse:
    """Clear Mem0 memories, reset SQLite price to seed, clear event log."""
    global _demo_state
    if _without_revok is None or _with_revok is None:
        return JSONResponse({"error": "agents not ready"}, status_code=503)

    async with aiohttp.ClientSession() as session:
        await asyncio.gather(
            _without_revok.clear_memories(session),
            _with_revok.clear_memories(session),
        )

    await db_module.reset_to_seed()
    _demo_state = state_module.reset_state()

    db_row = await db_module.get_price(db_module.PRODUCT_NAME)
    if db_row:
        _demo_state["db_price"] = db_row[db_module.COL_PRICE]
        _demo_state["db_updated_at"] = db_row[db_module.COL_UPDATED]

    state_module.add_event(_demo_state, "Demo reset to initial state", kind="info")
    state_module.save(_demo_state)

    return JSONResponse({"reset": True})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("DEMO_PORT", "8080"))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
