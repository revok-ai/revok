# Quickstart: Revok 0.1.0

**Estimated time**: < 15 minutes from clone to first enriched signal (SC-001)

---

## Prerequisites

- Python 3.11 or later (`python --version`)
- A running Mem0 instance reachable via HTTP (e.g., `http://localhost:8000`)
- Git

---

## 1. Clone and Install

```bash
git clone <repo-url>
cd revok
pip install -e ".[dev]"
```

The `[dev]` extra installs `pytest`, `pytest-asyncio`, and any other dev-only tools. The base install includes `aiohttp`, `aiosqlite`, `networkx`, and `pyyaml`.

---

## 2. Configure

Copy the example config and edit it for your environment:

```bash
cp config/revok.example.yaml config/revok.yaml
```

Open `config/revok.yaml` and set at minimum:

```yaml
upstream:
  mem0_url: "http://localhost:8000"   # your Mem0 URL

server:
  host: "127.0.0.1"
  port: 8080
```

All other values have working defaults (including `server.max_signal_size_bytes: 1048576` for the 1 MiB request-body limit). See `config/revok.example.yaml` for the full schema with comments.

---

## 3. Start Revok

```bash
python -m revok --config config/revok.yaml
```

Expected output:
```
INFO  revok.config  Config loaded from config/revok.yaml
INFO  revok.store   SQLite WAL store opened at ./revok_state.db
INFO  revok.proxy   Revok listening on http://127.0.0.1:8080
```

---

## 4. Send a Test Signal

Point your AI agent at `http://127.0.0.1:8080` instead of your Mem0 URL. Or send a test write manually:

```bash
curl -X POST http://127.0.0.1:8080/v1/memories \
  -H "Content-Type: application/json" \
  -d '{"content": "Alice reminded Bob about the Friday deadline.", "agent_id": "test"}'
```

Expected: Revok logs entity extraction + scoring, then Mem0 receives the write with an `x_revok` metadata block appended.

---

## 5. Verify Enrichment

Check your Mem0 instance for the stored memory. The payload should include:

```json
{
  "content": "Alice reminded Bob about the Friday deadline.",
  "agent_id": "test",
  "x_revok": {
    "version": "0.1.0",
    "entities": [
      {"id": "alice", "score": 0.3, "signal_count": 1, "pattern_name": "person"},
      {"id": "bob",   "score": 0.3, "signal_count": 1, "pattern_name": "person"}
    ]
  }
}
```

---

## 6. Run the Test Suite

```bash
pytest
```

All tests should pass with no additional setup. Tests use in-memory SQLite and a mock Mem0 server — no live Mem0 instance required for the test suite.

---

## Architecture Overview

```
AI Agent
   │  POST /v1/memories
   ▼
┌──────────────┐
│  Revok Proxy │  (aiohttp server, port 8080)
│  proxy.py    │
└──────┬───────┘
       │
       ├── entity_matcher.py  (regex patterns from config)
       ├── scoring.py         (exponential decay)
       ├── state_store.py     (SQLite WAL + LRU hot layer)
       └── metadata_writer.py (x_revok block assembly)
       │
       ▼  enriched payload
┌─────────────┐
│     Mem0    │  (upstream, configurable URL)
└─────────────┘
```

---

## Common Issues

| Problem | Fix |
|---------|-----|
| `ConfigError: mem0_url is required` | Set `upstream.mem0_url` in your YAML config |
| `ConfigError: half_life_seconds must be > 0` | Check `scoring.half_life_seconds` in config |
| `502 Bad Gateway` on requests | Mem0 is unreachable — check `upstream.mem0_url` and that Mem0 is running |
| `413 Payload Too Large` on writes | Request body exceeds `server.max_signal_size_bytes` (default 1 MiB); increase the limit or reduce the payload |
| `sqlite3.OperationalError` on startup | Check that the directory for `state_store.sqlite_path` exists and is writable |
| `RuntimeError: State store database is corrupted` | Delete or restore `state_store.sqlite_path` and restart |
| Entities not extracted | Verify `entity_matcher.patterns` regexes in config; test with `python -c "import re; print(re.findall(r'YOUR_PATTERN', 'test text'))"` |
