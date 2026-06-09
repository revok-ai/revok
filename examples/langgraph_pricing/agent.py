"""LangGraph sales agent for the Revok stale-memory pricing demo.

Uses a proper LangGraph StateGraph with explicit state, conditional routing,
and LangChain LLMs (AzureChatOpenAI or ChatOpenAI).

Provider auto-detection at startup:
- Azure OpenAI when ``AZURE_OPENAI_API_KEY`` **and** ``AZURE_OPENAI_ENDPOINT`` are set.
- Plain OpenAI otherwise (``OPENAI_API_KEY`` must be set).

Graph topology:
    START
      │
      ▼
  retrieve_memory        ← queries Mem0 + Revok confidence
      │
      ├─── stale ──────► verify_price ──► generate_answer
      │                                        ▲
      └─── all others ─────────────────────────┘
                                               │
                                              END

Two operating modes:
- **WITHOUT REVOK** (``use_revok=False``): retrieve_memory always routes
  to generate_answer; LLM answers from memory without caveats.
- **WITH REVOK** (``use_revok=True``): Revok confidence is queried in
  retrieve_memory; routing is confidence-aware:
    stale           → verify_price → generate_answer (use live price)
    degraded        → generate_answer (add caveat)
    fresh / unknown → generate_answer (trust memory)

Public method signatures are backward-compatible with ``server.py``.
The ``session`` parameter is retained on public methods for compatibility;
Mem0 and Revok HTTP calls inside the graph create their own aiohttp sessions.
"""

from __future__ import annotations

import logging
import os
import re
import time
import urllib.parse
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any, TypedDict

import aiohttp
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import AzureChatOpenAI, ChatOpenAI
from langgraph.graph import END, START, StateGraph

import database as db_module

_log = logging.getLogger(__name__)

_PRICE_RE = re.compile(r"\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)")


# ---------------------------------------------------------------------------
# LLM factory
# ---------------------------------------------------------------------------


def _build_llm() -> Any:
    """Return AzureChatOpenAI or ChatOpenAI based on environment variables.

    Always created with ``streaming=True`` so that LangGraph's
    ``astream_events`` can fire ``on_chat_model_stream`` events for each
    token while the generate_answer node runs.
    """
    az_key = os.getenv("AZURE_OPENAI_API_KEY", "")
    az_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    kwargs: dict[str, Any] = {
        "temperature": 0.3,
        "max_tokens": 200,
        "streaming": True,
    }
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
# LangGraph state
# ---------------------------------------------------------------------------


class AgentState(TypedDict):
    """Shared mutable state flowing through the LangGraph nodes."""

    confidence_score: float | None
    confidence_status: str
    signal_count: int
    memory_content: str | None
    live_price: float | None
    use_revok: bool
    product_name: str
    entity_key: str | None
    question: str
    final_answer: str | None


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
        re_verified:      ``True`` when verify_price_node ran and fetched a live price.
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
# Prompt builder (module-level, used by generate_answer_node)
# ---------------------------------------------------------------------------


