"""Minimal Mem0-compatible FastAPI server for the LangGraph pricing demo."""

from __future__ import annotations

import logging
import os
import urllib.parse
from typing import Any

import uvicorn
from fastapi import FastAPI
from mem0 import Memory
from pydantic import BaseModel

logger = logging.getLogger(__name__)

QDRANT_HOST: str = os.environ.get("QDRANT_HOST", "localhost")
QDRANT_PORT: int = int(os.environ.get("QDRANT_PORT", "6333"))

_AZURE_KEY: str = os.environ.get("AZURE_OPENAI_API_KEY", "")
_AZURE_ENDPOINT_RAW: str = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
_AZURE_API_VERSION: str = os.environ.get(
    "AZURE_OPENAI_API_VERSION", "2025-04-01-preview"
)
_AZURE_LLM_DEPLOYMENT: str = os.environ.get(
    "AZURE_OPENAI_LLM_DEPLOYMENT", "gpt-4o-mini"
)
_AZURE_EMBEDDER_DEPLOYMENT: str = os.environ.get(
    "AZURE_OPENAI_EMBEDDER_DEPLOYMENT", "text-embedding-ada-002"
)

_OPENAI_KEY: str = os.environ.get("OPENAI_API_KEY", "")
_OPENAI_LLM_MODEL: str = os.environ.get("OPENAI_LLM_MODEL", "gpt-4o-mini")
_OPENAI_EMBEDDER_MODEL: str = os.environ.get(
    "OPENAI_EMBEDDER_MODEL", "text-embedding-3-small"
)


def _azure_base_endpoint(raw: str) -> str:
    candidate = raw.strip()
    if not candidate:
        return ""

    # Foundry users sometimes paste endpoints without a scheme.
    if "://" not in candidate:
        candidate = f"https://{candidate}"

    parsed = urllib.parse.urlparse(candidate)
    host = parsed.netloc or parsed.path
    if not host:
        return ""

    return f"{parsed.scheme or 'https'}://{host}/"


def _build_mem0_config() -> dict[str, Any]:
    vector_store: dict[str, Any] = {
        "provider": "qdrant",
        "config": {
            "host": QDRANT_HOST,
            "port": QDRANT_PORT,
            "collection_name": "langgraph_pricing_demo",
        },
    }

    if _AZURE_KEY and _AZURE_ENDPOINT_RAW:
        endpoint = _azure_base_endpoint(_AZURE_ENDPOINT_RAW)
        if not endpoint:
            raise RuntimeError(
                "AZURE_OPENAI_ENDPOINT is set but invalid. "
                "Use a valid host URL like https://<resource>.cognitiveservices.azure.com"
            )
        logger.info("Provider: Azure OpenAI endpoint=%s", endpoint)
        return {
            "vector_store": vector_store,
            "llm": {
                "provider": "azure_openai",
                "config": {
                    "model": _AZURE_LLM_DEPLOYMENT,
                    "azure_kwargs": {
                        "api_key": _AZURE_KEY,
                        "azure_endpoint": endpoint,
                        "azure_deployment": _AZURE_LLM_DEPLOYMENT,
                        "api_version": _AZURE_API_VERSION,
                    },
                },
            },
            "embedder": {
                "provider": "azure_openai",
                "config": {
                    "model": _AZURE_EMBEDDER_DEPLOYMENT,
                    "azure_kwargs": {
                        "api_key": _AZURE_KEY,
                        "azure_endpoint": endpoint,
                        "azure_deployment": _AZURE_EMBEDDER_DEPLOYMENT,
                        "api_version": _AZURE_API_VERSION,
                    },
                },
            },
        }

    if _OPENAI_KEY:
        logger.info(
            "Provider: OpenAI llm=%s embedder=%s",
            _OPENAI_LLM_MODEL,
            _OPENAI_EMBEDDER_MODEL,
        )
        return {
            "vector_store": vector_store,
            "llm": {
                "provider": "openai",
                "config": {"api_key": _OPENAI_KEY, "model": _OPENAI_LLM_MODEL},
            },
            "embedder": {
                "provider": "openai",
                "config": {"api_key": _OPENAI_KEY, "model": _OPENAI_EMBEDDER_MODEL},
            },
        }

    raise RuntimeError(
        "No LLM provider configured. Set AZURE_OPENAI_API_KEY + AZURE_OPENAI_ENDPOINT or OPENAI_API_KEY."
    )


_MEM0_CONFIG: dict[str, Any] = _build_mem0_config()
_memory: Memory | None = None


def get_memory() -> Memory:
    global _memory
    if _memory is None:
        _memory = Memory.from_config(config_dict=_MEM0_CONFIG)
    return _memory


class Message(BaseModel):
    role: str
    content: str


class MemoryCreate(BaseModel):
    messages: list[Message]
    user_id: str | None = None
    agent_id: str | None = None


app = FastAPI(title="Mem0 demo server")


@app.post("/memories")
async def add_memory(body: MemoryCreate) -> Any:
    params: dict[str, Any] = {}
    if body.user_id:
        params["user_id"] = body.user_id
    if body.agent_id:
        params["agent_id"] = body.agent_id
    return get_memory().add(messages=[m.model_dump() for m in body.messages], **params)


@app.get("/memories")
async def get_memories(user_id: str | None = None, agent_id: str | None = None) -> Any:
    filters: dict[str, str] = {}
    if user_id:
        filters["user_id"] = user_id
    if agent_id:
        filters["agent_id"] = agent_id
    m = get_memory()
    return m.get_all(filters=filters) if filters else m.get_all()


@app.delete("/memories/{memory_id}")
async def delete_memory(memory_id: str) -> Any:
    get_memory().delete(memory_id)
    return {"deleted": memory_id}


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
