"""FastAPI dashboard server for the AgentFramework + Redis AMS entitlement cascade demo."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.parse
import uuid as _uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
import uvicorn
from dotenv import load_dotenv
from fastapi import Body, FastAPI, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

import threading

import database as db_module
import demo_state as state_module
import agent as agent_module
from agent import AgentFrameworkCustomerSuccessAgent, _score_to_status, build_agents

APP_DIR = Path(__file__).parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Root entity key — the subscription tier drives BFS propagation to all
# four dependent entities (seat-limit, feature-entitlements, api-rate-limit,
# billing-terms).  Must match the entity id in revok.yaml.
# ---------------------------------------------------------------------------
_ROOT_ENTITY_KEY: str = "subscription-tier"

# Dependent entity keys in BFS propagation order (closest first)
_DEPENDENT_ENTITY_KEYS: list[str] = [
    "seat-limit",
    "feature-entitlements",
    "api-rate-limit",
    "billing-terms",
]

# ---------------------------------------------------------------------------
# Runtime globals (populated in lifespan)
# ---------------------------------------------------------------------------

_without_revok: AgentFrameworkCustomerSuccessAgent | None = None
_with_revok: AgentFrameworkCustomerSuccessAgent | None = None
_demo_state: dict[str, Any] = {}
_entity_key: str = ""

# Cached AMS health — re-probed at most every _AMS_HEALTH_TTL seconds so that
# a busy AMS doesn't flicker the UI.
_AMS_HEALTH_TTL = 10.0
_ams_health_cache: dict[str, Any] = {"ok": True, "expires": 0.0}

# near the top, with other module-level config
_REVOK_AUTH: aiohttp.BasicAuth | None = None

def _build_revok_auth() -> aiohttp.BasicAuth | None:
    user = os.getenv("REVOK_DEMO_AUTH_USER", "")
    password = os.getenv("REVOK_DEMO_AUTH_PASS", "")
    if user and password:
        return aiohttp.BasicAuth(user, password)
    return None

# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    """Initialise DB, agents and state on startup."""
    global _without_revok, _with_revok, _demo_state, _entity_key, _REVOK_AUTH

    load_dotenv(APP_DIR / ".env")

    ams_url = os.getenv("AMS_URL", "http://localhost:8000")
    revok_url = os.getenv("REVOK_URL", "http://localhost:7771")
    _REVOK_AUTH = _build_revok_auth()
    # Populate agent module config before building agents
    agent_module._config.update({
        "ams_url": ams_url,
        "revok_url": revok_url,
        "revok_auth": _REVOK_AUTH,
        "without_revok_user_id": "demo-without-revok",
        "with_revok_user_id": "demo-with-revok",
    })

    await db_module.init_db()
    agent_module._config["db_path"] = db_module.DB_PATH

    _without_revok, _with_revok = build_agents()

    # Start AF DevUI in a background daemon thread on port 8082
    devui_token = os.getenv("DEVUI_AUTH_TOKEN", "revok-demo")
    try:
        from agent_framework.devui import serve as devui_serve  # type: ignore
        devui_thread = threading.Thread(
            target=lambda: devui_serve(
                entities=[_with_revok._agent, _without_revok._agent],
                host="0.0.0.0",
                port=8082,
                auto_open=False,
                auth_token=devui_token,
            ),
            daemon=True,
            name="af-devui",
        )
        devui_thread.start()
        _log.info("AF DevUI started on port 8082")
    except Exception as devui_exc:
        _log.warning("AF DevUI could not start: %s", devui_exc)

    _demo_state = state_module.load()
    # A previous run may have crashed mid-load and left memory_loading=True in
    # the persisted state file.  Always clear it on startup so the UI is never
    # permanently locked when the container restarts.
    _demo_state["memory_loading"] = False

    # Sync live subscription state into demo state
    sub = await db_module.get_subscription()
    if sub:
        _demo_state["db_subscription_tier"] = sub[db_module.COL_TIER]
        _demo_state["db_seat_limit"] = sub[db_module.COL_SEATS]
        _demo_state["db_feature_entitlements"] = sub[db_module.COL_FEATURES]
        _demo_state["db_api_rate_limit"] = sub[db_module.COL_API_RATE]
        _demo_state["db_billing_terms"] = sub[db_module.COL_BILLING]
        _demo_state["db_updated_at"] = sub[db_module.COL_UPDATED]

    _log.info(
        "Demo server ready.  root_entity=%r  customer=%r",
        _ROOT_ENTITY_KEY,
        db_module.CUSTOMER_ID,
    )
    yield


app = FastAPI(title="Revok AgentFramework + Redis AMS Entitlement Demo", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _fetch_revok_entity(key: str | None = None) -> dict[str, Any] | None:
    """Query Revok entity API; return ``None`` on error or 404."""
    revok_url = os.getenv("REVOK_URL", "http://localhost:7771")
    encoded = urllib.parse.quote(key if key is not None else _entity_key, safe="")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{revok_url}/v1/entities/{encoded}",
                auth=_REVOK_AUTH,
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
                auth=_REVOK_AUTH,
                timeout=aiohttp.ClientTimeout(total=3),
            ) as resp:
                return resp.status < 500
    except Exception:
        return False


async def _purge_user_memories(
    session: aiohttp.ClientSession,  # noqa: ARG001 — kept for API compat, unused
    ams_url: str,
    user_id: str,
) -> int:
    """Delete all Redis AMS long-term memories for *user_id*.

    The /forget endpoint's policy filter is broken (always 0 deleted) and bulk
    DELETE is also broken.  Working approach: search → collect IDs → delete
    each one individually.  Creates a fresh ClientSession per request to avoid
    ServerDisconnectedError from the AMS keep-alive behaviour.
    """
    # These terms are guaranteed to match because "subscription" and "tier" are
    # hardcoded literals in the memory write template:
    #   server.py  _run_load_memory()       → "Customer subscription tier: {tier}. ..."
    #   agent.py   store_corrected_memory() → same template
    # If that template wording changes, update these search terms to match.
    search_terms = ["subscription", "tier"]
    seen_ids: set[str] = set()
    try:
        for term in search_terms:
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    f"{ams_url}/v1/long-term-memory/search",
                    json={"text": term, "search_mode": "keyword",
                          "session_id": {"eq": user_id}, "limit": 100},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status >= 300:
                        continue
                    data = await resp.json()
                    mems = data if isinstance(data, list) else data.get("memories", data.get("results", []))
                    for m in mems:
                        seen_ids.add(m["id"])
        deleted = 0
        for mem_id in seen_ids:
            try:
                async with aiohttp.ClientSession() as s:
                    async with s.delete(
                        f"{ams_url}/v1/long-term-memory",
                        params=[("memory_ids", mem_id)],
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        if resp.status < 300:
                            deleted += 1
            except Exception:
                pass
    except Exception as exc:
        _log.warning("_purge_user_memories(%s) failed: %s", user_id, exc)
        return 0
    _log.info("_purge_user_memories: deleted %d memories for session %s", deleted, user_id)
    return deleted


async def _store_directly_to_ams(
    session: aiohttp.ClientSession,
    ams_url: str,
    user_id: str,
    content: str,
    entity_key: str | None = None,
) -> None:
    """Write a memory entry directly to Redis AMS, bypassing the Revok proxy.

    Tolerates non-2xx responses and timeouts so one slow/failed write does
    not abort the entire Load Memory operation.
    """
    # ExtractedMemoryRecord requires 'id' (mandatory per AMS OpenAPI schema).
    # deduplicate=false skips semantic deduplication which requires embeddings.
    payload = {
        "memories": [
            {
                "id": str(_uuid.uuid4()),
                "text": content,
                "session_id": user_id,
                "namespace": "entitlements",
            }
        ],
        "deduplicate": False,
    }
    headers: dict[str, str] = {}
    if entity_key:
        headers["X-Revok-Entity"] = entity_key
    try:
        async with session.post(
            f"{ams_url}/v1/long-term-memory/",
            json=payload,
            headers=headers if headers else None,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            if resp.status >= 300:
                body = await resp.text()
                _log.warning(
                    "AMS write returned %s for session %s: %s",
                    resp.status,
                    user_id,
                    body[:300],
                )
            else:
                _log.info("AMS write OK for session %s (status=%s)", user_id, resp.status)
    except Exception as exc:
        _log.warning("AMS write failed for session %s: %s", user_id, exc)


# ---------------------------------------------------------------------------
# Action request models
# ---------------------------------------------------------------------------


class RunRequest(BaseModel):
    """AG-UI protocol run request body."""

    threadId: str = ""
    runId: str = ""
    messages: list[dict] = []
    state: dict = {}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/")
async def index() -> JSONResponse:
    """Dashboard is the Next.js app in ./dashboard."""
    return JSONResponse(
        {
            "message": "Dashboard is a Next.js app. Run `npm run dev` in ./dashboard.",
            "api": {
                "state": "/state",
                "stream": "/stream",
                "health": "/health",
            },
        }
    )


@app.get("/state")
async def get_state() -> JSONResponse:
    """Return full live demo state: DB subscription + Revok confidence + event log."""
    return JSONResponse(await _build_state_snapshot())


@app.post("/actions/load-memory")
async def load_memory() -> JSONResponse:
    """Kick off memory loading in the background and return 202 immediately."""
    if _without_revok is None or _with_revok is None:
        return JSONResponse({"error": "agents not ready"}, status_code=503)

    _demo_state["memory_loading"] = True
    state_module.save(_demo_state)
    asyncio.create_task(_run_load_memory())
    return JSONResponse({"status": "started"}, status_code=202)


async def _run_load_memory() -> None:
    """Background worker: store subscription entitlement memory in Redis AMS for both agents."""
    try:
        sub = await db_module.get_subscription()
        if not sub:
            _log.warning("_run_load_memory: no subscription record found in database")
            return

        ams_url = os.getenv("AMS_URL", "http://localhost:8000")

        # Pin the health cache to "ok" for the full duration of Load Memory.
        _ams_health_cache["ok"] = True
        _ams_health_cache["expires"] = time.monotonic() + 120.0

        # Purge existing memories for both agents before writing fresh ones.
        async with aiohttp.ClientSession() as session:
            await asyncio.gather(
                _purge_user_memories(session, ams_url, _without_revok.user_id),
                _purge_user_memories(session, ams_url, _with_revok.user_id),
            )

        tier = sub[db_module.COL_TIER]
        seats = sub[db_module.COL_SEATS]
        features = sub[db_module.COL_FEATURES]
        api_rate = sub[db_module.COL_API_RATE]
        billing = sub[db_module.COL_BILLING]
        content = (
            f"Customer subscription tier: {tier}. "
            f"Seat limit: {seats}. "
            f"Feature entitlements: {features}. "
            f"API rate limit: {api_rate}. "
            f"Billing terms: {billing}. "
            "Verified from database."
        )

        async with aiohttp.ClientSession() as session:
            await asyncio.gather(
                _store_directly_to_ams(session, ams_url, _without_revok.user_id, content),
                _store_directly_to_ams(session, ams_url, _with_revok.user_id, content),
            )

        _demo_state["memory_content"] = content
        _demo_state["db_subscription_tier"] = tier
        _demo_state["db_seat_limit"] = seats
        _demo_state["db_feature_entitlements"] = features
        _demo_state["db_api_rate_limit"] = api_rate
        _demo_state["db_billing_terms"] = billing

        state_module.add_event(
            _demo_state,
            f"Customer profile loaded: {tier} tier, {seats} seats",
            kind="memory",
        )
        state_module.save(_demo_state)

    except Exception:
        _log.exception("_run_load_memory failed")

    finally:
        _ams_health_cache["expires"] = 0.0
        _demo_state["memory_loading"] = False
        state_module.save(_demo_state)


@app.post("/actions/downgrade-plan")
async def downgrade_plan() -> JSONResponse:
    """Downgrade customer subscription to Starter tier in SQLite.

    Simulates a billing system webhook that changes the customer's plan
    without the agent being notified.  The agent's memory still says Enterprise.
    """
    updated = await db_module.downgrade_to_starter()
    _demo_state["db_subscription_tier"] = updated[db_module.COL_TIER]
    _demo_state["db_seat_limit"] = updated[db_module.COL_SEATS]
    _demo_state["db_feature_entitlements"] = updated[db_module.COL_FEATURES]
    _demo_state["db_api_rate_limit"] = updated[db_module.COL_API_RATE]
    _demo_state["db_billing_terms"] = updated[db_module.COL_BILLING]
    _demo_state["db_updated_at"] = updated[db_module.COL_UPDATED]
    state_module.add_event(
        _demo_state,
        f"Billing system downgraded plan to {updated[db_module.COL_TIER]} "
        f"({updated[db_module.COL_SEATS]} seats)",
        kind="billing",
    )
    state_module.save(_demo_state)
    return JSONResponse({"updated": True, "subscription": updated})


@app.post("/actions/fire-signal")
async def fire_signal() -> JSONResponse:
    """Simulate a CDC event: billing system signals that the subscription changed.

    Kicks off the actual work in the background and returns 202 immediately,
    following the same pattern as POST /actions/load-memory.
    """
    if _with_revok is None:
        return JSONResponse({"error": "agents not ready"}, status_code=503)

    asyncio.create_task(_run_fire_signal())
    return JSONResponse({"status": "started"}, status_code=202)


async def _run_fire_signal() -> None:
    """Background worker: send the CDC signal through the Revok proxy.

    Sends a notification through the Revok proxy (WITH-Revok agent only) via
    POST /v1/long-term-memory so Revok can intercept the write, record the
    signal, and degrade confidence on the subscription-tier entity.  BFS
    propagation will also degrade all four dependent entities.  Updates
    ``_demo_state`` with the resulting confidence data on success, or logs
    the failure as an event on error.
    """
    cdc_content = (
        "Billing system notification: subscription plan changed for customer. "
        "Agent memory may be stale — re-verify all entitlements before answering."
    )

    entity: dict[str, Any] | None = None
    dep_scores: dict[str, float | None] = {}
    try:
        revok_url = os.getenv("REVOK_URL", "http://localhost:7771")
        async with aiohttp.ClientSession() as session:
            async def _write_memory() -> None:
                # Best-effort: this write proxies through Revok to the upstream
                # AMS store, which can hang/time out independently of causal
                # propagation. A slow/hung upstream write must NOT block the
                # /signals call below, or dependent entities never propagate.
                try:
                    async with session.post(
                        f"{revok_url}/v1/long-term-memory/",
                        json={
                            "memories": [
                                {
                                    "text": cdc_content,
                                    "session_id": _with_revok.user_id,
                                    "namespace": "entitlements",
                                }
                            ]
                        },
                        headers={"X-Revok-Entity": _ROOT_ENTITY_KEY},
                        auth=_REVOK_AUTH,
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as resp:
                        _log.info(
                            "Billing CDC signal sent through Revok proxy: status=%s", resp.status
                        )
                except Exception as exc:
                    detail = str(exc) or type(exc).__name__
                    _log.warning("Billing CDC memory write failed (continuing): %s", detail)

            async def _send_signal() -> None:
                # This is what actually triggers BFS propagation to dependent
                # entities via SignalProcessor — must run independently of the
                # memory write above.
                async with session.post(
                    f"{revok_url}/signals",
                    json={
                        "entity_refs": [_ROOT_ENTITY_KEY],
                        "severity": "high",
                        "source": "billing-cdc",
                    },
                    auth=_REVOK_AUTH,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as sresp:
                    if sresp.status >= 400:
                        detail = await sresp.text()
                        raise RuntimeError(
                            f"/signals failed: status={sresp.status} body={detail}"
                        )
                    _log.info("Causal propagation signal sent: status=%s", sresp.status)

            _, signal_result = await asyncio.gather(
                _write_memory(),
                _send_signal(),
                return_exceptions=True,
            )
            if isinstance(signal_result, Exception):
                raise signal_result

            await asyncio.sleep(0.5)

            async def _fetch_root_entity() -> dict[str, Any] | None:
                encoded = urllib.parse.quote(_ROOT_ENTITY_KEY, safe="")
                async with session.get(
                    f"{revok_url}/v1/entities/{encoded}",
                    auth=_REVOK_AUTH,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as eresp:
                    if eresp.status == 200:
                        return await eresp.json()
                    return None

            # Root entity fetch and dependent-entity scores are independent reads
            # once the signal has been sent — run them concurrently.
            entity, dep_scores = await asyncio.gather(
                _fetch_root_entity(),
                _fetch_dependent_scores(revok_url, session),
            )

    except Exception as exc:
        # str(exc) is empty for asyncio.TimeoutError, so always include the
        # exception type name or the message would silently be blank.
        detail = str(exc) or type(exc).__name__
        _log.exception("fire-signal failed: %s", detail)
        state_module.add_event(
            _demo_state,
            f"Billing CDC signal failed: {detail}",
            kind="info",
        )
        state_module.save(_demo_state)
        return

    now_iso = datetime.now(timezone.utc).isoformat()
    score: float | None = float(entity["score"]) if entity else None
    sig_count: int = int(entity["signal_count"]) if entity else 0
    conf_status = _score_to_status(score)

    _demo_state["last_signal_at"] = now_iso
    _demo_state["confidence_score"] = score
    _demo_state["confidence_status"] = conf_status
    _demo_state["signal_count"] = sig_count
    _demo_state["dependent_scores"] = dep_scores

    score_str = f"{score:.2f}" if score is not None else "N/A"
    state_module.add_event(
        _demo_state,
        f"Billing CDC signal fired — subscription data changed, confidence now {conf_status} (score={score_str})",
        kind="signal",
    )
    state_module.save(_demo_state)


async def _fetch_dependent_scores(
    revok_url: str,
    session: aiohttp.ClientSession,
) -> dict[str, float | None]:
    """Query Revok for the propagated confidence score of each dependent entity.

    Fires all lookups concurrently so the worst case is one 5s timeout instead
    of N sequential timeouts.

    Returns a dict mapping entity key → float score (or None if not yet tracked).
    """
    async def _fetch_one(key: str) -> float | None:
        encoded = urllib.parse.quote(key, safe="")
        try:
            async with session.get(
                f"{revok_url}/v1/entities/{encoded}",
                auth=_REVOK_AUTH,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return float(data.get("score", 1.0))
                elif resp.status == 404:
                    return None
                else:
                    return None
        except Exception as exc:
            _log.debug("_fetch_dependent_scores(%s) failed: %s", key, exc)
            return None

    results = await asyncio.gather(*(_fetch_one(key) for key in _DEPENDENT_ENTITY_KEYS))
    return dict(zip(_DEPENDENT_ENTITY_KEYS, results))


_DEFAULT_QUESTION = "What does this customer's current plan include?"


class AskAgentRequest(BaseModel):
    question: str = _DEFAULT_QUESTION


@app.post("/actions/ask-agent")
async def ask_agent(body: AskAgentRequest | None = Body(default=None)) -> JSONResponse:
    """Run the customer success agent in both modes; store answers in state."""
    if _without_revok is None or _with_revok is None:
        return JSONResponse({"error": "agents not ready"}, status_code=503)

    question = (body.question.strip() if body else "") or _DEFAULT_QUESTION

    state_module.add_event(
        _demo_state,
        "Asking both agents about subscription entitlements (running in parallel)…",
        kind="info",
    )
    state_module.save(_demo_state)

    async with aiohttp.ClientSession() as session:
        ans_without, ans_with = await asyncio.gather(
            _without_revok.answer_entitlement_question(
                session, None, question=question
            ),
            _with_revok.answer_entitlement_question(
                session, _ROOT_ENTITY_KEY, question=question
            ),
        )

        if ans_with.memory_quote:
            _demo_state["memory_content"] = ans_with.memory_quote

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
            "live_entitlements": ans_without.live_entitlements,
        }

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
            "live_entitlements": ans_with.live_entitlements,
        }

        # Session scoreboard
        stats: dict = _demo_state.setdefault(
            "session_stats",
            {"questions": 0, "drift_caught": 0, "wrong_answers": 0, "support_escalations_prevented": 0},
        )
        stats["questions"] += 1
        if ans_with.re_verified:
            stats["drift_caught"] += 1
            stats["wrong_answers"] += 1
            stats["support_escalations_prevented"] += 1

        state_module.save(_demo_state)

    return JSONResponse(
        {
            "without_revok": _demo_state["answer_without_revok"],
            "with_revok": _demo_state["answer_with_revok"],
        }
    )


@app.get("/memories")
async def get_memories() -> JSONResponse:
    """Return raw Redis AMS memories for both agents."""
    if _without_revok is None or _with_revok is None:
        return JSONResponse({"error": "agents not ready"}, status_code=503)
    try:
        async with aiohttp.ClientSession() as session:
            mems_without, mems_with = await asyncio.gather(
                _without_revok.check_memory_tool(session),
                _with_revok.check_memory_tool(session),
            )
    except Exception:
        return JSONResponse({"error": "redis-ams unavailable"}, status_code=503)

    def _slim(m: dict) -> dict:
        return {
            "id": m.get("id", ""),
            "memory": m.get("text") or m.get("memory") or str(m),
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
    """Check health of Redis AMS, Revok, SQLite, and LLM config."""
    ams_url = os.getenv("AMS_URL", "http://localhost:8000")
    revok_url = os.getenv("REVOK_URL", "http://localhost:7771")
    services: dict[str, str] = {}

    # SQLite
    try:
        sub = await db_module.get_subscription()
        services["sqlite"] = "ok" if sub is not None else "degraded"
    except Exception:
        services["sqlite"] = "error"

    # Redis AMS
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{ams_url}/v1/health",
                timeout=aiohttp.ClientTimeout(total=3),
            ) as r:
                services["redis_ams"] = "ok" if r.status < 500 else "error"
    except Exception:
        services["redis_ams"] = "error"

    # Revok
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"{revok_url}/v1/entities",
                auth=_REVOK_AUTH,
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
        {"status": overall, "framework": "agentframework", "services": services}
    )


@app.get("/stream/ask-agent")
async def stream_ask_agent(
    q: str = Query(default=_DEFAULT_QUESTION),
) -> StreamingResponse:
    """SSE endpoint — runs both agents concurrently and multiplexes their events."""

    async def generate():
        def evt(data: dict) -> str:
            return f"data: {json.dumps(data)}\n\n"

        if _without_revok is None or _with_revok is None:
            yield evt({"type": "error", "message": "agents not ready"})
            return

        queue: asyncio.Queue[dict] = asyncio.Queue()
        finished: dict[str, dict] = {}

        async def drain(
            agent: AgentFrameworkCustomerSuccessAgent, entity_key: str | None
        ) -> None:
            agent_id = "with_revok" if agent.use_revok else "without_revok"
            try:
                async with aiohttp.ClientSession() as sess:
                    async for event in agent.answer_entitlement_question_streaming(
                        sess, entity_key, question=q
                    ):
                        await queue.put(event)
            except Exception as exc:
                await queue.put(
                    {"type": "error", "agent": agent_id, "message": str(exc)}
                )
            finally:
                await queue.put({"type": "_done"})

        t1 = asyncio.create_task(drain(_without_revok, None))
        t2 = asyncio.create_task(drain(_with_revok, _ROOT_ENTITY_KEY))

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

        without = finished.get("without_revok", {})
        with_r = finished.get("with_revok", {})

        if without:
            if without.get("memory_quote"):
                _demo_state["memory_content"] = without["memory_quote"]
            _demo_state["answer_without_revok"] = {
                "answer": without.get("answer", ""),
                "memory_quote": without.get("memory_quote", ""),
                "re_verified": without.get("re_verified", False),
                "live_entitlements": without.get("live_entitlements"),
                "latency_ms": without.get("latency_ms"),
                "llm_tokens": without.get("llm_tokens"),
            }
            state_module.add_event(
                _demo_state,
                f"Without Revok answered: {without.get('answer', '')[:80]}",
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
                "live_entitlements": with_r.get("live_entitlements"),
            }
            state_module.add_event(
                _demo_state,
                f"With Revok answered ({with_mode}): {with_r.get('answer', '')[:80]}",
                kind="answer",
            )

        stats: dict = _demo_state.setdefault(
            "session_stats",
            {"questions": 0, "drift_caught": 0, "wrong_answers": 0, "support_escalations_prevented": 0},
        )
        stats["questions"] += 1
        if with_r.get("re_verified"):
            stats["drift_caught"] += 1
            stats["wrong_answers"] += 1
            stats["support_escalations_prevented"] += 1

        state_module.save(_demo_state)
        yield evt({"type": "done"})

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/v1/runs")
async def agent_run(body: RunRequest) -> StreamingResponse:
    """AG-UI protocol streaming endpoint for the WITH REVOK agent."""
    if _without_revok is None or _with_revok is None:
        return JSONResponse({"error": "agents not ready"}, status_code=503)

    question = _DEFAULT_QUESTION
    for m in reversed(body.messages):
        if m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, str) and content.strip():
                question = content.strip()
                break

    run_id = body.runId or _uuid.uuid4().hex
    thread_id = body.threadId or _with_revok.user_id

    async def generate():
        snapshot: dict = {}

        try:
            async for event in _with_revok.answer_entitlement_question_agui(
                _ROOT_ENTITY_KEY, question,
                run_id=run_id, thread_id=thread_id,
            ):
                if event["type"] == "STATE_SNAPSHOT":
                    snapshot = event.get("snapshot", {})
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:
            _log.exception("AG-UI run failed")
            yield f"data: {json.dumps({'type': 'RUN_ERROR', 'message': str(exc)})}\n\n"
            return

        ans_without = None
        try:
            async with aiohttp.ClientSession() as sess:
                ans_without = await _without_revok.answer_entitlement_question(
                    sess, None, question=question
                )
        except Exception as exc:
            _log.warning("without-revok agent failed: %s", exc)

        if ans_without:
            yield f"data: {json.dumps({'type': 'CUSTOM', 'name': 'without_revok_answer', 'value': {'answer': ans_without.answer, 'memory_quote': ans_without.memory_quote, 're_verified': ans_without.re_verified, 'live_entitlements': ans_without.live_entitlements}})}\n\n"

        if snapshot:
            live_ent = snapshot.get("live_entitlements")
            _demo_state["answer_with_revok"] = {
                "answer": snapshot.get("answer", ""),
                "memory_quote": snapshot.get("memory_quote", ""),
                "confidence_score": snapshot.get("confidence_score"),
                "confidence_status": snapshot.get("confidence_status", "unknown"),
                "signal_count": snapshot.get("signal_count", 0),
                "re_verified": snapshot.get("re_verified", False),
                "live_entitlements": live_ent,
            }
            with_mode = snapshot.get("confidence_status", "unknown")
            if snapshot.get("re_verified"):
                with_mode = f"{with_mode} → re-verified"
            state_module.add_event(
                _demo_state,
                f"[AG-UI] With Revok answered ({with_mode}): {snapshot.get('answer', '')[:80]}",
                kind="answer",
            )

        if ans_without:
            _demo_state["answer_without_revok"] = {
                "answer": ans_without.answer,
                "memory_quote": ans_without.memory_quote,
                "re_verified": ans_without.re_verified,
                "live_entitlements": ans_without.live_entitlements,
            }
            state_module.add_event(
                _demo_state,
                f"Without Revok answered: {ans_without.answer[:80]}",
                kind="answer",
            )

        # Update memory panel from with-revok AMS state (reflects post-correction reality)
        try:
            with_revok_mems = await _with_revok.check_memory_tool(None)
            if with_revok_mems:
                _demo_state["memory_content"] = with_revok_mems[0]["text"]
        except Exception as _exc:
            _log.warning("AG-UI: with-revok memory quote fetch failed: %s", _exc)

        stats: dict = _demo_state.setdefault(
            "session_stats",
            {"questions": 0, "drift_caught": 0, "wrong_answers": 0, "support_escalations_prevented": 0},
        )
        stats["questions"] += 1
        if snapshot.get("re_verified"):
            stats["drift_caught"] += 1
            stats["wrong_answers"] += 1
            stats["support_escalations_prevented"] += 1

        state_module.save(_demo_state)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _build_state_snapshot() -> dict[str, Any]:
    """Return the canonical demo-state snapshot used by ``/state`` and ``/stream``."""
    sub = await db_module.get_subscription()
    entity = await _fetch_revok_entity(_ROOT_ENTITY_KEY)
    revok_ok = await _revok_reachable()

    ams_url = os.getenv("AMS_URL", "http://localhost:8000")
    now = time.monotonic()
    if now >= _ams_health_cache["expires"]:
        ams_ok = False
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f"{ams_url}/v1/health",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as r:
                    ams_ok = r.status < 500
        except Exception:
            ams_ok = False
        _ams_health_cache["ok"] = ams_ok
        _ams_health_cache["expires"] = now + _AMS_HEALTH_TTL
    else:
        ams_ok = _ams_health_cache["ok"]

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

    if sub:
        _demo_state["db_subscription_tier"] = sub[db_module.COL_TIER]
        _demo_state["db_seat_limit"] = sub[db_module.COL_SEATS]
        _demo_state["db_feature_entitlements"] = sub[db_module.COL_FEATURES]
        _demo_state["db_api_rate_limit"] = sub[db_module.COL_API_RATE]
        _demo_state["db_billing_terms"] = sub[db_module.COL_BILLING]
        _demo_state["db_updated_at"] = sub[db_module.COL_UPDATED]
    _demo_state["confidence_score"] = score
    _demo_state["confidence_status"] = conf_status
    _demo_state["signal_count"] = sig_count

    # Fetch propagated scores for dependent entities
    revok_url = os.getenv("REVOK_URL", "http://localhost:7771")
    try:
        async with aiohttp.ClientSession() as sess:
            dep_scores = await _fetch_dependent_scores(revok_url, sess)
    except Exception:
        dep_scores = {k: None for k in _DEPENDENT_ENTITY_KEYS}

    # Mirror the root entity's "verified from DB" fresh-fallback: until a
    # dependent has a real Revok entity (i.e. a signal has propagated to it),
    # show it as fresh rather than gray/untracked whenever memory has been
    # loaded — consistent with how the root entity is displayed above.
    if _demo_state.get("memory_content"):
        dep_scores = {k: (v if v is not None else 1.0) for k, v in dep_scores.items()}
    _demo_state["dependent_scores"] = dep_scores

    return {
        **_demo_state,
        "memory_loading": _demo_state.get("memory_loading", False),
        "entity_key": _ROOT_ENTITY_KEY,
        "revok_reachable": revok_ok,
        "services": {"revok": revok_ok, "redis_ams": ams_ok},
        "dependent_scores": dep_scores,
    }


@app.get("/stream")
async def stream_state() -> StreamingResponse:
    """SSE endpoint emitting the full demo state once per second."""

    async def generate():
        last_payload: str | None = None
        try:
            while True:
                try:
                    snapshot = await _build_state_snapshot()
                    payload = json.dumps(snapshot, default=str)
                    if payload != last_payload:
                        yield f"data: {payload}\n\n"
                        last_payload = payload
                    else:
                        yield ": ping\n\n"
                except asyncio.CancelledError:
                    return
                except Exception as exc:
                    _log.warning("stream: snapshot error (continuing): %s", exc)
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
    """Clear Redis AMS memories, reset SQLite subscription to Enterprise, clear Revok entity state."""
    global _demo_state
    if _without_revok is None or _with_revok is None:
        return JSONResponse({"error": "agents not ready"}, status_code=503)

    revok_url = os.getenv("REVOK_URL", "http://localhost:7771")
    ams_url = os.getenv("AMS_URL", "http://localhost:8000")
    async with aiohttp.ClientSession() as session:
        await asyncio.gather(
            _without_revok.clear_memories(session),
            _with_revok.clear_memories(session),
        )
        # Reset all tracked entities in Revok
        for key in [_ROOT_ENTITY_KEY] + _DEPENDENT_ENTITY_KEYS:
            encoded = urllib.parse.quote(key, safe="")
            try:
                async with session.delete(
                    f"{revok_url}/v1/entities/{encoded}",
                    auth=_REVOK_AUTH,
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    _log.info("Revok entity reset %s: status=%s", key, resp.status)
            except Exception as exc:
                _log.warning("Could not reset Revok entity %s: %s", key, exc)

    # Poll AMS until memories are actually gone
    user_ids = [_without_revok.user_id, _with_revok.user_id]
    for _attempt in range(8):  # up to ~4 s
        await asyncio.sleep(0.5)
        still_has_memories = False
        try:
            async with aiohttp.ClientSession() as chk:
                for uid in user_ids:
                    async with chk.post(
                        f"{ams_url}/v1/long-term-memory/search",
                        json={"text": "subscription", "search_mode": "keyword",
                              "session_id": {"eq": uid}, "limit": 1},
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as resp:
                        if resp.status < 300:
                            data = await resp.json()
                            mems = data if isinstance(data, list) else data.get("memories", data.get("results", []))
                            if mems:
                                still_has_memories = True
                                break
        except Exception:
            pass
        if not still_has_memories:
            _log.info("AMS purge confirmed empty after %d poll(s)", _attempt + 1)
            break
    else:
        _log.warning("AMS purge not confirmed empty after 4 s — proceeding anyway")

    await db_module.reset_to_enterprise()
    _demo_state = state_module.reset_state()

    sub = await db_module.get_subscription()
    if sub:
        _demo_state["db_subscription_tier"] = sub[db_module.COL_TIER]
        _demo_state["db_seat_limit"] = sub[db_module.COL_SEATS]
        _demo_state["db_feature_entitlements"] = sub[db_module.COL_FEATURES]
        _demo_state["db_api_rate_limit"] = sub[db_module.COL_API_RATE]
        _demo_state["db_billing_terms"] = sub[db_module.COL_BILLING]
        _demo_state["db_updated_at"] = sub[db_module.COL_UPDATED]
    _demo_state["dependent_scores"] = {}

    state_module.add_event(_demo_state, "Demo reset to initial state", kind="info")
    state_module.save(_demo_state)

    return JSONResponse({"reset": True})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.getenv("DEMO_PORT", "8081"))
    uvicorn.run("server:app", host="0.0.0.0", port=port, reload=False)
