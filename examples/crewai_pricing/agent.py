"""CrewAI sales agent for the Revok stale-memory pricing demo.

The agent has two tools:
- ``get_current_price_tool`` — reads live price from SQLite.
- ``check_memory_tool``      — queries Mem0 (direct or via Revok proxy).

Provider auto-detection at startup:
- Azure OpenAI when ``AZURE_OPENAI_API_KEY`` **and** ``AZURE_OPENAI_ENDPOINT`` are set.
- Plain OpenAI otherwise (``OPENAI_API_KEY`` must be set).

Two operating modes are supported:
- **WITHOUT REVOK** (``use_revok=False``): answers confidently from memory,
  ignores confidence score entirely.
- **WITH REVOK** (``use_revok=True``): checks confidence before answering;
  if ``degraded`` or ``stale`` re-verifies via ``get_current_price_tool``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import aiohttp

import database as db_module


def _llm_config() -> dict[str, str]:
    """Return Azure OpenAI or plain OpenAI config from environment variables."""
    az_key = os.getenv("AZURE_OPENAI_API_KEY", "")
    az_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    if az_key and az_endpoint:
        return {
            "provider": "azure",
            "api_key": az_key,
            "endpoint": az_endpoint.rstrip("/"),
            "deployment": os.getenv("AZURE_OPENAI_LLM_DEPLOYMENT", "gpt-4o-mini"),
            "api_version": os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview"),
        }
    return {
        "provider": "openai",
        "api_key": os.getenv("OPENAI_API_KEY", ""),
        "model": os.getenv("OPENAI_LLM_MODEL", "gpt-4o-mini"),
    }

_log = logging.getLogger(__name__)

_PRICE_RE = re.compile(r"\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)")


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


class PricingSalesAgent:
    """Sales agent backed by Mem0 memory; optionally confidence-aware via Revok.

    Two instances are created in the demo:

    1. **Without Revok** — ``memory_base_url`` points directly at Mem0,
       ``use_revok=False``.  The agent answers confidently from memory.

    2. **With Revok** — ``memory_base_url`` points at the Revok proxy so
       that all memory writes are enriched, ``use_revok=True``.  The agent
       checks confidence and re-verifies stale beliefs from the live DB.
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
        """Initialise the agent.

        Args:
            name:             Human-readable label used in logs and the UI.
            user_id:          Mem0 namespace for this agent's memories.
            memory_base_url:  Base URL for memory reads/writes.
            revok_base_url:   Base URL for Revok entity API; ``None`` when Revok is unused.
            use_revok:        Enable confidence-aware answering.
        """
        self.name = name
        self.user_id = user_id
        self.memory_base_url = memory_base_url.rstrip("/")
        self.revok_base_url = revok_base_url.rstrip("/") if revok_base_url else None
        self.use_revok = use_revok

    # ------------------------------------------------------------------
    # Tool 1: get_current_price
    # ------------------------------------------------------------------

    async def get_current_price_tool(self, product_name: str) -> dict[str, Any] | None:
        """Tool: read the current price for *product_name* from the SQLite database.

        Args:
            product_name: Exact product name to look up.

        Returns:
            Row dict with ``name``, ``price``, ``updated_at``, or ``None`` if not found.
        """
        return await db_module.get_price(product_name)

    # ------------------------------------------------------------------
    # Tool 2: check_memory
    # ------------------------------------------------------------------

    async def check_memory_tool(
        self, session: aiohttp.ClientSession
    ) -> list[dict[str, Any]]:
        """Tool: query Mem0 (through the agent's configured base URL) for stored memories.

        Args:
            session: Active aiohttp client session.

        Returns:
            List of memory dicts from Mem0.
        """
        return await self._list_memories(session)

    # ------------------------------------------------------------------
    # Memory helpers
    # ------------------------------------------------------------------

    async def store_pricing_belief(
        self,
        session: aiohttp.ClientSession,
        product_name: str,
        price: float,
    ) -> dict[str, Any]:
        """Store a long-term memory: customer budget approved at current price.

        The stored text deliberately contains the product name so that Revok's
        entity pattern can match it on subsequent writes.

        Args:
            session:      Active aiohttp client session.
            product_name: Product name to embed in the memory.
            price:        Current price to record.

        Returns:
            Mem0 response dict.
        """
        content = (
            f"Customer budget approved {product_name} at ${price:.0f}/month. "
            "Verified pricing from database."
        )
        _log.info("[%s] Storing belief: %s", self.name, content)
        return await self._add_memory(session, content)

    # ------------------------------------------------------------------
    # Main answer method
    # ------------------------------------------------------------------

    async def answer_budget_question(
        self,
        session: aiohttp.ClientSession,
        product_name: str,
        entity_key: str | None,
    ) -> AgentAnswer:
        """Answer "Is *product_name* within the customer budget?"

        WITHOUT REVOK: answers confidently from memory, ignores confidence.
        WITH REVOK:    checks confidence; if degraded/stale calls
                       ``get_current_price_tool`` to re-verify before answering.

        Args:
            session:      Active aiohttp client session.
            product_name: Product to look up in memory and (if needed) the DB.
            entity_key:   Revok entity key to query for confidence score.
                          ``None`` disables confidence checking.

        Returns:
            :class:`AgentAnswer` with answer text and metadata.
        """
        # Tool 2: check memory
        memories = await self.check_memory_tool(session)
        memory_quote = self._find_pricing_memory(memories, product_name)

        score: float | None = None
        signal_count: int = 0
        status: str = "unknown"

        if self.use_revok and entity_key:
            entity = await self._get_revok_entity(session, entity_key)
            if entity:
                score = float(entity["score"])
                signal_count = int(entity["signal_count"])
                status = _score_to_status(score)

        if not memory_quote:
            return AgentAnswer(
                answer=(
                    f"I don't have pricing information for {product_name} in memory yet. "
                    "Please load memory first."
                ),
                memory_quote="",
                confidence_score=score,
                confidence_status=status,
                signal_count=signal_count,
            )

        # WITHOUT REVOK: answer from memory only — no confidence check, no DB call
        if not self.use_revok:
            answer = await self._llm_answer(
                session,
                product_name=product_name,
                memory_quote=memory_quote,
                use_revok=False,
                status="unknown",
                score=None,
                signal_count=0,
                live_price=None,
            )
            return AgentAnswer(
                answer=answer,
                memory_quote=memory_quote,
                confidence_score=None,
                confidence_status="unknown",
                signal_count=0,
            )

        # WITH REVOK: only stale → re-verify from DB; degraded answers from memory with caveat
        live_price: float | None = None
        re_verified = False
        if status == "stale":
            row = await self.get_current_price_tool(product_name)
            if row:
                live_price = float(row[db_module.COL_PRICE])
                re_verified = True

        # WITH REVOK: ask LLM with full context (memory + confidence + live price if re-verified)
        answer = await self._llm_answer(
            session,
            product_name=product_name,
            memory_quote=memory_quote,
            use_revok=True,
            status=status,
            score=score,
            signal_count=signal_count,
            live_price=live_price,
        )
        return AgentAnswer(
            answer=answer,
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
        question: str = "What is the current price for Redis Enterprise per month?",
    ) -> AsyncIterator[dict[str, Any]]:
        """Streaming version of :meth:`answer_budget_question`.

        Yields typed event dicts consumed by the ``/stream/ask-agent`` SSE
        endpoint.  Event types emitted (in order):

        * ``agent_started``
        * ``memory_loaded``
        * ``confidence_checked`` (WITH Revok only)
        * ``db_reverified`` (WITH Revok, degraded/stale only)
        * ``token`` — one dict per LLM text chunk
        * ``agent_finished`` — includes latency_ms, llm_tokens, path, answer

        Args:
            session:      Active aiohttp client session.
            product_name: Product to look up.
            entity_key:   Revok entity key; ``None`` disables confidence checking.
        """
        start = time.monotonic()
        agent_id = "with_revok" if self.use_revok else "without_revok"

        yield {"type": "agent_started", "agent": agent_id}

        memories = await self.check_memory_tool(session)
        memory_quote = self._find_pricing_memory(memories, product_name)
        yield {
            "type": "memory_loaded",
            "agent": agent_id,
            "memory_quote": memory_quote,
            "memory_count": len(memories),
        }

        score: float | None = None
        signal_count: int = 0
        status: str = "unknown"

        if self.use_revok and entity_key:
            entity = await self._get_revok_entity(session, entity_key)
            if entity:
                score = float(entity["score"])
                signal_count = int(entity["signal_count"])
                status = _score_to_status(score)
            yield {
                "type": "confidence_checked",
                "agent": agent_id,
                "score": score,
                "status": status,
                "signal_count": signal_count,
            }

        if not memory_quote:
            latency_ms = int((time.monotonic() - start) * 1000)
            msg = (
                f"I don't have pricing information for {product_name} in memory yet. "
                "Please load memory first."
            )
            for ch in msg:
                yield {"type": "token", "agent": agent_id, "text": ch}
            yield {
                "type": "agent_finished",
                "agent": agent_id,
                "latency_ms": latency_ms,
                "llm_tokens": 0,
                "path": "no_memory",
                "memory_quote": "",
                "re_verified": False,
                "live_price": None,
                "confidence_status": status,
                "confidence_score": score,
                "signal_count": signal_count,
                "answer": msg,
            }
            return

        live_price: float | None = None
        re_verified = False
        path = "memory"

        if self.use_revok and status == "stale":
            row = await self.get_current_price_tool(product_name)
            if row:
                live_price = float(row[db_module.COL_PRICE])
                re_verified = True
                path = "memory\u2192reverify"
                yield {
                    "type": "db_reverified",
                    "agent": agent_id,
                    "live_price": live_price,
                    "reason": status,
                }
        elif self.use_revok and status == "degraded":
            path = "memory (caveat)"

        full_text: list[str] = []
        token_count = 0
        async for tok_evt in self._llm_answer_streaming(
            session,
            product_name=product_name,
            memory_quote=memory_quote,
            use_revok=self.use_revok,
            status=status,
            score=score,
            signal_count=signal_count,
            live_price=live_price,
            question=question,
        ):
            if tok_evt["type"] == "token":
                full_text.append(tok_evt["text"])
                yield {"type": "token", "agent": agent_id, "text": tok_evt["text"]}
            elif tok_evt["type"] == "llm_done":
                token_count = tok_evt.get("token_count", len(full_text))

        answer_text = "".join(full_text)
        latency_ms = int((time.monotonic() - start) * 1000)
        yield {
            "type": "agent_finished",
            "agent": agent_id,
            "latency_ms": latency_ms,
            "llm_tokens": token_count,
            "path": path,
            "memory_quote": memory_quote,
            "re_verified": re_verified,
            "live_price": live_price,
            "confidence_status": status,
            "confidence_score": score,
            "signal_count": signal_count,
            "answer": answer_text,
        }

    # ------------------------------------------------------------------
    # LLM answer generation
    # ------------------------------------------------------------------

    def _build_prompts(
        self,
        product_name: str,
        memory_quote: str,
        use_revok: bool,
        status: str,
        score: float | None,
        signal_count: int,
        live_price: float | None,
        question: str = "What is the current price for Redis Enterprise per month?",
    ) -> tuple[str, str]:
        """Build ``(system, user)`` prompts for the LLM.

        Extracted so both the batch ``_llm_answer`` and the streaming
        ``_llm_answer_streaming`` use identical prompts.

        Returns:
            Tuple of *(system_prompt, user_prompt)*.
        """
        if use_revok:
            if live_price is not None:
                # Stale path: memory was stale, re-verified from live DB
                system = (
                    "You are a confidence-aware sales agent. Your memory layer tracks "
                    "how fresh your stored knowledge is. When memory confidence is too low, "
                    "you always re-verify from the live database before answering. "
                    "Be concise (2-3 sentences). Mention that you re-verified and use "
                    "the live price in your answer."
                )
                user = (
                    f"The customer is asking about {product_name}.\n"
                    f"Customer question: \"{question}\"\n"
                    f"Your stored memory: \"{memory_quote}\"\n"
                    f"Memory confidence: {status} (score={score:.2f}, signals={signal_count})\n"
                    f"You re-verified via the live database: current price is ${live_price:.0f}/month.\n"
                    f"Answer the customer's question using the re-verified price."
                )
            elif status == "degraded":
                # Degraded path: answer from memory but add explicit caveat
                score_str = f"{score:.2f}" if score is not None else "N/A"
                system = (
                    "You are a confidence-aware sales agent. Your memory layer tracks "
                    "how fresh your stored knowledge is. When confidence is degraded, "
                    "answer from memory but explicitly tell the customer the information "
                    "may need verification before acting on it. Be concise (2-3 sentences)."
                )
                user = (
                    f"The customer is asking about {product_name}.\n"
                    f"Customer question: \"{question}\"\n"
                    f"Your stored memory: \"{memory_quote}\"\n"
                    f"Memory confidence: degraded (score={score_str}, signals={signal_count})\n"
                    f"Answer from memory but include a caveat that this information may need verification."
                )
            else:
                # Fresh path: answer confidently from memory
                score_str = f"{score:.2f}" if score is not None else "N/A"
                system = (
                    "You are a confidence-aware sales agent. Your memory layer tracks "
                    "how fresh your stored knowledge is. Confidence is high — answer "
                    "confidently from memory. Be concise (2-3 sentences)."
                )
                user = (
                    f"The customer is asking about {product_name}.\n"
                    f"Customer question: \"{question}\"\n"
                    f"Your stored memory: \"{memory_quote}\"\n"
                    f"Memory confidence: fresh (score={score_str}, signals={signal_count})\n"
                    f"Answer the customer's question confidently from memory."
                )
        else:
            system = (
                "You are a sales agent. You answer customer questions using only "
                "the information stored in your memory. You have no way to verify "
                "if your memory is current. Be concise (2-3 sentences) and confident."
            )
            user = (
                f"The customer is asking about {product_name}.\n"
                f"Customer question: \"{question}\"\n"
                f"Your stored memory: \"{memory_quote}\"\n"
                f"Answer the customer's question from memory."
            )
        return system, user

    async def _llm_answer(
        self,
        session: aiohttp.ClientSession,
        *,
        product_name: str,
        memory_quote: str,
        use_revok: bool,
        status: str,
        score: float | None,
        signal_count: int,
        live_price: float | None,
    ) -> str:
        """Call the LLM to generate a natural-language answer (batch mode).

        Used by the non-streaming ``/actions/ask-agent`` endpoint.
        Prompt construction is shared with ``_llm_answer_streaming`` via
        :meth:`_build_prompts`.
        """
        system, user = self._build_prompts(
            product_name, memory_quote, use_revok, status, score, signal_count, live_price
        )

        cfg = _llm_config()
        try:
            if cfg["provider"] == "azure":
                url = (
                    f"{cfg['endpoint']}/openai/deployments/{cfg['deployment']}"
                    f"/chat/completions?api-version={cfg['api_version']}"
                )
                headers = {
                    "Content-Type": "application/json",
                    "api-key": cfg["api_key"],
                }
            else:
                url = "https://api.openai.com/v1/chat/completions"
                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {cfg['api_key']}",
                }
            payload: dict[str, Any] = {
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": 0.3,
                "max_tokens": 200,
            }
            if cfg["provider"] == "openai":
                payload["model"] = cfg["model"]

            async with session.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return data["choices"][0]["message"]["content"].strip()
        except Exception as exc:
            _log.error("[%s] LLM call failed: %s", self.name, exc)
            price_str = self._extract_price(memory_quote) or "an approved amount"
            if live_price is not None:
                return (
                    f"[LLM unavailable] Memory said {price_str} but confidence was stale. "
                    f"Re-verified: current price is ${live_price:.0f}/month."
                )
            if status == "degraded":
                return (
                    f"[LLM unavailable] Based on memory: {product_name} is approved at {price_str}. "
                    "(Note: confidence is degraded — this information may need verification.)"
                )
            return f"[LLM unavailable] Based on memory: {product_name} is approved at {price_str}."

    async def _llm_answer_streaming(
        self,
        session: aiohttp.ClientSession,
        *,
        product_name: str,
        memory_quote: str,
        use_revok: bool,
        status: str,
        score: float | None,
        signal_count: int,
        live_price: float | None,
        question: str = "What is the current price for Redis Enterprise per month?",
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream LLM tokens via Azure OpenAI / OpenAI streaming API.

        Yields ``{"type": "token", "text": "..."}`` dicts as each chunk
        arrives, followed by ``{"type": "llm_done", "token_count": N}``.
        Falls back to emitting the whole fallback string as tokens on error.
        """
        system, user = self._build_prompts(
            product_name, memory_quote, use_revok, status, score, signal_count, live_price,
            question=question,
        )

        cfg = _llm_config()
        if cfg["provider"] == "azure":
            url = (
                f"{cfg['endpoint']}/openai/deployments/{cfg['deployment']}"
                f"/chat/completions?api-version={cfg['api_version']}"
            )
            headers = {"Content-Type": "application/json", "api-key": cfg["api_key"]}
        else:
            url = "https://api.openai.com/v1/chat/completions"
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {cfg['api_key']}",
            }

        payload: dict[str, Any] = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.3,
            "max_tokens": 200,
            "stream": True,
        }
        if cfg["provider"] == "openai":
            payload["model"] = cfg["model"]

        token_count = 0
        try:
            async with session.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:
                resp.raise_for_status()
                async for raw_line in resp.content:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:]
                    if data_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data_str)
                        text = chunk["choices"][0]["delta"].get("content", "")
                        if text:
                            token_count += 1
                            yield {"type": "token", "text": text}
                    except (json.JSONDecodeError, KeyError, IndexError):
                        pass
        except Exception as exc:
            _log.error("[%s] LLM streaming failed: %s", self.name, exc)
            price_str = self._extract_price(memory_quote) or "an approved amount"
            if live_price is not None:
                fallback = (
                    f"[LLM unavailable] Memory said {price_str} but confidence was stale. "
                    f"Re-verified: current price is ${live_price:.0f}/month."
                )
            elif status == "degraded":
                fallback = (
                    f"[LLM unavailable] Based on memory: {product_name} is approved at {price_str}. "
                    "(Note: confidence is degraded — this information may need verification.)"
                )
            else:
                fallback = (
                    f"[LLM unavailable] Based on memory: {product_name} is approved at {price_str}."
                )
            for ch in fallback:
                token_count += 1
                yield {"type": "token", "text": ch}
        yield {"type": "llm_done", "token_count": token_count}

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
    ) -> dict[str, Any]:
        payload = {
            "messages": [{"role": role, "content": content}],
            "user_id": self.user_id,
        }
        async with session.post(
            f"{self.memory_base_url}/memories",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def _list_memories(
        self, session: aiohttp.ClientSession
    ) -> list[dict[str, Any]]:
        async with session.get(
            f"{self.memory_base_url}/memories",
            params={"user_id": self.user_id},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
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
