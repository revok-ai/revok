"""Microsoft Agent Framework sales agent for the Revok stale-memory pricing demo.

Uses the real Microsoft Agent Framework SDK (agent-framework package):
  - Agent  — AF's core agent class
  - @tool  — AF's function-tool decorator
  - OpenAIChatCompletionClient — Azure OpenAI Chat Completions backend

Two agents are created at startup via build_agents():

  WITHOUT REVOK  — only check_memory tool; answers from whatever is in memory.
  WITH REVOK     — check_memory + get_revok_confidence + get_current_price;
                   the LLM is instructed to check confidence and re-verify from
                   the database when stale.

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
# Runtime configuration — set from server.py lifespan before build_agents()
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
    product_name: Annotated[str, Field(description="The product name to search memory for.")],
) -> str:
    """Search long-term memory in Redis Agent Memory Server for pricing information about a product.

    Use this tool first before answering any pricing question.
    Returns stored memories as a JSON list. Each entry has a 'text' field with the memory content.
    If no memories are found, the list will be empty.
    """
    ams_url = _config["ams_url"]
    user_id = _config["without_revok_user_id"]
    return await _do_check_memory(ams_url, user_id, product_name)


@tool(approval_mode="never_require")
async def check_memory_with_revok(
    product_name: Annotated[str, Field(description="The product name to search memory for.")],
) -> str:
    """Search long-term memory in Redis Agent Memory Server for pricing information about a product.

    Use this tool first before answering any pricing question.
    Returns stored memories as a JSON list. Each entry has a 'text' field with the memory content.
    If no memories are found, the list will be empty.
    """
    ams_url = _config["ams_url"]
    user_id = _config["with_revok_user_id"]
    return await _do_check_memory(ams_url, user_id, product_name)


@tool(approval_mode="never_require")
async def get_revok_confidence(
    entity_key: Annotated[str, Field(description="The Revok entity key for the product (e.g. 'orion_cache', 'nova_gateway').")],
) -> str:
    """Check the Revok confidence score for a product entity.

    Revok tracks how many external change-data-capture (CDC) signals have been
    received since the last memory write. A high score means memory is fresh;
    a low score means an external system has updated the pricing data and the
    memory may be stale.

    Returns a JSON object with:
      - score: float 0.0–1.0 (null if entity not yet tracked)
      - status: 'fresh' (>=0.7), 'degraded' (>=0.3), 'stale' (<0.3), or 'unknown'
      - signal_count: number of CDC signals received

    Decision rule:
      - fresh    → answer from memory confidently
      - degraded → answer from memory but add a caveat about possible staleness
      - stale    → you MUST call get_current_price to re-verify before answering
      - unknown  → answer from memory (no signals tracked yet)
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
def get_current_price(
    product_name: Annotated[str, Field(description="The exact product name to look up in the database.")],
) -> str:
    """Fetch the current live price for a product from the SQLite database.

    Call this tool ONLY when Revok confidence is 'stale', to re-verify pricing
    before answering the customer. Do NOT call this if confidence is fresh or degraded.

    Returns a JSON object with:
      - product: product name
      - price: current price in USD per month (float), or null if not found
      - source: 'database'
    """
    db_path = _config.get("db_path", "")
    if not db_path:
        return json.dumps({"product": product_name, "price": None, "source": "database", "error": "db not configured"})
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.execute(
            "SELECT price FROM products WHERE name = ? LIMIT 1",
            (product_name,),
        )
        row = cur.fetchone()
        conn.close()
        price = float(row[0]) if row else None
        return json.dumps({"product": product_name, "price": price, "source": "database"})
    except Exception as exc:
        _log.error("get_current_price failed for %s: %s", product_name, exc)
        return json.dumps({"product": product_name, "price": None, "source": "database", "error": str(exc)})


# ---------------------------------------------------------------------------
# Shared HTTP helper
# ---------------------------------------------------------------------------

