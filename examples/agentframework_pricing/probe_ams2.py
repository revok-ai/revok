"""Probe AMS endpoints to discover correct API schema."""
import asyncio
import aiohttp
import json
import os

AMS_URL = os.getenv("AMS_URL", "http://redis-ams:8000")

async def main():
    async with aiohttp.ClientSession() as sess:
        # Get full OpenAPI schema
        async with sess.get(f"{AMS_URL}/openapi.json") as r:
            schema = await r.json()

        # Print SearchRequest fully
        sr = schema.get("components", {}).get("schemas", {}).get("SearchRequest", {})
        print("=== FULL SearchRequest ===")
        print(json.dumps(sr, indent=2))

        # Print all long-term-memory paths and methods
        print("\n=== LONG-TERM MEMORY PATHS ===")
        for path, methods in schema.get("paths", {}).items():
            if "long-term" in path:
                print(f"\n{path}:")
                for method, info in methods.items():
                    params = [p.get("name") for p in info.get("parameters", [])]
                    req_body = info.get("requestBody", {})
                    print(f"  {method.upper()}: params={params} body={'yes' if req_body else 'no'}")
                    if req_body:
                        content = req_body.get("content", {}).get("application/json", {})
                        ref = content.get("schema", {}).get("$ref", "")
                        if ref:
                            schema_name = ref.split("/")[-1]
                            s = schema.get("components", {}).get("schemas", {}).get(schema_name, {})
                            print(f"    body schema ({schema_name}): {json.dumps(s, indent=4)[:600]}")

        # Test search with filter objects
        test_payloads = [
            {"text": "Orion Cache", "session_id": {"eq": "demo-with-revok"}},
            {"text": "Orion Cache", "user_id": {"eq": "demo-with-revok"}},
        ]
        print("\n=== SEARCH TESTS ===")
        for p in test_payloads:
            try:
                async with sess.post(f"{AMS_URL}/v1/long-term-memory/search", json=p) as r:
                    body = await r.text()
                    print(f"payload keys={list(p.keys())} filter={list(p[list(p.keys())[1]].keys())} -> {r.status}: {body[:200]}")
            except Exception as e:
                print(f"payload keys={list(p.keys())} -> ERROR: {e}")

asyncio.run(main())
