"""Probe AMS store and search schema."""
import asyncio
import aiohttp
import json
import os

AMS_URL = os.getenv("AMS_URL", "http://redis-ams:8000")

async def main():
    async with aiohttp.ClientSession() as sess:
        async with sess.get(f"{AMS_URL}/openapi.json") as r:
            schema = await r.json()

        emr = schema["components"]["schemas"].get("ExtractedMemoryRecord", {})
        print("=== ExtractedMemoryRecord ===")
        print(json.dumps(emr, indent=2))

        # Also check SessionId filter schema
        sid = schema["components"]["schemas"].get("SessionId", {})
        print("=== SessionId ===")
        print(json.dumps(sid, indent=2))

        uid = schema["components"]["schemas"].get("UserId", {})
        print("=== UserId ===")
        print(json.dumps(uid, indent=2))

        # Test store payloads
        tests = [
            {"memories": [{"text": "Orion Cache costs 500 per month", "session_id": "test-user-1"}]},
            {"memories": [{"text": "Orion Cache costs 500 per month", "user_id": "test-user-2"}]},
            {"memories": [{"text": "Orion Cache costs 500 per month"}]},
        ]
        for t in tests:
            mem_fields = list(t["memories"][0].keys())
            async with sess.post(f"{AMS_URL}/v1/long-term-memory/", json=t) as r:
                body = await r.text()
                print(f"store fields={mem_fields} -> {r.status}: {body[:200]}")

        # Search with no filters
        for payload in [{"text": "Orion Cache"}, {}]:
            async with sess.post(f"{AMS_URL}/v1/long-term-memory/search", json=payload) as r:
                body = await r.text()
                print(f"search payload={payload} -> {r.status}: {body[:400]}")

asyncio.run(main())
