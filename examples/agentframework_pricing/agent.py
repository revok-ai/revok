"""Microsoft Agent Framework customer success agent for the Revok entitlement demo.

Uses the real Microsoft Agent Framework SDK (agent-framework package):
  - Agent   -  AF's core agent class
  - @tool   -  AF's function-tool decorator
  - OpenAIChatCompletionClient  -  Azure OpenAI Chat Completions backend

Two agents are created at startup via build_agents():

  WITHOUT REVOK   -  only check_memory tool; answers from whatever is in memory.
  WITH REVOK      -  check_memory + get_revok_confidence + get_current_entitlements;
                   the LLM is instructed to check subscription-tier confidence
                   and re-verify ALL five entitlement facts when stale.

Module-level _config is populated by server.py's lifespan before build_agents()
is called, so the tool closures can read AMS / Revok URLs at runtime.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
import urllib.parse
import uuid as _uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Annotated, Any

import aiohttp
from agent_framework import Agent, tool
from agent_framework.openai import OpenAIChatCompletionClient
from pydantic import Field

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Runtime configuration  -  set from server.py lifespan before build_agents()
# ---------------------------------------------------------------------------

_config: dict[str, Any] = {
    "ams_url": "http://localhost:8000",
    "revok_url": "http://localhost:7771",
    "without_revok_user_id": "demo-without-revok",
    "with_revok_user_id": "demo-with-revok",
    "db_path": "",          # set by server.py after db init
}


# ---------------------------------------------------------------------------
# Score → status helper (exported; used by server.py)
# ---------------------------------------------------------------------------


def _score_to_status(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score >= 0.7:
        return "fresh"
    if score >= 0.3:
        return "degraded"
    return "stale"


# ---------------------------------------------------------------------------
# LLM client factory
# ---------------------------------------------------------------------------


def _build_client() -> OpenAIChatCompletionClient:
    """Return an OpenAIChatCompletionClient configured for Azure or plain OpenAI."""
    az_key = os.getenv("AZURE_OPENAI_API_KEY", "")
    az_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    if az_key and az_endpoint:
        return OpenAIChatCompletionClient(
            model=os.getenv("AZURE_OPENAI_LLM_DEPLOYMENT", "gpt-4o-mini"),
            azure_endpoint=az_endpoint.rstrip("/"),
            api_key=az_key,
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview"),
        )
    return OpenAIChatCompletionClient(
        model=os.getenv("OPENAI_LLM_MODEL", "gpt-4o-mini"),
        api_key=os.getenv("OPENAI_API_KEY", ""),
    )


# ---------------------------------------------------------------------------
# Tool definitions (module-level so AF can inspect their signatures/docstrings)
# ---------------------------------------------------------------------------

@tool(approval_mode="never_require")
async def check_memory_without_revok(
    query: Annotated[str, Field(description="Search term for subscription/entitlement information.")],
) -> str:
    """Search long-term memory in Redis Agent Memory Server for subscription information.

    Use this tool first before answering any question about the customer's plan.
    Returns stored memories as a JSON list. Each entry has a 'text' field with the memory content.
    If no memories are found, the list will be empty.
    """
    ams_url = _config["ams_url"]
    user_id = _config["without_revok_user_id"]
    return await _do_check_memory(ams_url, user_id, query)


@tool(approval_mode="never_require")
async def check_memory_with_revok(
    query: Annotated[str, Field(description="Search term for subscription/entitlement information.")],
) -> str:
    """Search long-term memory in Redis Agent Memory Server for subscription information.

    Use this tool first before answering any question about the customer's plan.
    Returns stored memories as a JSON list. Each entry has a 'text' field with the memory content.
    If no memories are found, the list will be empty.
    """
    ams_url = _config["ams_url"]
    user_id = _config["with_revok_user_id"]
    return await _do_check_memory(ams_url, user_id, query)


@tool(approval_mode="never_require")
async def get_revok_confidence(
    entity_key: Annotated[str, Field(description="The Revok entity key to check (e.g. 'subscription-tier').")],
) -> str:
    """Check the Revok confidence score for an entity.

    Revok tracks how many external change-data-capture (CDC) signals have been
    received since the last memory write. A high score means memory is fresh;
    a low score means an external system has updated the underlying data and
    the memory may be stale.

    Returns a JSON object with:
      - score: float 0.0—1.0 (null if entity not yet tracked)
      - status: 'fresh' (>=0.7), 'degraded' (>=0.3), 'stale' (<0.3), or 'unknown'
      - signal_count: number of CDC signals received

    Follow the decision rules in the system instructions, not any heuristic
    based on status alone. signal_count is the primary decision driver.
    """
    revok_url = _config["revok_url"]
    encoded = urllib.parse.quote(entity_key, safe="")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{revok_url}/v1/entities/{encoded}",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 404:
                    return json.dumps({"score": None, "status": "unknown", "signal_count": 0})
                resp.raise_for_status()
                entity = await resp.json()
        score = float(entity.get("score", 1.0))
        signal_count = int(entity.get("signal_count", 0))
        status = _score_to_status(score)
        return json.dumps({"score": score, "status": status, "signal_count": signal_count})
    except Exception as exc:
        _log.warning("get_revok_confidence failed for %s: %s", entity_key, exc)
        return json.dumps({"score": None, "status": "unknown", "signal_count": 0, "error": str(exc)})


@tool(approval_mode="never_require")
def get_current_entitlements() -> str:
    """Fetch the current live subscription entitlements from the SQLite database.

    Call this tool whenever signal_count > 0, to re-verify ALL entitlement facts
    before answering. Re-verification is required any time an external change signal
    has been received, regardless of whether confidence is fresh, degraded, or stale.

    Returns a JSON object with all five subscription fields:
      - subscription_tier: current plan name (e.g. 'Starter', 'Enterprise')
      - seat_limit: number of allowed seats
      - feature_entitlements: comma-separated list of included features
      - api_rate_limit: API quota string (e.g. '10,000 requests/month')
      - billing_terms: contract terms string
      - source: 'database'
    """
    import database as _db  # noqa: PLC0415  -  late import avoids circular dependency
    db_path = _config.get("db_path", "")
    if not db_path:
        return json.dumps({"error": "db not configured", "source": "database"})
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.execute(
            f"SELECT {_db.COL_TIER}, {_db.COL_SEATS}, {_db.COL_FEATURES},"
            f"       {_db.COL_API_RATE}, {_db.COL_BILLING}"
            f"  FROM {_db.TABLE}"
            f" WHERE {_db.COL_CUSTOMER_ID} = ? LIMIT 1",
            (_db.CUSTOMER_ID,),
        )
        row = cur.fetchone()
        conn.close()
        if row is None:
            return json.dumps({"error": "no subscription record found", "source": "database"})
        return json.dumps({
            "subscription_tier": row[0],
            "seat_limit": row[1],
            "feature_entitlements": row[2],
            "api_rate_limit": row[3],
            "billing_terms": row[4],
            "source": "database",
        })
    except Exception as exc:
        _log.error("get_current_entitlements failed: %s", exc)
        return json.dumps({"error": str(exc), "source": "database"})


@tool(approval_mode="never_require")
async def store_corrected_memory(
    subscription_tier: Annotated[str, Field(description="Current subscription tier name (e.g. 'Growth', 'Starter').")],
    seat_limit: Annotated[int, Field(description="Number of allowed seats.")],
    feature_entitlements: Annotated[str, Field(description="Comma-separated list of included features.")],
    api_rate_limit: Annotated[str, Field(description="API quota string (e.g. '10,000 requests/month').")],
    billing_terms: Annotated[str, Field(description="Contract billing terms string.")],
) -> str:
    """Write corrected subscription entitlements back to memory through the Revok proxy.

    Call this ONLY after get_current_entitlements has confirmed that the live database
    values differ from what is stored in memory. This closes the correction loop:
    it resets the Revok confidence score and writes the verified facts as a fresh
    belief, so future queries see accurate data.

    Pass the five values exactly as returned by get_current_entitlements.
    Returns a JSON object with 'stored': true on success.
    """
    revok_url = _config.get("revok_url", "")
    user_id = _config.get("with_revok_user_id", "")
    content = (
        f"Customer subscription tier: {subscription_tier}. "
        f"Seat limit: {seat_limit}. "
        f"Feature entitlements: {feature_entitlements}. "
        f"API rate limit: {api_rate_limit}. "
        f"Billing terms: {billing_terms}. "
        "Verified from database."
    )
    ams_url = _config.get("ams_url", "")
    try:
        async with aiohttp.ClientSession() as session:
            # Step 1: Delete all existing AMS memories for this user so the corrected
            # entry is the only one.  POST /v1/long-term-memory always INSERTs a new
            # record (there is no update-in-place); without this step the stale record
            # and the corrected record coexist and search results are non-deterministic.
            # These terms are guaranteed to match because "subscription" and "tier"
            # are hardcoded literals in the memory write template (see the content=
            # block just above, and _run_load_memory() in server.py which uses the
            # same template).  If that template wording changes, update these too.
            search_terms = ["subscription", "tier"]
            seen_ids: set[str] = set()
            for term in search_terms:
                try:
                    async with session.post(
                        f"{ams_url}/v1/long-term-memory/search",
                        json={
                            "text": term,
                            "search_mode": "keyword",
                            "session_id": {"eq": user_id},
                            "limit": 100,
                        },
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        if resp.status < 300:
                            data = await resp.json()
                            mems = data if isinstance(data, list) else data.get("memories", data.get("results", []))
                            for m in mems:
                                if m.get("id"):
                                    seen_ids.add(m["id"])
                except Exception:
                    pass
            for mem_id in seen_ids:
                try:
                    async with session.delete(
                        f"{ams_url}/v1/long-term-memory",
                        params=[("memory_ids", mem_id)],
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as resp:
                        pass  # best-effort
                except Exception:
                    pass
            if seen_ids:
                await asyncio.sleep(0.5)  # let AMS propagate deletes

            # Step 2: Delete the stale Revok entity so the write-back creates a fresh
            # record at score_cap - signal_strength instead of degrading further.
            try:
                async with session.delete(
                    f"{revok_url}/v1/entities/subscription-tier",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as del_resp:
                    _log.info(
                        "store_corrected_memory: entity delete status=%s", del_resp.status
                    )
            except Exception as exc:
                _log.warning("store_corrected_memory: entity delete failed (continuing): %s", exc)

            # Step 3: Write the single corrected memory through the Revok proxy so
            # enrich() creates a fresh entity record (score resets, signal_count=1).
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
            async with session.post(
                f"{revok_url}/v1/long-term-memory",
                json=payload,
                headers={"X-Revok-Entity": "subscription-tier"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
        _log.info(
            "store_corrected_memory: replaced %d stale memories, wrote corrected entry for %s",
            len(seen_ids), user_id,
        )
        return json.dumps({"stored": True, "content": content})
    except Exception as exc:
        _log.error("store_corrected_memory failed: %s", exc)
        return json.dumps({"stored": False, "error": str(exc)})


# ---------------------------------------------------------------------------
# Shared HTTP helper
# ---------------------------------------------------------------------------

async def _do_check_memory(ams_url: str, user_id: str, query: str) -> str:
    # AMS SearchRequest: session_id is a filter OBJECT {"eq": "value"}, not a string.
    # Use keyword search to avoid needing an embedding model (avoids Azure embed 401).
    payload = {
        "text": query,
        "search_mode": "keyword",
        "session_id": {"eq": user_id},
        "limit": 10,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{ams_url}/v1/long-term-memory/search",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
        results = data if isinstance(data, list) else data.get("results", data.get("memories", []))
        memories = [
            {
                "id": m.get("id", ""),
                "text": (m.get("text") or m.get("memory") or m.get("content") or ""),
                "created_at": m.get("created_at", ""),
            }
            for m in results
            if (m.get("text") or m.get("memory") or m.get("content"))
        ]
        return json.dumps({"count": len(memories), "memories": memories})
    except Exception as exc:
        _log.warning("check_memory failed: %s", exc)
        # Treat 500 (empty index) the same as empty results
        return json.dumps({"count": 0, "memories": [], "error": str(exc)})


# ---------------------------------------------------------------------------
# Agent instructions
# ---------------------------------------------------------------------------

_WITHOUT_REVOK_INSTRUCTIONS = """\
You are a customer success agent. You answer questions about a customer's \
current subscription plan and entitlements.

