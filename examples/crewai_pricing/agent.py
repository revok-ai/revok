"""CrewAI sales agent for the Revok stale-memory pricing demo.

Uses a proper CrewAI Crew / Agent / Task stack with BaseTool subclasses
and LangChain LLMs (AzureChatOpenAI or ChatOpenAI).

Provider auto-detection at startup:
- Azure OpenAI when ``AZURE_OPENAI_API_KEY`` **and** ``AZURE_OPENAI_ENDPOINT`` are set.
- Plain OpenAI otherwise (``OPENAI_API_KEY`` must be set).

Two operating modes are supported:
- **WITHOUT REVOK** (``use_revok=False``): task tells the agent to answer
  from memory without any re-verification requirement.
- **WITH REVOK** (``use_revok=True``): Revok confidence is queried first;
  the Task description is built dynamically:
    fresh    → answer confidently from memory
    degraded → answer from memory but add a caveat
    stale    → MUST call ``get_current_price`` before answering

Public method signatures are backward-compatible with ``server.py``.
CrewAI handles all LLM calls; the ``session`` parameter is retained for
``check_memory_tool`` / ``clear_memories`` / ``store_pricing_belief``
which are called directly from ``server.py`` via aiohttp.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import aiohttp
from crewai import Agent, Crew, Process, Task
from crewai.tools import BaseTool
from langchain_core.callbacks.base import BaseCallbackHandler
from langchain_openai import AzureChatOpenAI, ChatOpenAI
from pydantic import BaseModel, Field, PrivateAttr

import database as db_module

_log = logging.getLogger(__name__)

_PRICE_RE = re.compile(r"\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)")


# ---------------------------------------------------------------------------
# LLM factory
# ---------------------------------------------------------------------------


def _build_llm(streaming: bool = False, callbacks: list | None = None) -> Any:
    """Return AzureChatOpenAI or ChatOpenAI based on environment variables."""
    az_key = os.getenv("AZURE_OPENAI_API_KEY", "")
    az_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    kwargs: dict[str, Any] = {
        "temperature": 0.3,
        "max_tokens": 200,
        "streaming": streaming,
    }
    if callbacks:
        kwargs["callbacks"] = callbacks
    if az_key and az_endpoint:
        return AzureChatOpenAI(
            azure_deployment=os.getenv("AZURE_OPENAI_LLM_DEPLOYMENT", "gpt-4o-mini"),
            azure_endpoint=az_endpoint.rstrip("/"),
            api_key=az_key,
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview"),
            **kwargs,
        )
    return ChatOpenAI(
        model=os.getenv("OPENAI_LLM_MODEL", "gpt-4o-mini"),
        api_key=os.getenv("OPENAI_API_KEY", ""),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class AgentAnswer:
    """Answer produced by the agent in one operating mode.

    Attributes:
        answer:           Human-readable answer text.
        memory_quote:     Raw text of the memory used to generate the answer.
        confidence_score: Revok confidence score at answer time (None if Revok unused).
        confidence_status: ``"fresh"``, ``"degraded"``, ``"stale"``, or ``"unknown"``.
        signal_count:     Number of signals Revok has received for this entity.
        re_verified:      ``True`` when the agent called ``get_current_price_tool``
                          to cross-check the memory.
        live_price:       Live DB price retrieved during re-verification (or ``None``).
    """

    answer: str
    memory_quote: str
    confidence_score: float | None
    confidence_status: str
    signal_count: int
    re_verified: bool = field(default=False)
    live_price: float | None = field(default=None)


# ---------------------------------------------------------------------------
# Input schemas for CrewAI tools
# ---------------------------------------------------------------------------


class GetCurrentPriceInput(BaseModel):
    product_name: str = Field(description="Exact product name to look up in the database")


class CheckMemoryInput(BaseModel):
    product_name: str = Field(description="Product name to search for in memory")


# ---------------------------------------------------------------------------
# CrewAI Tool: get_current_price
# ---------------------------------------------------------------------------


class GetCurrentPriceTool(BaseTool):
    """Reads live price from SQLite — used when memory confidence is stale."""

    name: str = "get_current_price"
    description: str = (
        "Get the current price of a product from the live pricing database. "
        "Use this when you need to verify or re-check a price."
    )
    args_schema: type[BaseModel] = GetCurrentPriceInput
    db_path: str = ""

    _call_log: list[str] = PrivateAttr(default_factory=list)
    _last_result: str = PrivateAttr(default="")

    def _run(self, product_name: str) -> str:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.execute(
                    f"SELECT {db_module.COL_NAME}, {db_module.COL_PRICE},"
                    f" {db_module.COL_UPDATED}"
                    f" FROM {db_module.TABLE} WHERE {db_module.COL_NAME} = ?",
                    (product_name,),
                )
                row = cur.fetchone()
            if row is None:
                result = f"Product '{product_name}' not found in the database."
            else:
                result = (
                    f"{row[db_module.COL_NAME]}: "
                    f"${float(row[db_module.COL_PRICE]):.2f}/month "
                    f"(last updated: {row[db_module.COL_UPDATED]})"
                )
        except Exception as exc:
            _log.error("GetCurrentPriceTool error: %s", exc)
            result = f"Error fetching price for '{product_name}': {exc}"
        self._call_log.append(product_name)
        self._last_result = result
        return result


# ---------------------------------------------------------------------------
# CrewAI Tool: check_memory
# ---------------------------------------------------------------------------


class CheckMemoryTool(BaseTool):
    """Queries Mem0 (through Revok proxy when configured) for stored memories."""

    name: str = "check_memory"
    description: str = (
        "Check what the agent remembers about a product from past conversations. "
        "Returns the memory content and its confidence score from Revok."
    )
    args_schema: type[BaseModel] = CheckMemoryInput
    memory_base_url: str = ""
    user_id: str = ""
    revok_base_url: str | None = None

    _call_log: list[str] = PrivateAttr(default_factory=list)
    _last_result: str = PrivateAttr(default="")

    def _run(self, product_name: str) -> str:
        try:
            params = urllib.parse.urlencode({"user_id": self.user_id})
            url = f"{self.memory_base_url}/memories?{params}"
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            memories: list[dict] = (
                data if isinstance(data, list) else data.get("results", [])
            )
        except Exception as exc:
            _log.error("CheckMemoryTool list_memories error: %s", exc)
            result = f"Could not retrieve memories: {exc}"
            self._call_log.append(product_name)
            self._last_result = result
            return result

        sorted_mems = sorted(
            memories,
            key=lambda m: m.get("updated_at") or m.get("created_at") or "",
            reverse=True,
        )
        keyword = product_name.lower()
        memory_text = ""
        for item in sorted_mems:
            text = _extract_text(item)
            if keyword in text.lower() and "$" in text:
                memory_text = text
                break
        if not memory_text:
            for item in sorted_mems:
                text = _extract_text(item)
                if keyword in text.lower():
                    memory_text = text
                    break

        if not memory_text:
            result = f"No memory found for '{product_name}'. Please load memory first."
            self._call_log.append(product_name)
            self._last_result = result
            return result

        # Extract Revok metadata written by the proxy (optional enrichment)
        revok_score: float | None = None
        revok_status: str | None = None
        for item in sorted_mems:
            text = _extract_text(item)
            if keyword in text.lower():
                meta = item.get("metadata") or {}
                x_revok = meta.get("x_revok") or {}
                if x_revok:
                    revok_score = x_revok.get("score")
                    revok_status = x_revok.get("status")
                break

        result = f"Memory for {product_name}: {memory_text}"
        if revok_score is not None:
            result += f" | Revok confidence: {revok_score:.2f} ({revok_status or 'unknown'})"
        self._call_log.append(product_name)
        self._last_result = result
        return result


# ---------------------------------------------------------------------------
# Main agent class
# ---------------------------------------------------------------------------


class PricingSalesAgent:
    """Sales agent backed by Mem0 memory; uses a proper CrewAI Crew for reasoning.

    Two instances are created in the demo:

    1. **Without Revok** — ``memory_base_url`` points directly at Mem0,
       ``use_revok=False``.  Task tells the agent to answer from memory.

    2. **With Revok** — ``memory_base_url`` points at the Revok proxy so
       that memory writes are enriched, ``use_revok=True``.  Task description
       is built dynamically based on Revok confidence:
       fresh → trust memory; stale → must call ``get_current_price``.
    """

    def __init__(
        self,
        *,
        name: str,
        user_id: str,
        memory_base_url: str,
        revok_base_url: str | None,
        use_revok: bool,
    ) -> None:
        self.name = name
        self.user_id = user_id
        self.memory_base_url = memory_base_url.rstrip("/")
        self.revok_base_url = revok_base_url.rstrip("/") if revok_base_url else None
        self.use_revok = use_revok

    # ------------------------------------------------------------------
    # Tool factories (fresh instances per crew run so _call_log is clean)
    # ------------------------------------------------------------------

    def _make_price_tool(self) -> GetCurrentPriceTool:
        return GetCurrentPriceTool(db_path=db_module.DB_PATH)

    def _make_memory_tool(self) -> CheckMemoryTool:
        return CheckMemoryTool(
            memory_base_url=self.memory_base_url,
            user_id=self.user_id,
            revok_base_url=self.revok_base_url,
        )

    # ------------------------------------------------------------------
    # Task description builder
    # ------------------------------------------------------------------

    def _build_task_description(
        self,
        product_name: str,
        status: str,
        score: float | None,
        signal_count: int,
        question: str = "",
    ) -> str:
        score_str = f"{score:.2f}" if score is not None else "N/A"
        q = question or f"What is the current price for {product_name} per month?"
        no_guess_rule = (
            "IMPORTANT: If check_memory returns 'No memory found', do NOT guess or "
            "invent a price. Tell the customer you have no pricing information yet "
            "and that they should try again after pricing data has been loaded. "
        )
        if not self.use_revok:
            return (
                f"Answer this question using your memory: Is {product_name} within "
                f"the customer budget? Use check_memory to recall what you know. "
                f"Answer confidently from what you remember only if memory exists. "
                f"{no_guess_rule}"
                f'Customer question: "{q}"'
            )
        if status == "stale":
            return (
                f"Answer this question: Is {product_name} within the customer budget? "
                f"Your memory confidence is {score_str} (STALE). "
                f"You must use get_current_price to get the live price before answering. "
                f"Do not use your memory for the price. "
                f"{no_guess_rule}"
                f'Customer question: "{q}"'
            )
        if status == "degraded":
            return (
                f"Answer this question: Is {product_name} within the customer budget? "
                f"Check your memory first. Your memory confidence is {score_str} "
                f"(DEGRADED) — add a caveat that verification is recommended. "
                f"{no_guess_rule}"
                f'Customer question: "{q}"'
            )
        return (
            f"Answer this question: Is {product_name} within the customer budget? "
            f"Check your memory first. Your memory confidence is {score_str} (FRESH) — "
            f"you can trust it. "
            f"{no_guess_rule}"
            f'Customer question: "{q}"'
        )

    # ------------------------------------------------------------------
    # Crew factory
    # ------------------------------------------------------------------

    def _build_crew(
        self,
        product_name: str,
        status: str,
        score: float | None,
        signal_count: int,
        *,
        price_tool: GetCurrentPriceTool,
        memory_tool: CheckMemoryTool,
        llm: Any = None,
        step_callback: Any = None,
        question: str = "",
    ) -> Crew:
        pricing_advisor = Agent(
            role="Pricing Sales Advisor",
            goal=(
                "Provide accurate pricing recommendations based on current market data "
                "and customer budget constraints"
            ),
            backstory=(
                "You are an experienced sales advisor who helps customers make informed "
                "purchasing decisions. You always verify information before making "
                "recommendations, especially when dealing with time-sensitive pricing data."
            ),
            tools=[price_tool, memory_tool],
            llm=llm or _build_llm(),
            verbose=False,
            allow_delegation=False,
        )
        budget_task = Task(
            description=self._build_task_description(
                product_name, status, score, signal_count, question=question
            ),
            expected_output=(
                "A clear pricing recommendation with confidence level and any caveats "
                "about data freshness"
            ),
            agent=pricing_advisor,
        )
        crew_kwargs: dict[str, Any] = {
            "agents": [pricing_advisor],
            "tasks": [budget_task],
            "process": Process.sequential,
            "verbose": False,
        }
        if step_callback is not None:
            crew_kwargs["step_callback"] = step_callback
        return Crew(**crew_kwargs)

    # ------------------------------------------------------------------
    # Revok confidence helper
    # ------------------------------------------------------------------

    async def _get_revok_confidence(
        self, entity_key: str
    ) -> tuple[float | None, int, str]:
        """Query Revok entity. Returns (score, signal_count, status)."""
        if not self.revok_base_url:
            return None, 0, "unknown"
        encoded = urllib.parse.quote(entity_key, safe="")
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.revok_base_url}/v1/entities/{encoded}",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 404:
                        return None, 0, "unknown"
                    resp.raise_for_status()
                    entity = await resp.json()
            score = float(entity["score"])
            signal_count = int(entity["signal_count"])
            status = _score_to_status(score)
            return score, signal_count, status
        except Exception as exc:
            _log.warning("[%s] Revok confidence check failed: %s", self.name, exc)
            return None, 0, "unknown"

    # ------------------------------------------------------------------
    # Public: batch answer  (backward-compatible signature)
    # ------------------------------------------------------------------

    async def answer_budget_question(
        self,
        session: aiohttp.ClientSession,
        product_name: str,
        entity_key: str | None,
        question: str = "What is the current price for Orion Cache per month?",
    ) -> AgentAnswer:
        """Run the CrewAI pricing crew and return a structured answer.

        ``session`` is retained for backward compatibility with ``server.py``;
        CrewAI tools make their own HTTP/SQLite calls internally.
        Revok confidence is queried via a fresh aiohttp session before the
        crew runs so the Task description can be confidence-aware.
        """
        score: float | None = None
        signal_count = 0
        status = "unknown"

        if self.use_revok and entity_key:
            score, signal_count, status = await self._get_revok_confidence(entity_key)

        price_tool = self._make_price_tool()
        memory_tool = self._make_memory_tool()
        crew = self._build_crew(
            product_name,
            status,
            score,
            signal_count,
            price_tool=price_tool,
            memory_tool=memory_tool,
            question=question,
        )

        result = await asyncio.to_thread(crew.kickoff)
        answer_text = result.raw if hasattr(result, "raw") else str(result)

        re_verified = bool(price_tool._call_log)
        memory_quote = memory_tool._last_result

        live_price: float | None = None
        if re_verified and price_tool._last_result:
            matches = _PRICE_RE.findall(price_tool._last_result)
            if matches:
                live_price = float(matches[-1].replace(",", ""))

        return AgentAnswer(
            answer=answer_text,
            memory_quote=memory_quote,
            confidence_score=score,
            confidence_status=status,
            signal_count=signal_count,
            re_verified=re_verified,
            live_price=live_price,
        )

    async def answer_budget_question_streaming(
        self,
        session: aiohttp.ClientSession,
        product_name: str,
        entity_key: str | None,
        question: str = "What is the current price for Orion Cache per month?",
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream typed event dicts as the CrewAI crew runs.

        Event types emitted (same structure as previous implementation):
        ``agent_started`` / ``confidence_checked`` / ``memory_loaded`` /
        ``db_reverified`` / ``token`` / ``agent_finished``.

        Token events arrive from the LangChain streaming callback while the
        crew's LLM is generating.  Tool-call events are emitted via
        step_callback during tool execution and, as a fallback, post-run
        from ``_call_log`` if the callback did not fire (CrewAI version
        differences).
        """
        start = time.monotonic()
        agent_id = "with_revok" if self.use_revok else "without_revok"

        yield {"type": "agent_started", "agent": agent_id}

        score: float | None = None
        signal_count = 0
        status = "unknown"

        if self.use_revok and entity_key:
            score, signal_count, status = await self._get_revok_confidence(entity_key)
            yield {
                "type": "confidence_checked",
                "agent": agent_id,
                "score": score,
                "status": status,
                "signal_count": signal_count,
            }

        # ── Inter-thread communication ────────────────────────────────
        queue: asyncio.Queue[dict[str, Any] | object] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        _SENTINEL = object()

        token_count = [0]
        emitted: dict[str, bool] = {}

        # ── LangChain streaming callback — fires in the worker thread ─
        class _TokenHandler(BaseCallbackHandler):
            def on_llm_new_token(self_h, token: str, **kwargs: Any) -> None:
                token_count[0] += 1
                asyncio.run_coroutine_threadsafe(
                    queue.put({"type": "token", "agent": agent_id, "text": token}),
                    loop,
                )

        # ── CrewAI step callback — fires after each tool returns ──────
        def _step_callback(step_output: Any) -> None:
            try:
                if isinstance(step_output, tuple) and len(step_output) == 2:
                    action, observation = step_output
                    tool_name: str = getattr(action, "tool", "") or ""
                    obs: str = str(observation) if observation else ""
                else:
                    tool_name = (
                        getattr(step_output, "tool", "")
                        or getattr(step_output, "name", "")
                        or ""
                    )
                    obs = (
                        getattr(step_output, "result", "")
                        or getattr(step_output, "output", "")
                        or ""
                    )
                if tool_name == "check_memory":
                    emitted["memory_loaded"] = True
                    asyncio.run_coroutine_threadsafe(
                        queue.put(
                            {
                                "type": "memory_loaded",
                                "agent": agent_id,
                                "memory_quote": obs,
                                "memory_count": 1,
                            }
                        ),
                        loop,
                    )
                elif tool_name == "get_current_price":
                    price_matches = _PRICE_RE.findall(obs)
                    live_p = (
                        float(price_matches[-1].replace(",", ""))
                        if price_matches
                        else None
                    )
                    emitted["db_reverified"] = True
                    asyncio.run_coroutine_threadsafe(
                        queue.put(
                            {
                                "type": "db_reverified",
                                "agent": agent_id,
                                "live_price": live_p,
                                "reason": status,
                            }
                        ),
                        loop,
                    )
            except Exception as exc:
                _log.debug("[%s] step_callback parse error: %s", self.name, exc)

        # ── Build and run the crew in a worker thread ─────────────────
        price_tool = self._make_price_tool()
        memory_tool = self._make_memory_tool()
        streaming_llm = _build_llm(streaming=True, callbacks=[_TokenHandler()])

        crew = self._build_crew(
            product_name,
            status,
            score,
            signal_count,
            price_tool=price_tool,
            memory_tool=memory_tool,
            llm=streaming_llm,
            step_callback=_step_callback,
            question=question,
        )

        def _sync_run() -> Any:
            res = crew.kickoff()
            asyncio.run_coroutine_threadsafe(queue.put(_SENTINEL), loop)
            return res

        crew_task: asyncio.Task[Any] = asyncio.create_task(
            asyncio.to_thread(_sync_run)
        )

        # ── Drain queue until sentinel ────────────────────────────────
        while True:
            item = await queue.get()
            if item is _SENTINEL:
                break
            yield item  # type: ignore[misc]

        result = await crew_task
        answer_text = result.raw if hasattr(result, "raw") else str(result)

        # ── Fallback: emit tool events if step_callback did not fire ──
        if not emitted.get("memory_loaded") and memory_tool._call_log:
            yield {
                "type": "memory_loaded",
                "agent": agent_id,
                "memory_quote": memory_tool._last_result,
                "memory_count": 1,
            }

        re_verified = bool(price_tool._call_log)
        live_price: float | None = None
        if re_verified and price_tool._last_result:
            matches_live = _PRICE_RE.findall(price_tool._last_result)
            if matches_live:
                live_price = float(matches_live[-1].replace(",", ""))

        if not emitted.get("db_reverified") and re_verified:
            yield {
                "type": "db_reverified",
                "agent": agent_id,
                "live_price": live_price,
                "reason": status,
            }

        # ── Fallback token emission if LangChain streaming did not work
        if token_count[0] == 0:
            for ch in answer_text:
                yield {"type": "token", "agent": agent_id, "text": ch}
            token_count[0] = len(answer_text)

        memory_quote = memory_tool._last_result
        latency_ms = int((time.monotonic() - start) * 1000)
        yield {
            "type": "agent_finished",
            "agent": agent_id,
            "latency_ms": latency_ms,
            "llm_tokens": token_count[0],
            "path": "memory\u2192reverify" if re_verified else "memory",
            "memory_quote": memory_quote,
            "re_verified": re_verified,
            "live_price": live_price,
            "confidence_status": status,
            "confidence_score": score,
            "signal_count": signal_count,
            "answer": answer_text,
        }

    # ------------------------------------------------------------------
    # Compatibility methods called directly by server.py
    # ------------------------------------------------------------------

    async def check_memory_tool(
        self, session: aiohttp.ClientSession
    ) -> list[dict[str, Any]]:
        """List raw memories for this agent's ``user_id``. Used by ``/memories``."""
        return await self._list_memories(session)

    async def store_pricing_belief(
        self,
        session: aiohttp.ClientSession,
        product_name: str,
        price: float,
        entity_key: str | None = None,
    ) -> dict[str, Any]:
        """Store a pricing memory via Mem0/Revok. Used by ``_heal_pricing_memory``."""
        content = (
            f"Customer budget approved {product_name} at ${price:.0f}/month. "
            "Verified pricing from database."
        )
        _log.info("[%s] Storing belief: %s", self.name, content)
        return await self._add_memory(session, content, entity_key=entity_key)

    # ------------------------------------------------------------------
    # Memory clearing (used by reset)
    # ------------------------------------------------------------------

    async def clear_memories(self, session: aiohttp.ClientSession) -> None:
        """Delete all memories for this agent's ``user_id``.

        Args:
            session: Active aiohttp client session.
        """
        memories = await self._list_memories(session)
        for item in memories:
            mem_id = item.get("id")
            if not mem_id:
                continue
            try:
                async with session.delete(
                    f"{self.memory_base_url}/memories/{mem_id}",
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    _log.debug(
                        "[%s] Deleted memory %s → %s", self.name, mem_id, resp.status
                    )
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "[%s] Could not delete memory %s: %s", self.name, mem_id, exc
                )

    # ------------------------------------------------------------------
    # Internal Mem0 API helpers
    # ------------------------------------------------------------------

    async def _add_memory(
        self,
        session: aiohttp.ClientSession,
        content: str,
        role: str = "user",
        entity_key: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "messages": [{"role": role, "content": content}],
            "user_id": self.user_id,
        }
        extra_headers: dict[str, str] = {}
        if entity_key:
            extra_headers["X-Revok-Entity"] = entity_key
        async with session.post(
            f"{self.memory_base_url}/memories",
            json=payload,
            headers=extra_headers if extra_headers else None,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _list_memories(
        self, session: aiohttp.ClientSession
    ) -> list[dict[str, Any]]:
        try:
            async with session.get(
                f"{self.memory_base_url}/memories",
                params={"user_id": self.user_id},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except Exception:
            return []
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("results"), list):
            return data["results"]
        return []

    async def _get_revok_entity(
        self,
        session: aiohttp.ClientSession,
        entity_key: str,
    ) -> dict[str, Any] | None:
        """Query Revok for the current confidence record of *entity_key*.

        Args:
            session:    Active aiohttp client session.
            entity_key: Normalised entity key (lowercase, stripped).

        Returns:
            Entity dict from Revok, or ``None`` on 404 or error.
        """
        if not self.revok_base_url:
            return None
        encoded = urllib.parse.quote(entity_key, safe="")
        try:
            async with session.get(
                f"{self.revok_base_url}/v1/entities/{encoded}",
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 404:
                    return None
                resp.raise_for_status()
                return await resp.json()
        except Exception as exc:  # noqa: BLE001
            _log.warning("[%s] Revok entity query failed: %s", self.name, exc)
            return None

    # ------------------------------------------------------------------
    # Private text helpers
    # ------------------------------------------------------------------

    def _find_pricing_memory(
        self,
        memories: list[dict[str, Any]],
        product_name: str,
    ) -> str:
        """Return the first memory mentioning *product_name* and a price.

        Falls back to any memory mentioning the product if none contain a price.

        Args:
            memories:     List of memory dicts from Mem0.
            product_name: Product name to search for (case-insensitive).

        Returns:
            Plain-text memory string, or ``""`` if none found.
        """
        keyword = product_name.lower()
        # Sort newest-first so a CDC-updated memory beats the original stale one.
        # Mem0 may ADD a new entry rather than overwrite; we always want the latest.
        sorted_mems = sorted(
            memories,
            key=lambda m: m.get("updated_at") or m.get("created_at") or "",
            reverse=True,
        )
        for item in sorted_mems:
            text = _extract_text(item)
            if keyword in text.lower() and "$" in text:
                return text
        for item in sorted_mems:
            text = _extract_text(item)
            if keyword in text.lower():
                return text
        return ""

    def _extract_price(self, text: str) -> str | None:
        """Extract the last dollar-amount from *text*, formatted as ``$N/month``.

        The last match is used so that Mem0 merge-text like
        "updated from $500/month to $801/month" returns the newest price.

        Args:
            text: Raw text to search.

        Returns:
            Formatted price string, or ``None`` if no price found.
        """
        matches = _PRICE_RE.findall(text)
        if not matches:
            return None
        return f"${matches[-1]}/month"


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _extract_text(item: dict[str, Any]) -> str:
    """Extract plain text from a Mem0 memory dict.

    Args:
        item: A single memory dict from Mem0.

    Returns:
        Best available text string.
    """
    for key in ("memory", "content", "text"):
        val = item.get(key)
        if isinstance(val, str) and val.strip():
            return val
    messages = item.get("messages")
    if isinstance(messages, list):
        parts = [
            m["content"]
            for m in messages
            if isinstance(m, dict) and isinstance(m.get("content"), str)
        ]
        if parts:
            return " ".join(parts)
    return str(item)


def _score_to_status(score: float | None) -> str:
    """Map a numeric confidence score to a human-readable status label.

    Args:
        score: Revok confidence score in [0, score_cap], or ``None``.

    Returns:
        One of ``"fresh"``, ``"degraded"``, ``"stale"``, or ``"unknown"``.
    """
    if score is None:
        return "fresh"  # no CDC signals yet — memory is fully trusted
    if score > 0.7:
        return "fresh"
    if score >= 0.3:
        return "degraded"
    return "stale"
