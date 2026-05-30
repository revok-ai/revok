# Revok

> Transparent entity-scoring middleware for AI memory systems.

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue)](https://www.python.org/downloads/)

Revok sits between your AI agent and [Mem0](https://mem0.ai), intercepts every write, extracts named entities from the content, scores them with exponential decay, and appends an `x_revok` metadata block to each forwarded payload — without changing the observable API.

---

## Architecture

```
AI Agent
   │  POST /v1/memories (or any write path)
   ▼
┌──────────────────────────────┐
│  Revok Proxy  (aiohttp :8080) │
│  proxy.py                    │
└──────┬───────────────────────┘
       │
       ├── entity_matcher.py   regex patterns from config
       ├── scoring.py          exponential decay (score = score·e^{-λΔt} + signal)
       ├── state_store.py      SQLite WAL + LRU hot layer
       ├── metadata_writer.py  assembles x_revok block
       └── causal_graph.py     entity relationship scaffold (NetworkX)
       │
       │  enriched payload (original body + x_revok)
       ▼
┌─────────────┐
│    Mem0     │  upstream, configurable URL
└─────────────┘
```

Read endpoints served by Revok directly (not forwarded):

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/v1/entities/{entity_key}` | Fetch a single entity record with read-time decay |
| `GET` | `/v1/entities` | Paginated list — `?offset=0&limit=100` (max 500) |

---

## Prerequisites

- Python 3.11 or later
- A running Mem0 instance reachable via HTTP (e.g. `http://localhost:8000`)
- Git

---

## Install

```bash
git clone <repo-url>
cd revok
pip install -e ".[dev]"
```

The `[dev]` extra adds `pytest`, `pytest-asyncio`, `ruff`, `mypy`, and `types-PyYAML`. The base install pulls in `aiohttp`, `aiosqlite`, `networkx`, and `pyyaml`.

---

## Configure

Copy the example config and edit it for your environment:

```bash
cp config/revok.example.yaml config/revok.yaml
```

Minimum required values:

```yaml
upstream:
  mem0_url: "http://localhost:8000"   # your Mem0 instance

server:
  host: "127.0.0.1"
  port: 8080
```

See `config/revok.example.yaml` for the full annotated schema.

---

## Usage

```bash
python -m revok --config config/revok.yaml
```

Expected startup output:

```
INFO  revok.config  Config loaded from config/revok.yaml
INFO  revok.store   SQLite WAL store opened at ./revok_state.db
INFO  revok.proxy   Revok listening on http://127.0.0.1:8080
```

Point your AI agent at `http://127.0.0.1:8080` instead of Mem0. Revok is transparent — every request is forwarded unchanged except for the appended `x_revok` block.

### Example write

```bash
curl -X POST http://127.0.0.1:8080/v1/memories \
  -H "Content-Type: application/json" \
  -d '{"content": "Alice reminded Bob about the Friday deadline.", "agent_id": "test"}'
```

The payload forwarded to Mem0 will include:

```json
{
  "content": "Alice reminded Bob about the Friday deadline.",
  "agent_id": "test",
  "x_revok": {
    "version": "0.1.0",
    "entities": [
      {"entity_key": "alice", "score": 0.3, "signal_count": 1, "pattern_name": "person"},
      {"entity_key": "bob",   "score": 0.3, "signal_count": 1, "pattern_name": "person"}
    ]
  }
}
```

### Example reads

```bash
# Single entity (with read-time decay applied)
curl http://127.0.0.1:8080/v1/entities/alice

# Paginated list
curl "http://127.0.0.1:8080/v1/entities?offset=0&limit=50"
```

---

## YAML Config Reference

```yaml
server:
  host: "127.0.0.1"          # bind address
  port: 8080                  # listen port
  startup_timeout_seconds: 10 # abort startup if not ready within this many seconds

upstream:
  mem0_url: "http://localhost:8000"   # required — Mem0 base URL
  write_methods: ["POST", "PUT", "PATCH"]
  write_paths: ["/v1/memories"]       # paths that trigger entity enrichment

entity_matcher:
  patterns:
    - name: person             # pattern label stored with each entity
      regex: '\b[A-Z][a-z]+\b' # named entity regex (stdlib re, no spaCy)

scoring:
  half_life_seconds: 86400    # score halves every 24 hours
  signal_strength: 0.3        # score added per new signal
  score_cap: 1.0              # maximum possible score

state_store:
  sqlite_path: "./revok_state.db"
  hot_layer_size: 1000        # LRU in-memory cache size (entity records)

logging:
  level: "INFO"               # DEBUG | INFO | WARNING | ERROR
```

---

## Test Suite

```bash
pytest
```

All tests use in-memory SQLite and a mock HTTP server — no live Mem0 instance required.

Coverage includes:

| Test file | Module |
|-----------|--------|
| `tests/test_config.py` | `revok/config.py` |
| `tests/test_models.py` | `revok/models.py` |
| `tests/test_entity_matcher.py` | `revok/entity_matcher.py` |
| `tests/test_scoring.py` | `revok/scoring.py` |
| `tests/test_state_store.py` | `revok/state_store.py` |
| `tests/test_signal_queue.py` | `revok/signal_queue.py` |
| `tests/test_metadata_writer.py` | `revok/metadata_writer.py` |
| `tests/test_proxy.py` | `revok/proxy.py` |

---

## Common Issues

| Problem | Fix |
|---------|-----|
| `ConfigError: mem0_url is required` | Set `upstream.mem0_url` in your YAML config |
| `ConfigError: half_life_seconds must be > 0` | Check `scoring.half_life_seconds` |
| `502 Bad Gateway` | Mem0 is unreachable — check `upstream.mem0_url` |
| `sqlite3.OperationalError` on startup | Ensure the directory for `state_store.sqlite_path` exists and is writable |
| Entities not extracted | Verify `entity_matcher.patterns` regexes; test with `python -c "import re; print(re.findall(r'YOUR_PATTERN', 'test text'))"` |

---

## Contributing

1. Fork the repository and create a feature branch.
2. Run `pytest` and `python -m mypy revok/ --strict` before submitting a PR.
3. All source files in `revok/` and `tests/` must carry the AGPL v3 header.
4. Keep dependencies minimal — the Constitution forbids `requests`, `spaCy`, `NLTK`, and other heavy NLP libraries. Use `aiohttp` for all HTTP I/O.

---

## License

Revok is free software: you can redistribute it and/or modify it under the terms of the [GNU Affero General Public License v3](https://www.gnu.org/licenses/agpl-3.0) or later.

Copyright © 2026 Revok Contributors.