When answering:
1. ALWAYS call check_memory_without_revok first with the query "subscription" \
to retrieve stored subscription information.
2. If the memory result has count 0 or an empty memories list, respond ONLY with:
   "No subscription data has been loaded yet. Please load the customer profile first."
   Do NOT make up any values. Do NOT call any other tool.
3. If memory contains subscription data, answer confidently from memory listing \
all relevant entitlement details.
4. Be concise (3-5 sentences). Do not mention any memory system or technical details.
"""

_WITH_REVOK_INSTRUCTIONS = """\
You are a confidence-aware customer success agent backed by Revok  -  a system \
that tracks how fresh your memory is by monitoring external data-change signals.

When answering a question about a customer's subscription plan, follow these \
steps IN ORDER:
1. Call check_memory_with_revok with the query "subscription" to retrieve \
stored subscription information.
2. IMPORTANT: If the memory result has count 0 or an empty memories list, respond ONLY with:
   "No subscription data has been loaded yet. Please load the customer profile first."
   Do NOT call get_revok_confidence. Do NOT call get_current_entitlements. \
Do NOT make up any values.
3. Call get_revok_confidence with entity key "subscription-tier" to check memory \
freshness. The subscription tier is the root fact  -  if it changed externally, \
all other entitlements changed with it.
4. Based on signal_count and confidence status:
   - signal_count == 0 (any status) → No external change signal has ever been \