def _build_prompt(state: AgentState) -> tuple[str, str]:
    """Return (system, user) prompt strings based on current graph state.

    Args:
        state: Current AgentState after retrieve_memory and (optionally)
               verify_price have run.

    Returns:
        Tuple of (system_prompt, user_prompt).
    """
    product = state["product_name"]
    question = (
        state.get("question") or f"What is the current price for {product} per month?"
    )
    raw_memory = state.get("memory_content") or ""
    has_memory = bool(raw_memory.strip())
    memory = raw_memory if has_memory else "No memory available."
    status = state.get("confidence_status", "unknown")
    live_price = state.get("live_price")
    score = state.get("confidence_score")
    score_str = f"{score:.2f}" if score is not None else "N/A"
    use_revok = state.get("use_revok", False)

    # No memory yet — refuse to guess regardless of other flags
    if not has_memory:
        system = (
            "You are a sales agent. You have no stored memory about this product's "
            "pricing yet. Do NOT guess or invent prices under any circumstances. "
            "Inform the customer politely that pricing data hasn't been loaded yet."
        )
        user = (
            f"Product: {product}\n"
            f"Memory: none\n"
            f'Customer question: "{question}"\n'
            "Tell the customer you have no pricing information for this product yet "
            "and that they should try again after pricing data has been loaded."
        )
        return system, user

    if not use_revok:
        system = (
            "You are a sales agent. Answer the customer's question using only "
            "the information stored in your memory. You have no way to verify "
            "if your memory is current. Be concise (2-3 sentences) and confident."
        )
        user = (
            f"Product: {product}\n"
            f"Memory: {memory}\n"
            f'Customer question: "{question}"\n'
            "Answer from memory."
        )
        return system, user

    if live_price is not None:
        # Stale path: memory was stale, re-verified from live DB
        system = (
            "You are a confidence-aware sales agent. Your memory layer tracks how "
            "fresh your stored knowledge is. When memory confidence is too low, you "
            "always re-verify from the live database before answering. "
            "Be concise (2-3 sentences). Mention that you re-verified and use the "
            "live price in your answer."
        )
        user = (
            f"Product: {product}\n"
            f"Memory (STALE, score={score_str}): {memory}\n"
            f"Live database price (re-verified): ${live_price:.0f}/month\n"
            f'Customer question: "{question}"\n'
            "Answer using the live price. Explain that you re-verified."
        )
        return system, user

    if status == "degraded":
        system = (
            "You are a confidence-aware sales agent. Your memory confidence is "
            "degraded — answer from memory but explicitly tell the customer the "
            "information may need verification before acting on it. "
            "Be concise (2-3 sentences)."
        )
        user = (
            f"Product: {product}\n"
            f"Memory (DEGRADED, score={score_str}): {memory}\n"
            f'Customer question: "{question}"\n'
            "Answer from memory but include a caveat that verification is recommended."
        )
        return system, user

    if status == "unknown":
        # No Revok entity yet — treat memory as fully trusted, no score label.
        system = (
            "You are a confident sales agent. Answer the customer's question using "
            "the information stored in your memory. Be concise (2-3 sentences)."
        )
        user = (
            f"Product: {product}\n"
            f"Memory: {memory}\n"
            f'Customer question: "{question}"\n'
            "Answer confidently from memory."
        )
        return system, user

    # fresh — trust memory
    system = (
        "You are a confident sales agent. Your memory confidence is high — answer "
        "confidently from memory. Be concise (2-3 sentences)."
    )
    user = (
        f"Product: {product}\n"
        f"Memory (FRESH, score={score_str}): {memory}\n"
        f'Customer question: "{question}"\n'
        "Answer confidently from memory."
    )
    return system, user


# ---------------------------------------------------------------------------
# Main agent class
# ---------------------------------------------------------------------------


