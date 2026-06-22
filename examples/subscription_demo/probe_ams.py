"""Probe the Redis AMS search endpoint to discover the correct payload schema."""
import asyncio
import aiohttp
import json
import os

AMS_URL = os.getenv("AMS_URL", "http://redis-ams:8000")

async def main():
    async with aiohttp.ClientSession() as sess:
        # 1. Fetch OpenAPI schema
        async with sess.get(f"{AMS_URL}/openapi.json") as r:
            schema = await r.json()
        # Print search-related schemas
        for k, v in schema.get("components", {}).get("schemas", {}).items():
            if "search" in k.lower() or "Search" in k:
                print(f"--- SCHEMA: {k} ---")
                print(json.dumps(v, indent=2)[:1000])
        # Print search endpoint
        for path, methods in schema.get("paths", {}).items():
            if "search" in path:
                print(f"--- PATH: {path} ---")
                print(json.dumps(methods, indent=2)[:800])
        # 2. Test payloads
        payloads = [
            {"text": "Orion Cache", "session_id": {"user_id": "demo-with-revok"}},
            {"text": "Orion Cache", "user_id": "demo-with-revok"},
            {"text": "Orion Cache", "filters": {"user_id": "demo-with-revok"}},
            {"query": "Orion Cache", "session_id": {"user_id": "demo-with-revok"}},
        ]
        for p in payloads:
            async with sess.post(f"{AMS_URL}/v1/long-term-memory/search", json=p) as r:
                body = await r.text()
                print(f"keys={list(p.keys())} -> {r.status}: {body[:300]}")

asyncio.run(main())