received. Answer from memory confidently listing all entitlement details.
   - signal_count > 0, confidence_status is 'stale' or 'degraded' → Memory \
may be outdated. ALWAYS call get_current_entitlements to re-verify ALL \
subscription facts from the live database. If the live values DIFFER from \
what is stored in memory, call store_corrected_memory with the live values \
to write the correction back before answering, then answer using the live \
values and note the subscription was changed. If the values MATCH memory, \
answer from memory confidently. List ALL five entitlement facts (tier, seats, \
features, API rate limit, billing terms).
   - signal_count > 0, confidence_status is 'fresh' → Memory has been verified \
recently. Answer from memory confidently listing all entitlement details. \
Do NOT call get_current_entitlements or store_corrected_memory.
5. Be concise but complete (3-5 sentences covering all relevant entitlements).
"""


# ---------------------------------------------------------------------------
# AgentAnswer dataclass  -  same public interface server.py expects
# ---------------------------------------------------------------------------

@dataclass
class AgentAnswer:
    answer: str
    memory_quote: str
    confidence_score: float | None
    confidence_status: str
    signal_count: int
    re_verified: bool = field(default=False)
    live_entitlements: dict[str, Any] | None = field(default=None)


# ---------------------------------------------------------------------------
# Thin wrapper that server.py can call
# ---------------------------------------------------------------------------

class AgentFrameworkCustomerSuccessAgent:
    """Thin wrapper around an AF Agent for the subscription entitlement demo."""

    def __init__(
        self,
        *,
        name: str,
        user_id: str,
        use_revok: bool,
        agent: Agent,
    ) -> None:
        self.name = name
        self.user_id = user_id
        self.use_revok = use_revok
        self._agent = agent

    async def answer_entitlement_question(
        self,
        session: Any,  # unused, kept for API compat
        entity_key: str | None,
        question: str = "What does this customer's current plan include?",
    ) -> AgentAnswer:
        """Run the AF agent and gather metadata; return a structured AgentAnswer."""
        prompt = f"Question: {question}"
        if self.use_revok and entity_key:
            prompt += f"\nRevok root entity key: {entity_key}"

        async def _gather_metadata() -> tuple[str, float | None, str, int, dict[str, Any] | None]:
            """Fetch memory quote, confidence, and live entitlements for the state panel."""
            ams_url = _config["ams_url"]
            raw = await _do_check_memory(ams_url, self.user_id, "subscription")
            mem_data = json.loads(raw)
            memories = mem_data.get("memories", [])
            memory_quote = memories[0]["text"] if memories else ""

            confidence_score: float | None = None
            confidence_status = "unknown"
            signal_count = 0
            live_entitlements: dict[str, Any] | None = None

            if self.use_revok and entity_key:
                revok_url = _config["revok_url"]
                encoded = urllib.parse.quote(entity_key, safe="")
                try:
                    async with aiohttp.ClientSession() as sess:
                        async with sess.get(
                            f"{revok_url}/v1/entities/{encoded}",
                            timeout=aiohttp.ClientTimeout(total=5),
                        ) as resp:
                            if resp.status == 200:
                                entity = await resp.json()
                                confidence_score = float(entity.get("score", 1.0))
                                signal_count = int(entity.get("signal_count", 0))
                                confidence_status = _score_to_status(confidence_score)
                except Exception as exc:
                    _log.warning("metadata: confidence fetch failed: %s", exc)

                if signal_count > 0:
                    import database as _db  # noqa: PLC0415
                    db_path = _config.get("db_path", "")
                    if db_path:
                        try:
                            conn = sqlite3.connect(db_path)
                            cur = conn.execute(
                                f"SELECT {_db.COL_TIER}, {_db.COL_SEATS}, {_db.COL_FEATURES},"
                                f"       {_db.COL_API_RATE}, {_db.COL_BILLING}"
                                f"  FROM {_db.TABLE}"
                                f" WHERE {_db.COL_CUSTOMER_ID} = ? LIMIT 1",
                                (_db.CUSTOMER_ID,),
                            )
                            row = cur.fetchone()
                            conn.close()
                            if row:
                                live_entitlements = {
                                    "subscription_tier": row[0],
                                    "seat_limit": row[1],
                                    "feature_entitlements": row[2],
                                    "api_rate_limit": row[3],
                                    "billing_terms": row[4],
                                }
                        except Exception as exc:
                            _log.warning("metadata: live entitlements fetch failed: %s", exc)

            return memory_quote, confidence_score, confidence_status, signal_count, live_entitlements

        # Run the LLM agent first  -  asyncio.gather() creates subtasks that
        # break the AF SDK's ContextVar telemetry (Token created in different Context).
        # Sequential execution avoids this.
        answer_result = await self._agent.run(prompt)
        answer_text = str(answer_result) if answer_result else "Unable to answer."
        memory_quote, confidence_score, confidence_status, signal_count, live_entitlements = (
            await _gather_metadata()
        )

        return AgentAnswer(
            answer=answer_text,
            memory_quote=memory_quote,
            confidence_score=confidence_score,
            confidence_status=confidence_status,
            signal_count=signal_count,
            re_verified=live_entitlements is not None,
            live_entitlements=live_entitlements,
        )

    async def check_memory_tool(
        self,
        session: Any,  # unused, kept for /memories endpoint compat
    ) -> list[dict]:
        """Return raw memories for this agent's user_id (for /memories endpoint)."""
        ams_url = _config["ams_url"]
        raw = await _do_check_memory(ams_url, self.user_id, "subscription")
        try:
            data = json.loads(raw)
            return data.get("memories", [])
        except Exception:
            return []

    async def clear_memories(
        self,
        session: Any,  # unused, kept for API compat
    ) -> None:
        """Delete all long-term memories for this agent's user_id.

        /forget policy is broken (always 0 deleted) and bulk DELETE is also
        broken.  Use search → delete-by-ID one at a time, with a fresh
        ClientSession per request to avoid ServerDisconnectedError.
        """
        ams_url = _config["ams_url"]
        # These terms are guaranteed to match because "subscription" and "tier" are
        # hardcoded literals in the memory write template used by both
        # _run_load_memory() in server.py and store_corrected_memory() in this file
        # ("Customer subscription tier: {tier}. ...").  If that template wording
        # changes, update these search terms to match.
        search_terms = ["subscription", "tier"]
        seen_ids: set[str] = set()
        try:
            for term in search_terms:
                async with aiohttp.ClientSession() as sess:
                    async with sess.post(
                        f"{ams_url}/v1/long-term-memory/search",
                        json={"text": term, "search_mode": "keyword",
                              "session_id": {"eq": self.user_id}, "limit": 100},
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
                    async with aiohttp.ClientSession() as sess:
                        async with sess.delete(
                            f"{ams_url}/v1/long-term-memory",
                            params=[("memory_ids", mem_id)],
                            timeout=aiohttp.ClientTimeout(total=10),
                        ) as resp:
                            if resp.status < 300:
                                deleted += 1
                except Exception:
                    pass
            _log.info("clear_memories: deleted %d memories for %s", deleted, self.user_id)
        except Exception as exc:
            _log.warning("clear_memories failed for %s: %s", self.user_id, exc)

    def answer_entitlement_question_streaming(
        self,
        session: Any,
        entity_key: str | None,
        question: str = "What does this customer's current plan include?",
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Return an async generator that yields SSE events from the AF agent."""
        return _stream_af_agent(self._agent, self.use_revok, entity_key, question)

    def answer_entitlement_question_agui(
        self,
        entity_key: str | None,
        question: str = "What does this customer's current plan include?",
        run_id: str | None = None,
        thread_id: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Return an async generator that yields AG-UI protocol events."""
        return _agui_stream_af_agent(
            self._agent, self.use_revok, entity_key, question,
            run_id=run_id, thread_id=thread_id,
        )


# ---------------------------------------------------------------------------
# Streaming helpers
# ---------------------------------------------------------------------------

async def _stream_af_agent(
    agent: Agent,
    use_revok: bool,
    entity_key: str | None,
    question: str,
) -> AsyncGenerator[dict[str, Any], None]:
    """Yield demo SSE events by running the AF agent and streaming its output."""
    agent_id = "with_revok" if use_revok else "without_revok"
    start = time.monotonic()
    prompt = f"Question: {question}"
    if use_revok and entity_key:
        prompt += f"\nRevok root entity key: {entity_key}"

    yield {"type": "agent_started", "agent": agent_id}

    full_answer = ""
    token_count = 0
    try:
        async for chunk in agent.run(prompt, stream=True):
            text = getattr(chunk, "text", None) or ""
            if text:
                token_count += 1
                full_answer += text
                yield {"type": "token", "agent": agent_id, "text": text}
    except Exception as exc:
        _log.error("AF agent streaming failed: %s", exc)
        yield {"type": "error", "agent": agent_id, "message": str(exc)}
        return

    latency_ms = int((time.monotonic() - start) * 1000)
    yield {
        "type": "agent_finished",
        "agent": agent_id,
        "latency_ms": latency_ms,
        "llm_tokens": token_count,
        "path": "memory",
        "memory_quote": "",
        "re_verified": False,
        "live_entitlements": None,
        "confidence_status": "unknown",
        "confidence_score": None,
        "signal_count": 0,
        "answer": full_answer,
    }


async def _agui_stream_af_agent(
    agent: Agent,
    use_revok: bool,
    entity_key: str | None,
    question: str,
    run_id: str | None = None,
    thread_id: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Yield AG-UI protocol events from an AF agent run."""
    run_id = run_id or str(_uuid.uuid4())
    thread_id = thread_id or str(_uuid.uuid4())
    prompt = f"Question: {question}"
    if use_revok and entity_key:
        prompt += f"\nRevok root entity key: {entity_key}"

    yield {"type": "RUN_STARTED", "runId": run_id, "threadId": thread_id}

    full_answer = ""
    msg_id = str(_uuid.uuid4())
    try:
        started_msg = False
        async for chunk in agent.run(prompt, stream=True):
            text = getattr(chunk, "text", None) or ""
            if text:
                if not started_msg:
                    yield {"type": "TEXT_MESSAGE_START", "messageId": msg_id, "role": "assistant"}
                    started_msg = True
                full_answer += text
                yield {"type": "TEXT_MESSAGE_CONTENT", "messageId": msg_id, "delta": text}

        if started_msg:
            yield {"type": "TEXT_MESSAGE_END", "messageId": msg_id}
    except Exception as exc:
        yield {"type": "RUN_ERROR", "runId": run_id, "message": str(exc)}
        return

    yield {
        "type": "STATE_SNAPSHOT",
        "snapshot": {
            "answer": full_answer,
            "confidence_status": "unknown",
        },
    }
    yield {"type": "RUN_FINISHED", "runId": run_id, "threadId": thread_id}


# ---------------------------------------------------------------------------
# Factory  -  called from server.py lifespan
# ---------------------------------------------------------------------------

def build_agents() -> tuple["AgentFrameworkCustomerSuccessAgent", "AgentFrameworkCustomerSuccessAgent"]:
    """Create and return (without_revok_agent, with_revok_agent).

    Must be called after _config has been populated.
    """
    client = _build_client()

    without_revok_af = Agent(
        client=client,
        name="without-revok-customer-success-agent",
        description="Customer success agent (no memory freshness checking)",
        instructions=_WITHOUT_REVOK_INSTRUCTIONS,
        tools=[check_memory_without_revok],
    )

    with_revok_af = Agent(
        client=client,
        name="with-revok-customer-success-agent",
        description="Confidence-aware customer success agent backed by Revok",
        instructions=_WITH_REVOK_INSTRUCTIONS,
        tools=[check_memory_with_revok, get_revok_confidence, get_current_entitlements, store_corrected_memory],
    )

    without_revok = AgentFrameworkCustomerSuccessAgent(
        name="WITHOUT REVOK",
        user_id=_config["without_revok_user_id"],
        use_revok=False,
        agent=without_revok_af,
    )
    with_revok = AgentFrameworkCustomerSuccessAgent(
        name="WITH REVOK",
        user_id=_config["with_revok_user_id"],
        use_revok=True,
        agent=with_revok_af,
    )
    return without_revok, with_revok


def db_module_customer_id() -> str:
    """Late-import helper to get the customer ID from the database module."""
    import database as _db  # noqa: PLC0415
    return _db.CUSTOMER_ID