async def _do_check_memory(ams_url: str, user_id: str, product_name: str) -> str:
    # AMS SearchRequest: session_id is a filter OBJECT {"eq": "value"}, not a string.
    # Use keyword search to avoid needing an embedding model (avoids Azure embed 401).
    payload = {
        "text": product_name,
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
You are a pricing sales agent. You answer customer questions about product pricing.

When answering:
1. ALWAYS call check_memory_without_revok first to retrieve stored pricing information.
2. If the memory result has count 0 or an empty memories list, respond ONLY with:
   "No pricing data has been loaded yet. Please check back after data has been initialised."
   Do NOT make up a price. Do NOT call any other tool.
3. If memory contains pricing data, answer confidently from memory.
4. Be concise (2-3 sentences). Do not mention any memory system or technical details.
"""

_WITH_REVOK_INSTRUCTIONS = """\
You are a confidence-aware pricing sales agent backed by Revok — a system that \
tracks how fresh your memory is by monitoring external data-change signals.

When answering a pricing question, follow these steps IN ORDER:
1. Call check_memory_with_revok to retrieve stored pricing information.
2. IMPORTANT: If the memory result has count 0 or an empty memories list, respond ONLY with:
   "No pricing data has been loaded yet. Please check back after data has been initialised."
   Do NOT call get_revok_confidence. Do NOT call get_current_price. Do NOT make up a price.
3. Call get_revok_confidence with the entity key for the product to check memory freshness.
   Entity key rules: lowercase, underscores for spaces (e.g. "Orion Cache" → "orion_cache").
4. Based on the confidence status:
   - fresh    → Answer from memory confidently.
   - degraded → Answer from memory but tell the customer the price may have changed recently \
and they should confirm before finalising.
   - stale    → Call get_current_price to re-verify the live price from the database, \
then answer using the live price. Explain that you re-verified.
   - unknown  → Answer from memory confidently.
5. Be concise (2-3 sentences).
"""


# ---------------------------------------------------------------------------
# AgentAnswer dataclass — same public interface server.py expects
# ---------------------------------------------------------------------------

@dataclass
class AgentAnswer:
    answer: str
    memory_quote: str
    confidence_score: float | None
    confidence_status: str
    signal_count: int
    re_verified: bool = field(default=False)
    live_price: float | None = field(default=None)


# ---------------------------------------------------------------------------
# Thin wrapper that server.py can call with the same interface as before
# ---------------------------------------------------------------------------

class AgentFrameworkPricingSalesAgent:
    """Thin wrapper around an AF Agent preserving the server.py call interface."""

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

    async def answer_budget_question(
        self,
        session: Any,  # unused, kept for API compat
        product_name: str,
        entity_key: str | None,
        question: str = "What is the current price for Orion Cache per month?",
    ) -> AgentAnswer:
        """Run the AF agent and gather metadata concurrently; return a structured AgentAnswer."""
        prompt = f"Product: {product_name}\nQuestion: {question}"
        if self.use_revok and entity_key:
            prompt += f"\nRevok entity key: {entity_key}"

        async def _gather_metadata() -> tuple[str, float | None, str, int, float | None]:
            """Fetch memory quote, confidence, and live price directly for the state panel."""
            ams_url = _config["ams_url"]
            raw = await _do_check_memory(ams_url, self.user_id, product_name)
            mem_data = json.loads(raw)
            memories = mem_data.get("memories", [])
            memory_quote = memories[0]["text"] if memories else ""

            confidence_score: float | None = None
            confidence_status = "unknown"
            signal_count = 0
            live_price: float | None = None

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

                if confidence_status == "stale":
                    db_path = _config.get("db_path", "")
                    if db_path:
                        try:
                            conn = sqlite3.connect(db_path)
                            cur = conn.execute(
                                "SELECT price FROM products WHERE name = ? LIMIT 1",
                                (product_name,),
                            )
                            row = cur.fetchone()
                            conn.close()
                            live_price = float(row[0]) if row else None
                        except Exception as exc:
                            _log.warning("metadata: live price fetch failed: %s", exc)

            return memory_quote, confidence_score, confidence_status, signal_count, live_price

        # Run the LLM agent first — asyncio.gather() creates subtasks that
        # break the AF SDK's ContextVar telemetry (Token created in different Context).
        # Sequential execution avoids this.
        answer_result = await self._agent.run(prompt)
        answer_text = str(answer_result) if answer_result else "Unable to answer."
        memory_quote, confidence_score, confidence_status, signal_count, live_price = await _gather_metadata()

        return AgentAnswer(
            answer=answer_text,
            memory_quote=memory_quote,
            confidence_score=confidence_score,
            confidence_status=confidence_status,
            signal_count=signal_count,
            re_verified=live_price is not None,
            live_price=live_price,
        )

    async def check_memory_tool(
        self,
        session: Any,  # unused, kept for /memories endpoint compat
    ) -> list[dict]:
        """Return raw memories for this agent's user_id (for /memories endpoint)."""
        ams_url = _config["ams_url"]
        product_name = db_module_product_name()
        raw = await _do_check_memory(ams_url, self.user_id, product_name)
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
        search_terms = ["Customer", "price", "approved", "budget"]
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

    def answer_budget_question_streaming(
        self,
        session: Any,
        product_name: str,
        entity_key: str | None,
        question: str = "What is the current price for Orion Cache per month?",
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Return an async generator that yields SSE events from the AF agent."""
        return _stream_af_agent(self._agent, self.use_revok, product_name, entity_key, question)

    def answer_budget_question_agui(
        self,
        product_name: str,
        entity_key: str | None,
        question: str = "What is the current price for Orion Cache per month?",
        run_id: str | None = None,
        thread_id: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Return an async generator that yields AG-UI protocol events."""
        return _agui_stream_af_agent(
            self._agent, self.use_revok, product_name, entity_key, question,
            run_id=run_id, thread_id=thread_id,
        )


# ---------------------------------------------------------------------------
# Streaming helpers
# ---------------------------------------------------------------------------

async def _stream_af_agent(
    agent: Agent,
    use_revok: bool,
    product_name: str,
    entity_key: str | None,
    question: str,
) -> AsyncGenerator[dict[str, Any], None]:
    """Yield demo SSE events by running the AF agent and streaming its output."""
    agent_id = "with_revok" if use_revok else "without_revok"
    start = time.monotonic()
    prompt = f"Product: {product_name}\nQuestion: {question}"
    if use_revok and entity_key:
        prompt += f"\nRevok entity key: {entity_key}"

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
        "live_price": None,
        "confidence_status": "unknown",
        "confidence_score": None,
        "signal_count": 0,
        "answer": full_answer,
    }


async def _agui_stream_af_agent(
    agent: Agent,
    use_revok: bool,
    product_name: str,
    entity_key: str | None,
    question: str,
    run_id: str | None = None,
    thread_id: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Yield AG-UI protocol events from an AF agent run."""
    run_id = run_id or str(_uuid.uuid4())
    thread_id = thread_id or str(_uuid.uuid4())
    prompt = f"Product: {product_name}\nQuestion: {question}"
    if use_revok and entity_key:
        prompt += f"\nRevok entity key: {entity_key}"

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
# Factory — called from server.py lifespan
# ---------------------------------------------------------------------------

def build_agents() -> tuple["AgentFrameworkPricingSalesAgent", "AgentFrameworkPricingSalesAgent"]:
    """Create and return (without_revok_agent, with_revok_agent).

    Must be called after _config has been populated.
    """
    client = _build_client()

    without_revok_af = Agent(
        client=client,
        name="without-revok-pricing-agent",
        description="Pricing sales agent (no memory freshness checking)",
        instructions=_WITHOUT_REVOK_INSTRUCTIONS,
        tools=[check_memory_without_revok],
    )

    with_revok_af = Agent(
        client=client,
        name="with-revok-pricing-agent",
        description="Confidence-aware pricing sales agent backed by Revok",
        instructions=_WITH_REVOK_INSTRUCTIONS,
        tools=[check_memory_with_revok, get_revok_confidence, get_current_price],
    )

    without_revok = AgentFrameworkPricingSalesAgent(
        name="WITHOUT REVOK",
        user_id=_config["without_revok_user_id"],
        use_revok=False,
        agent=without_revok_af,
    )
    with_revok = AgentFrameworkPricingSalesAgent(
        name="WITH REVOK",
        user_id=_config["with_revok_user_id"],
        use_revok=True,
        agent=with_revok_af,
    )
    return without_revok, with_revok


def db_module_product_name() -> str:
    """Late-import helper to avoid circular dependency."""
    import database as db_module
    return db_module.PRODUCT_NAME
