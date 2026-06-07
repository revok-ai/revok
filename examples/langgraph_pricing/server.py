"""FastAPI dashboard server for the LangGraph stale-memory pricing demo."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.parse
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
import uvicorn
from dotenv import load_dotenv
from fastapi import Body, FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

import database as db_module
import demo_state as state_module
from agent import LangGraphPricingSalesAgent, _score_to_status

APP_DIR = Path(__file__).parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Product catalog — maps display name → Revok entity key.
# Must stay in sync with the entities: section in revok.yaml.
# ---------------------------------------------------------------------------
PRODUCT_CATALOG: dict[str, str] = {
    "Orion Cache": "orion_cache",
    "Nova Gateway": "nova_gateway",
    "Atlas Search": "atlas_search",
    "Titan Queue": "titan_queue",
    "Spark Store": "spark_store",
}

# ---------------------------------------------------------------------------
# Runtime globals (populated in lifespan)
# ---------------------------------------------------------------------------

_without_revok: LangGraphPricingSalesAgent | None = None
_with_revok: LangGraphPricingSalesAgent | None = None
_demo_state: dict[str, Any] = {}
_entity_key: str = ""

# Cached mem0 health — re-probed at most every MEM0_HEALTH_TTL seconds so that
# a busy Mem0 (e.g. during Load Memory LLM writes) doesn't flicker the UI.
_MEM0_HEALTH_TTL = 10.0
_mem0_health_cache: dict[str, Any] = {"ok": True, "expires": 0.0}


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    """Initialise DB, agents and state on startup."""
    global _without_revok, _with_revok, _demo_state, _entity_key

    load_dotenv(APP_DIR / ".env")

    mem0_url = os.getenv("MEM0_URL", "http://localhost:7772")
    revok_url = os.getenv("REVOK_URL", "http://localhost:7773")
    _entity_key = os.getenv("REVOK_ENTITY", "orion_cache")

    _without_revok = LangGraphPricingSalesAgent(
        name="WITHOUT REVOK",
        user_id="demo-without-revok",
        memory_base_url=mem0_url,
        revok_base_url=None,
        use_revok=False,
    )
    _with_revok = LangGraphPricingSalesAgent(
        name="WITH REVOK",
        user_id="demo-with-revok",
        memory_base_url=revok_url,
        revok_base_url=revok_url,
        use_revok=True,
    )

    await db_module.init_db()
    _demo_state = state_module.load()
    # A previous run may have crashed mid-load and left memory_loading=True in
    # the persisted state file.  Always clear it on startup so the UI is never
    # permanently locked when the container restarts.
    _demo_state["memory_loading"] = False

    # Sync live DB price into state
    db_row = await db_module.get_price(db_module.PRODUCT_NAME)
    if db_row:
        _demo_state[state_module._DEFAULTS.keys() and "db_price"] = db_row[
            db_module.COL_PRICE
        ]
        _demo_state["db_price"] = db_row[db_module.COL_PRICE]
        _demo_state["db_updated_at"] = db_row[db_module.COL_UPDATED]

    _log.info(
        "Demo server ready.  entity_key=%r  product=%r",
        _entity_key,
        db_module.PRODUCT_NAME,
    )
    yield


app = FastAPI(title="Revok LangGraph Pricing Demo", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _detect_product(text: str) -> tuple[str, str]:
    """Return (product_name, entity_key) for the first known product mentioned in *text*.

    Matches case-insensitively against PRODUCT_CATALOG keys.  Falls back to
    the primary product (``PRODUCT_NAME``) if no catalog product is found.
    """
    lower = text.lower()
    for name, key in PRODUCT_CATALOG.items():
        if name.lower() in lower:
            return name, key
    return db_module.PRODUCT_NAME, PRODUCT_CATALOG.get(
        db_module.PRODUCT_NAME, _entity_key
    )


async def _fetch_revok_entity(key: str | None = None) -> dict[str, Any] | None:
    """Query Revok entity API; return ``None`` on error or 404."""
    revok_url = os.getenv("REVOK_URL", "http://localhost:7773")
    encoded = urllib.parse.quote(key if key is not None else _entity_key, safe="")
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
    revok_url = os.getenv("REVOK_URL", "http://localhost:7773")
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
async def index() -> JSONResponse:
    """Dashboard moved to the Next.js app at examples/langgraph_pricing/dashboard."""
    return JSONResponse(
        {
            "message": "Dashboard is now a Next.js app. Run `npm run dev` in ./dashboard.",
            "api": {
                "state": "/state",
                "stream": "/stream",
                "health": "/health",
            },
        }
    )


@app.get("/calculator")
async def calculator() -> HTMLResponse:
    """Serve the standalone ROI calculator page."""
    content = (APP_DIR / "calculator.html").read_text(encoding="utf-8")
    return HTMLResponse(content=content, media_type="text/html; charset=utf-8")


@app.get("/state")
async def get_state() -> JSONResponse:
    """Return full live demo state: DB price + Revok confidence + event log."""
    return JSONResponse(await _build_state_snapshot())


async def _purge_user_memories(
    session: aiohttp.ClientSession,
    mem0_url: str,
    user_id: str,
) -> int:
    """Delete all Mem0 entries for *user_id*, returning the number removed.

    Called at the start of Load Memory so each run starts from a clean slate
    instead of relying on Mem0's LLM deduplication, which can silently drop
    entries across multiple reload cycles.
    """
    deleted = 0
    try:
        async with session.get(
            f"{mem0_url}/memories",
            params={"user_id": user_id},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
        memories = data if isinstance(data, list) else data.get("results", [])
        for mem in memories:
            mem_id = mem.get("id")
            if not mem_id:
                continue
            try:
                async with session.delete(
                    f"{mem0_url}/memories/{mem_id}",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as del_resp:
                    if del_resp.status < 300:
                        deleted += 1
                    else:
                        body = await del_resp.text()
                        _log.warning(
                            "mem0 delete %s returned %s: %s",
                            mem_id,
                            del_resp.status,
                            body[:100],
                        )
            except Exception as exc:
                _log.warning("mem0 delete %s failed: %s", mem_id, exc)
    except Exception as exc:
        _log.warning("_purge_user_memories(%s) list failed: %s", user_id, exc)
    _log.info("Purged %d memories for user %s", deleted, user_id)
    return deleted


async def _heal_pricing_memory(
    user_id: str,
    product_name: str,
    live_price: float,
) -> None:
    """Heal a stale pricing memory in Mem0 after re-verification.

    Deletes any existing mem0 entries that mention *product_name* and a price
    (the old stale value), then writes a fresh entry with *live_price*.  Using
    delete-then-write instead of relying on mem0's LLM deduplication avoids
    the race where dedup silently keeps the old value.

    Runs directly against the Mem0 URL, bypassing the Revok proxy, so there is
    no enrichment overhead that could confuse the dedup decision.
    """
    mem0_url = os.getenv("MEM0_URL", "http://localhost:7772")
    keyword = product_name.lower()
    async with aiohttp.ClientSession() as session:
        # 1. List all memories for this user
        try:
            async with session.get(
                f"{mem0_url}/memories",
                params={"user_id": user_id},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except Exception as exc:
            _log.warning("_heal_pricing_memory: list failed for %s: %s", user_id, exc)
            return
        memories = data if isinstance(data, list) else data.get("results", [])

        # 2. Delete EVERY entry that mentions this product — including entries
        # that mem0's LLM rewrote as price-free "notification" text.
        deleted = 0
        for mem in memories:
            text = (mem.get("memory") or mem.get("content") or "").lower()
            if keyword in text:
                mem_id = mem.get("id")
                if not mem_id:
                    continue
                try:
                    async with session.delete(
                        f"{mem0_url}/memories/{mem_id}",
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as del_resp:
                        if del_resp.status < 300:
                            deleted += 1
                            _log.info(
                                "_heal_pricing_memory: deleted entry %s (%s) for %s",
                                mem_id[:8],
                                text[:40],
                                user_id,
                            )
                except Exception as exc:
                    _log.warning(
                        "_heal_pricing_memory: delete %s failed: %s", mem_id, exc
                    )

        if deleted:
            await asyncio.sleep(2.0)

        # 3. Write fresh corrected memory directly (no LLM dedup race)
        content = (
            f"Customer budget approved {product_name} at ${live_price:.0f}/month. "
            "Verified pricing from database."
        )
        await _store_directly_to_mem0(session, mem0_url, user_id, content)
        _log.info(
            "_heal_pricing_memory: wrote healed memory for %s/%s @ $%.0f",
            user_id,
            product_name,
            live_price,
        )


async def _store_directly_to_mem0(
    session: aiohttp.ClientSession,
    mem0_url: str,
    user_id: str,
    content: str,
) -> None:
    """Write a memory entry directly to Mem0, bypassing the Revok proxy.

    Tolerates non-2xx responses and timeouts so that one slow/failed write
    does not abort the entire Load Memory operation.
    """
    payload = {
        "messages": [{"role": "user", "content": content}],
        "user_id": user_id,
    }
    try:
        async with session.post(
            f"{mem0_url}/memories",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            if resp.status >= 500:
                body = await resp.text()
                _log.warning(
                    "mem0 write returned %s for user %s: %s",
                    resp.status,
                    user_id,
                    body[:200],
                )
            # non-fatal: log and continue
    except Exception as exc:
        _log.warning("mem0 write failed for user %s: %s", user_id, exc)


# ---------------------------------------------------------------------------
# Action request models
# ---------------------------------------------------------------------------


class ChangePriceRequest(BaseModel):
    new_price: float
    product_name: str = ""


class TriggerSignalRequest(BaseModel):
    product_name: str = ""


# ---------------------------------------------------------------------------
# Action endpoints
# ---------------------------------------------------------------------------


@app.post("/actions/load-memory")
async def load_memory() -> JSONResponse:
    """Kick off memory loading in the background and return 202 immediately.

    The Next.js dev-proxy has a short timeout that drops long-running POSTs
    before FastAPI finishes writing all 10 mem0 entries.  Returning 202 at
    once lets the HTTP connection close instantly while the real work runs
    inside an asyncio background task.  The UI already polls ``/state`` every
    second and will pick up each ``Memory loaded: …`` event as it fires.
    """
    if _without_revok is None or _with_revok is None:
        return JSONResponse({"error": "agents not ready"}, status_code=503)

    _demo_state["memory_loading"] = True
    state_module.save(_demo_state)
    asyncio.create_task(_run_load_memory())
    return JSONResponse({"status": "started"}, status_code=202)


async def _run_load_memory() -> None:
    """Background worker: store pricing memory for every catalog product in Mem0."""
    try:
        # Load memories for all catalog products, not just the primary one.
        all_rows = await db_module.list_products()
        catalog_rows = [r for r in all_rows if r[db_module.COL_NAME] in PRODUCT_CATALOG]
        if not catalog_rows:
            fallback = await db_module.get_price(db_module.PRODUCT_NAME)
            catalog_rows = [fallback] if fallback else []

        if not catalog_rows:
            _log.warning("_run_load_memory: no catalog products found in database")
            return

        mem0_url = os.getenv("MEM0_URL", "http://localhost:7772")
        loaded: list[dict] = []

        # Pin the health cache to "ok" for the full duration of Load Memory.
        _mem0_health_cache["ok"] = True
        _mem0_health_cache["expires"] = time.monotonic() + 120.0

        # Purge existing memories for both agents before writing fresh ones.
        async with aiohttp.ClientSession() as session:
            await asyncio.gather(
                _purge_user_memories(session, mem0_url, _without_revok.user_id),
                _purge_user_memories(session, mem0_url, _with_revok.user_id),
            )

        # Write sequentially per product (both agents concurrently per product).
        async with aiohttp.ClientSession() as session:
            for row in catalog_rows:
                product = str(row[db_module.COL_NAME])
                price = float(row[db_module.COL_PRICE])
                content = (
                    f"Customer budget approved {product} at ${price:.0f}/month. "
                    "Verified pricing from database."
                )
                await asyncio.gather(
                    _store_directly_to_mem0(
                        session, mem0_url, _without_revok.user_id, content
                    ),
                    _store_directly_to_mem0(
                        session, mem0_url, _with_revok.user_id, content
                    ),
                )
                loaded.append(
                    {"product": product, "price": price, "memory_content": content}
                )
                state_module.add_event(
                    _demo_state,
                    f"Memory loaded: {product} at ${price:.0f}/month",
                    kind="memory",
                )
                state_module.save(_demo_state)

        # Verify all products were written; retry any that mem0's LLM silently dropped.
        await asyncio.sleep(2.0)
        expected_products = [str(r[db_module.COL_NAME]) for r in catalog_rows]
        for user_id in (_without_revok.user_id, _with_revok.user_id):
            try:
                async with aiohttp.ClientSession() as vsession:
                    async with vsession.get(
                        f"{mem0_url}/memories",
                        params={"user_id": user_id},
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as resp:
                        data = await resp.json()
                        stored_texts = [
                            m.get("memory", "") for m in data.get("results", [])
                        ]
                missing = [
                    p
                    for p in expected_products
                    if not any(p in t for t in stored_texts)
                ]
                if missing:
                    _log.warning(
                        "_run_load_memory: %d missing products for %s: %s — retrying sequentially",
                        len(missing),
                        user_id,
                        missing,
                    )
                    async with aiohttp.ClientSession() as rsession:
                        for row in catalog_rows:
                            product = str(row[db_module.COL_NAME])
                            if product not in missing:
                                continue
                            price = float(row[db_module.COL_PRICE])
                            content = (
                                f"Customer budget approved {product} at ${price:.0f}/month. "
                                "Verified pricing from database."
                            )
                            await _store_directly_to_mem0(
                                rsession, mem0_url, user_id, content
                            )
                            await asyncio.sleep(1.0)
                else:
                    _log.info(
                        "_run_load_memory: verified all %d products for %s",
                        len(expected_products),
                        user_id,
                    )
            except Exception as exc:
                _log.warning(
                    "_run_load_memory: verification failed for %s: %s", user_id, exc
                )

        if loaded:
            primary = loaded[0]
            _demo_state["memory_content"] = primary["memory_content"]
            _demo_state["active_product"] = primary["product"]
            _demo_state["active_entity_key"] = PRODUCT_CATALOG.get(
                primary["product"], _entity_key
            )
            _demo_state["db_price"] = primary["price"]

    except Exception:
        _log.exception("_run_load_memory failed")

    finally:
        _mem0_health_cache["expires"] = 0.0
        _demo_state["memory_loading"] = False
        state_module.save(_demo_state)


@app.post("/actions/change-price")
async def change_price(body: ChangePriceRequest) -> JSONResponse:
    """Update price in SQLite (simulates a SQL change in production)."""
    product = body.product_name.strip() or db_module.PRODUCT_NAME
    updated = await db_module.update_price(product, body.new_price)
    _demo_state["db_price"] = updated[db_module.COL_PRICE]
    _demo_state["db_updated_at"] = updated[db_module.COL_UPDATED]
    state_module.add_event(
        _demo_state,
        f"Price changed to ${body.new_price:.0f}/month for {product} in database",
        kind="db",
    )
    state_module.save(_demo_state)
    return JSONResponse(
        {
            "updated": True,
            "product": product,
            "new_price": body.new_price,
            "updated_at": updated[db_module.COL_UPDATED],
        }
    )


@app.post("/actions/trigger-signal")
async def trigger_signal(
    body: TriggerSignalRequest | None = Body(default=None),
) -> JSONResponse:
    """Simulate a CDC event: an external system signals that pricing data changed.

    Sends a notification through the Revok proxy for the WITH-Revok agent only.
    The ``X-Revok-Entity`` header identifies the entity explicitly, bypassing
    text matching.  The pricing memory is intentionally NOT overwritten — the
    agent must re-verify from the live database to discover the new price.

    The WITHOUT-Revok agent receives no CDC event; its memory stays stale.
    """
    if _with_revok is None:
        return JSONResponse({"error": "agents not ready"}, status_code=503)

    product = (body.product_name.strip() if body else "") or db_module.PRODUCT_NAME
    entity_key = PRODUCT_CATALOG.get(product, product.lower().replace(" ", "_"))

    cdc_content = (
        f"External system notification: pricing record updated for {product}. "
        "Agent memory may be stale — re-verify before quoting."
    )

    entity: dict[str, Any] | None = None
    try:
        revok_url = os.getenv("REVOK_URL", "http://localhost:7773")
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{revok_url}/memories",
                json={
                    "messages": [{"role": "user", "content": cdc_content}],
                    "user_id": _with_revok.user_id,
                },
                headers={"X-Revok-Entity": entity_key},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                _log.info("CDC signal sent through Revok proxy: status=%s", resp.status)

            await asyncio.sleep(0.5)

            encoded = urllib.parse.quote(entity_key, safe="")
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

    score_str = f"{score:.2f}" if score is not None else "N/A"
    state_module.add_event(
        _demo_state,
        f"CDC signal fired — {product} pricing data changed, confidence now {conf_status} (score={score_str})",
        kind="signal",
    )
    state_module.save(_demo_state)

    return JSONResponse(
        {
            "fired": True,
            "entity_key": entity_key,
            "confidence_score": score,
            "confidence_status": conf_status,
            "signal_count": sig_count,
            "last_signal_at": now_iso,
        }
    )


@app.post("/actions/signal-pressure")
async def signal_pressure(
    body: TriggerSignalRequest | None = Body(default=None),
) -> JSONResponse:
    """Simulate a burst of CDC events to drive confidence through degraded → stale.

    Fires 3 signals with 400 ms gaps so the dashboard shows the full
    fresh → degraded → stale progression in real time.
    """
    if _with_revok is None:
        return JSONResponse({"error": "agents not ready"}, status_code=503)

    product = (body.product_name.strip() if body else "") or db_module.PRODUCT_NAME
    entity_key = PRODUCT_CATALOG.get(product, product.lower().replace(" ", "_"))
    encoded = urllib.parse.quote(entity_key, safe="")
    revok_url = os.getenv("REVOK_URL", "http://localhost:7773")

    snapshots: list[dict] = []

    async with aiohttp.ClientSession() as session:
        for i in range(1, 4):
            cdc_content = (
                f"External system notification #{i}: pricing record updated for {product}. "
                "Agent memory may be stale — re-verify before quoting."
            )
            try:
                async with session.post(
                    f"{revok_url}/memories",
                    json={
                        "messages": [{"role": "user", "content": cdc_content}],
                        "user_id": _with_revok.user_id,
                    },
                    headers={"X-Revok-Entity": entity_key},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    _log.info(
                        "Signal pressure %d/%d sent: status=%s", i, 3, resp.status
                    )
            except Exception as exc:
                _log.error("signal-pressure send %d failed: %s", i, exc)

            await asyncio.sleep(0.4)

            try:
                async with session.get(
                    f"{revok_url}/v1/entities/{encoded}",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as eresp:
                    entity = await eresp.json() if eresp.status == 200 else None
            except Exception:
                entity = None

            score: float | None = float(entity["score"]) if entity else None
            sig_count: int = int(entity["signal_count"]) if entity else i
            conf_status = _score_to_status(score)
            score_str = f"{score:.2f}" if score is not None else "N/A"
            snapshots.append({"signal": i, "score": score, "status": conf_status})

            _demo_state["confidence_score"] = score
            _demo_state["confidence_status"] = conf_status
            _demo_state["signal_count"] = sig_count
            _demo_state["last_signal_at"] = datetime.now(timezone.utc).isoformat()

            state_module.add_event(
                _demo_state,
                f"[Pressure {i}/3] Signal fired → score={score_str}, status={conf_status}",
                kind="signal",
            )
            state_module.save(_demo_state)

    final = snapshots[-1] if snapshots else {}
    return JSONResponse(
        {
            "fired": True,
            "signals_sent": len(snapshots),
            "entity_key": entity_key,
            "final_score": final.get("score"),
            "final_status": final.get("status"),
            "progression": snapshots,
        }
    )


_DEFAULT_QUESTION = "What is the current price for Orion Cache per month?"


class AskAgentRequest(BaseModel):
    question: str = _DEFAULT_QUESTION


@app.post("/actions/ask-agent")
async def ask_agent(body: AskAgentRequest | None = Body(default=None)) -> JSONResponse:
    """Run the sales agent in both modes; store answers in state."""
    if _without_revok is None or _with_revok is None:
        return JSONResponse({"error": "agents not ready"}, status_code=503)

    question = (body.question.strip() if body else "") or _DEFAULT_QUESTION
    product, detected_entity_key = _detect_product(question)

    db_row = await db_module.get_price(product)
    if db_row:
        _demo_state["db_price"] = db_row[db_module.COL_PRICE]
        _demo_state["db_updated_at"] = db_row[db_module.COL_UPDATED]
    _demo_state["active_product"] = product
    _demo_state["active_entity_key"] = detected_entity_key

    async with aiohttp.ClientSession() as session:
        state_module.add_event(
            _demo_state, f"Asking agent WITHOUT Revok about {product}…", kind="info"
        )
        state_module.save(_demo_state)
        ans_without = await _without_revok.answer_budget_question(
            session, product, None, question=question
        )

        if ans_without.memory_quote:
            _demo_state["memory_content"] = ans_without.memory_quote

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

        state_module.add_event(
            _demo_state, f"Asking agent WITH Revok about {product}…", kind="info"
        )
        state_module.save(_demo_state)
        ans_with = await _with_revok.answer_budget_question(
            session, product, detected_entity_key, question=question
        )

        if ans_with.re_verified and ans_with.live_price is not None:
            await _heal_pricing_memory(
                _with_revok.user_id, product, ans_with.live_price
            )

        with_mode = ans_with.confidence_status + (
            " → re-verified" if ans_with.re_verified else ""
        )
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
    try:
        async with aiohttp.ClientSession() as session:
            mems_without, mems_with = await asyncio.gather(
                _without_revok.check_memory_tool(session),
                _with_revok.check_memory_tool(session),
            )
    except Exception:
        return JSONResponse({"error": "mem0 unavailable"}, status_code=503)

    def _slim(m: dict) -> dict:
        return {
            "id": m.get("id", ""),
            "memory": m.get("memory", str(m)),
            "created_at": m.get("created_at", ""),
            "updated_at": m.get("updated_at", ""),
        }

    return JSONResponse(
        {
            "without_revok": [_slim(m) for m in mems_without],
            "with_revok": [_slim(m) for m in mems_with],
        }
    )


@app.get("/health")
async def health_check() -> JSONResponse:
    """Check health of Mem0, Revok, SQLite, and LLM config."""
    mem0_url = os.getenv("MEM0_URL", "http://localhost:7772")
    revok_url = os.getenv("REVOK_URL", "http://localhost:7773")
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
    return JSONResponse(
        {"status": overall, "framework": "langgraph", "services": services}
    )


@app.get("/stream/ask-agent")
async def stream_ask_agent(
    q: str = Query(default="What is the current price for Orion Cache per month?"),
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

        product, detected_entity_key = _detect_product(q)

        db_row_for_product = await db_module.get_price(product)
        if db_row_for_product:
            _demo_state["db_price"] = db_row_for_product[db_module.COL_PRICE]
            _demo_state["db_updated_at"] = db_row_for_product[db_module.COL_UPDATED]
        _demo_state["active_product"] = product
        _demo_state["active_entity_key"] = detected_entity_key

        queue: asyncio.Queue[dict] = asyncio.Queue()
        finished: dict[str, dict] = {}

        async def drain(
            agent: LangGraphPricingSalesAgent, entity_key: str | None
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
        t2 = asyncio.create_task(drain(_with_revok, detected_entity_key))

        done_count = 0
        while done_count < 2:
            event = await queue.get()
            if event["type"] == "_done":
                done_count += 1
                continue
            if event["type"] == "agent_finished":
                finished[event["agent"]] = event
            elif event["type"] == "confidence_checked":
                score = event.get("score")
                status = event.get("status", "unknown")
                sigs = event.get("signal_count", 0)
                score_str = f"{score:.2f}" if score is not None else "N/A"
                state_module.add_event(
                    _demo_state,
                    f"[Revok] Confidence check → status={status}, score={score_str}, signals={sigs}",
                    kind="signal",
                )
            elif event["type"] == "db_reverified":
                live_p = event.get("live_price")
                reason = event.get("reason", "stale")
                price_str = f"${live_p:.0f}/month" if live_p is not None else "unknown"
                state_module.add_event(
                    _demo_state,
                    f"[Revok] Memory was {reason} — re-verified from DB → live price={price_str}",
                    kind="database",
                )
            yield evt(event)

        for t in (t1, t2):
            if not t.done():
                t.cancel()

        # ── Update demo state from finished results ─────────────────────
        without = finished.get("without_revok", {})
        with_r = finished.get("with_revok", {})

        if without:
            if without.get("memory_quote"):
                _demo_state["memory_content"] = without["memory_quote"]
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
                live_p = with_r.get("live_price")
                if live_p is not None:
                    await _heal_pricing_memory(
                        _with_revok.user_id, product, float(live_p)
                    )
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


async def _build_state_snapshot() -> dict[str, Any]:
    """Return the canonical demo-state snapshot used by `/state` and `/stream`."""
    active_product = _demo_state.get("active_product", db_module.PRODUCT_NAME)
    db_row = await db_module.get_price(active_product)
    all_db_products = await db_module.list_products()
    entity = await _fetch_revok_entity()
    revok_ok = await _revok_reachable()

    mem0_url = os.getenv("MEM0_URL", "http://localhost:7772")
    now = time.monotonic()
    if now >= _mem0_health_cache["expires"]:
        mem0_ok = False
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f"{mem0_url}/memories",
                    params={"user_id": "health-check"},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as r:
                    mem0_ok = r.status < 500
        except Exception:
            mem0_ok = False
        _mem0_health_cache["ok"] = mem0_ok
        _mem0_health_cache["expires"] = now + _MEM0_HEALTH_TTL
    else:
        mem0_ok = _mem0_health_cache["ok"]

    if entity is not None:
        score: float | None = float(entity["score"])
        sig_count = int(entity["signal_count"])
        conf_status = _score_to_status(score)
    elif _demo_state.get("memory_content"):
        score = 1.0
        sig_count = 0
        conf_status = "fresh"
    else:
        score = None
        sig_count = _demo_state.get("signal_count", 0)
        conf_status = _score_to_status(score)

    if db_row:
        _demo_state["db_price"] = db_row[db_module.COL_PRICE]
        _demo_state["db_updated_at"] = db_row[db_module.COL_UPDATED]
    _demo_state["confidence_score"] = score
    _demo_state["confidence_status"] = conf_status
    _demo_state["signal_count"] = sig_count

    def _entity_key_for(name: str) -> str:
        return PRODUCT_CATALOG.get(name, name.lower().replace(" ", "_"))

    product_entities = await asyncio.gather(
        *[
            _fetch_revok_entity(_entity_key_for(row[db_module.COL_NAME]))
            for row in all_db_products
        ]
    )
    products: list[dict[str, Any]] = []
    for row, ent in zip(all_db_products, product_entities):
        if ent is not None:
            p_score: float | None = float(ent["score"])
            p_sig = int(ent["signal_count"])
            p_status = _score_to_status(p_score)
        else:
            p_score = 1.0 if _demo_state.get("memory_content") else None
            p_sig = 0
            p_status = (
                "fresh" if _demo_state.get("memory_content") else _score_to_status(None)
            )
        products.append(
            {
                "name": row[db_module.COL_NAME],
                "entity_key": _entity_key_for(row[db_module.COL_NAME]),
                "price": row[db_module.COL_PRICE],
                "updated_at": row[db_module.COL_UPDATED],
                "confidence_score": p_score,
                "confidence_status": p_status,
                "signal_count": p_sig,
            }
        )

    return {
        **_demo_state,
        "memory_loading": _demo_state.get("memory_loading", False),
        "db_product": active_product,
        "product_name": active_product,
        "entity_key": _demo_state.get("active_entity_key", _entity_key),
        "revok_reachable": revok_ok,
        "services": {"revok": revok_ok, "mem0": mem0_ok},
        "products": products,
    }


# ---------------------------------------------------------------------------
# Products endpoints
# ---------------------------------------------------------------------------


@app.get("/products")
async def list_products() -> JSONResponse:
    """Return all tracked products with their live price and Revok confidence."""
    all_db = await db_module.list_products()

    def _ekey(name: str) -> str:
        return PRODUCT_CATALOG.get(name, name.lower().replace(" ", "_"))

    entities = await asyncio.gather(
        *[_fetch_revok_entity(_ekey(r[db_module.COL_NAME])) for r in all_db]
    )
    results = []
    for row, ent in zip(all_db, entities):
        if ent is not None:
            score: float | None = float(ent["score"])
            sig = int(ent["signal_count"])
        else:
            score = None
            sig = 0
        results.append(
            {
                "name": row[db_module.COL_NAME],
                "entity_key": _ekey(row[db_module.COL_NAME]),
                "price": row[db_module.COL_PRICE],
                "updated_at": row[db_module.COL_UPDATED],
                "confidence_score": score,
                "confidence_status": _score_to_status(score),
                "signal_count": sig,
            }
        )
    return JSONResponse({"total": len(results), "products": results})


class BulkSeedRequest(BaseModel):
    count: int = 50


_BULK_PREFIXES = [
    "Quantum",
    "Nexus",
    "Apex",
    "Stellar",
    "Vortex",
    "Echo",
    "Prism",
    "Flux",
    "Hyper",
    "Solar",
    "Nano",
    "Turbo",
    "Ultra",
    "Micro",
    "Omni",
    "Meta",
]
_BULK_TYPES = [
    "Cache",
    "Gateway",
    "Search",
    "Queue",
    "Store",
    "Engine",
    "Index",
    "Mesh",
    "Broker",
    "Stream",
    "Relay",
    "Vault",
    "Router",
    "Hub",
    "Node",
    "Sync",
]


@app.post("/actions/bulk-seed")
async def bulk_seed(body: BulkSeedRequest | None = Body(default=None)) -> JSONResponse:
    """Seed N synthetic products into SQLite and register each with Revok."""
    count = max(1, min((body.count if body else 50), 500))
    revok_url = os.getenv("REVOK_URL", "http://localhost:7773")

    seeded: list[dict[str, Any]] = []
    errors = 0

    async with aiohttp.ClientSession() as session:
        for i in range(count):
            prefix = _BULK_PREFIXES[i % len(_BULK_PREFIXES)]
            ptype = _BULK_TYPES[(i // len(_BULK_PREFIXES)) % len(_BULK_TYPES)]
            name = f"{prefix} {ptype} v{i + 1}"
            entity_key = name.lower().replace(" ", "_")
            price = round(49.0 + (i * 9.7) % 951.0, 2)

            await db_module.upsert_product(name, price)

            try:
                async with session.post(
                    f"{revok_url}/memories",
                    json={
                        "messages": [
                            {
                                "role": "user",
                                "content": f"Pricing initialized: {name} at ${price:.0f}/month.",
                            }
                        ],
                        "user_id": "bulk-seed",
                    },
                    headers={"X-Revok-Entity": entity_key},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status >= 400:
                        errors += 1
            except Exception:
                errors += 1

            seeded.append({"name": name, "entity_key": entity_key, "price": price})

    state_module.add_event(
        _demo_state,
        f"Bulk-seeded {len(seeded)} synthetic products into DB + Revok (errors={errors})",
        kind="info",
    )
    state_module.save(_demo_state)

    return JSONResponse({"seeded": len(seeded), "errors": errors, "products": seeded})


@app.get("/stream")
async def stream_state() -> StreamingResponse:
    """SSE endpoint emitting the full demo state once per second."""

    async def generate():
        last_payload: str | None = None
        try:
            while True:
                snapshot = await _build_state_snapshot()
                payload = json.dumps(snapshot, default=str)
                if payload != last_payload:
                    yield f"data: {payload}\n\n"
                    last_payload = payload
                else:
                    yield ": ping\n\n"
                await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.post("/actions/reset")
async def reset_demo() -> JSONResponse:
    """Clear Mem0 memories, reset SQLite price to seed, clear Revok entity state, clear event log."""
    global _demo_state
    if _without_revok is None or _with_revok is None:
        return JSONResponse({"error": "agents not ready"}, status_code=503)

    revok_url = os.getenv("REVOK_URL", "http://localhost:7773")
    encoded = urllib.parse.quote(_entity_key, safe="")
    async with aiohttp.ClientSession() as session:
        await asyncio.gather(
            _without_revok.clear_memories(session),
            _with_revok.clear_memories(session),
        )
        try:
            async with session.delete(
                f"{revok_url}/v1/entities/{encoded}",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                _log.info("Revok entity reset: status=%s", resp.status)
        except Exception as exc:
            _log.warning("Could not reset Revok entity state: %s", exc)

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
    port = int(os.getenv("DEMO_PORT", "8081"))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