class LangGraphPricingSalesAgent:
    """Sales agent backed by Mem0 memory; uses a LangGraph StateGraph for reasoning.

    Two instances are created in the demo:

    1. **Without Revok** — ``memory_base_url`` points directly at Mem0,
       ``use_revok=False``.  ``retrieve_memory`` always routes to
       ``generate_answer``; the LLM answers from memory without caveats.

    2. **With Revok** — ``memory_base_url`` points at the Revok proxy,
       ``use_revok=True``.  ``retrieve_memory`` queries Revok confidence first;
       routing is confidence-aware:
       stale           → verify_price → generate_answer (use live price)
       degraded        → generate_answer (add caveat)
       fresh / unknown → generate_answer (trust memory)
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
        self._llm = _build_llm()
        self._graph = self._build_graph()

    # ------------------------------------------------------------------
    # Graph builder
    # ------------------------------------------------------------------

    def _build_graph(self) -> Any:
        """Compile and return the LangGraph StateGraph.

        Nodes are async closures over ``self`` so they can access instance
        configuration (URLs, user_id, use_revok) at runtime.
        """

        async def retrieve_memory_node(state: AgentState) -> dict[str, Any]:
            """Query Mem0 for memories and (optionally) Revok for confidence score."""
            confidence_score: float | None = None
            confidence_status = "unknown"
            signal_count = 0

            if self.use_revok and state.get("entity_key"):
                (
                    confidence_score,
                    signal_count,
                    confidence_status,
                ) = await self._get_revok_confidence(state["entity_key"])

            memory_content: str | None = None
            async with aiohttp.ClientSession() as sess:
                memories = await self._list_memories(sess)
            memory_content = (
                self._find_pricing_memory(memories, state["product_name"]) or None
            )

            return {
                "confidence_score": confidence_score,
                "confidence_status": confidence_status,
                "signal_count": signal_count,
                "memory_content": memory_content,
            }

        def _routing(state: AgentState) -> str:
            """Determine next node: verify_price or generate_answer.

            Only stale memory requires live re-verification.
            Degraded memory routes directly to generate_answer where the
            prompt adds a caveat — no DB lookup needed.
            """
            if not state.get("use_revok", False):
                return "generate_answer"
            if state.get("confidence_status") == "stale":
                return "verify_price"
            return "generate_answer"

        async def verify_price_node(state: AgentState) -> dict[str, Any]:
            """Fetch the live price from SQLite for stale/degraded memory paths."""
            live_price: float | None = None
            try:
                row = await db_module.get_price(state["product_name"])
                if row:
                    live_price = float(row[db_module.COL_PRICE])
            except Exception as exc:
                _log.error("[%s] verify_price_node error: %s", self.name, exc)
            return {"live_price": live_price}

        async def generate_answer_node(state: AgentState) -> dict[str, Any]:
            """Call the LLM to generate a natural-language pricing answer."""
            system_prompt, user_prompt = _build_prompt(state)
            response = await self._llm.ainvoke(
                [
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_prompt),
                ]
            )
            return {"final_answer": response.content}

        builder: StateGraph = StateGraph(AgentState)
        builder.add_node("retrieve_memory", retrieve_memory_node)
        builder.add_node("verify_price", verify_price_node)
        builder.add_node("generate_answer", generate_answer_node)

        builder.add_edge(START, "retrieve_memory")
        builder.add_conditional_edges(
            "retrieve_memory",
            _routing,
            {"verify_price": "verify_price", "generate_answer": "generate_answer"},
        )
        builder.add_edge("verify_price", "generate_answer")
        builder.add_edge("generate_answer", END)

        return builder.compile()

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
        """Run the LangGraph pricing graph and return a structured answer.

        ``session`` is retained for backward compatibility with ``server.py``;
        the graph creates its own aiohttp sessions for Mem0 / Revok internally.

        Args:
            session:      Unused; retained for API compatibility with server.py.
            product_name: Name of the product to answer about.
            entity_key:   Revok entity key (used when ``use_revok=True``).
            question:     Customer question text.

        Returns:
            :class:`AgentAnswer` with answer text, memory quote, and confidence
            metadata.
        """
        initial_state: AgentState = {
            "confidence_score": None,
            "confidence_status": "unknown",
            "signal_count": 0,
            "memory_content": None,
            "live_price": None,
            "use_revok": self.use_revok,
            "product_name": product_name,
            "entity_key": entity_key,
            "question": question,
            "final_answer": None,
        }
        result: dict[str, Any] = await self._graph.ainvoke(initial_state)
        re_verified = result.get("live_price") is not None
        return AgentAnswer(
            answer=result.get("final_answer") or "",
            memory_quote=result.get("memory_content") or "",
            confidence_score=result.get("confidence_score"),
            confidence_status=result.get("confidence_status", "unknown"),
            signal_count=result.get("signal_count", 0),
            re_verified=re_verified,
            live_price=result.get("live_price"),
        )

    # ------------------------------------------------------------------
    # Public: streaming answer  (backward-compatible signature)
    # ------------------------------------------------------------------

    async def answer_budget_question_streaming(
        self,
        session: aiohttp.ClientSession,
        product_name: str,
        entity_key: str | None,
        question: str = "What is the current price for Orion Cache per month?",
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream typed event dicts as the LangGraph graph runs.

        Uses ``StateGraph.astream_events(version="v2")`` to surface node
        completion events and LLM token events without running the graph twice.

        Event types emitted (same structure as the CrewAI demo for drop-in
        compatibility with ``server.py``):
        ``agent_started`` / ``confidence_checked`` / ``memory_loaded`` /
        ``db_reverified`` / ``token`` / ``agent_finished``.

        ``session`` is retained for backward compatibility; the graph creates
        its own sessions internally.

        Args:
            session:      Unused; retained for API compatibility with server.py.
            product_name: Name of the product to answer about.
            entity_key:   Revok entity key (used when ``use_revok=True``).
            question:     Customer question text.

        Yields:
            Typed event dicts consumed by ``server.py``'s SSE generator.
        """
        agent_id = "with_revok" if self.use_revok else "without_revok"
        start = time.monotonic()

        yield {"type": "agent_started", "agent": agent_id}

        initial_state: AgentState = {
            "confidence_score": None,
            "confidence_status": "unknown",
            "signal_count": 0,
            "memory_content": None,
            "live_price": None,
            "use_revok": self.use_revok,
            "product_name": product_name,
            "entity_key": entity_key,
            "question": question,
            "final_answer": None,
        }

        captured: dict[str, Any] = {}
        token_count = 0
        confidence_emitted = False
        memory_emitted = False
        verify_emitted = False

        async for event in self._graph.astream_events(initial_state, version="v2"):
            ev_type: str = event.get("event", "")
            metadata: dict[str, Any] = event.get("metadata", {})
            node: str = metadata.get("langgraph_node", "")

            # ── Node completions ───────────────────────────────────────
            if ev_type == "on_chain_end":
                output = event.get("data", {}).get("output")
                if not isinstance(output, dict):
                    continue

                if node == "retrieve_memory":
                    captured.update(output)
                    score = output.get("confidence_score")
                    status = output.get("confidence_status", "unknown")
                    sig = output.get("signal_count", 0)
                    mem = output.get("memory_content")

                    if self.use_revok and not confidence_emitted:
                        confidence_emitted = True
                        yield {
                            "type": "confidence_checked",
                            "agent": agent_id,
                            "score": score,
                            "status": status,
                            "signal_count": sig,
                        }
                    if mem and not memory_emitted:
                        memory_emitted = True
                        yield {
                            "type": "memory_loaded",
                            "agent": agent_id,
                            "memory_quote": mem,
                            "memory_count": 1,
                        }

                elif node == "verify_price":
                    captured.update(output)
                    live_p = output.get("live_price")
                    if not verify_emitted:
                        verify_emitted = True
                        yield {
                            "type": "db_reverified",
                            "agent": agent_id,
                            "live_price": live_p,
                            "reason": captured.get("confidence_status", "stale"),
                        }

                elif node == "generate_answer":
                    # Store final_answer but skip the messages delta (list of AIMessage)
                    captured.update(
                        {k: v for k, v in output.items() if k != "messages"}
                    )

            # ── LLM token streaming ────────────────────────────────────
            elif ev_type == "on_chat_model_stream":
                chunk = event.get("data", {}).get("chunk")
                if chunk is not None:
                    text: str = getattr(chunk, "content", None) or ""
                    if text:
                        token_count += 1
                        yield {"type": "token", "agent": agent_id, "text": text}

        # ── Fallback: emit tokens char-by-char if streaming did not fire ──
        final_answer: str = captured.get("final_answer", "")
        if token_count == 0 and final_answer:
            for ch in final_answer:
                token_count += 1
                yield {"type": "token", "agent": agent_id, "text": ch}

        live_price: float | None = captured.get("live_price")
        re_verified = live_price is not None
        latency_ms = int((time.monotonic() - start) * 1000)
        yield {
            "type": "agent_finished",
            "agent": agent_id,
            "latency_ms": latency_ms,
            "llm_tokens": token_count,
            "path": "memory\u2192reverify" if re_verified else "memory",
            "memory_quote": captured.get("memory_content", ""),
            "re_verified": re_verified,
            "live_price": live_price,
            "confidence_status": captured.get("confidence_status", "unknown"),
            "confidence_score": captured.get("confidence_score"),
            "signal_count": captured.get("signal_count", 0),
            "answer": final_answer,
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
