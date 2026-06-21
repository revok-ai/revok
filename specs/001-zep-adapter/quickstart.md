# Quickstart: Zep Community Edition Adapter

**Feature**: 001-zep-adapter  
**Audience**: Operator deploying a Zep-backed AI agent through Revok

---

## Minimal `revok.yaml` for Zep CE

```yaml
server:
  host: "0.0.0.0"
  port: 8080
  startup_timeout_seconds: 10

# Switch Revok to Zep mode
adapter_type: zep

zep:
  # Base URL of your Zep Community Edition instance
  zep_url: "http://zep:8000"
  # HTTP methods that trigger entity extraction
  write_methods:
    - "POST"
  # Stored for reference; the actual write gate uses the path anchor:
  #   path.startswith("/api/v1/sessions/") and path.endswith("/memory")
  write_paths:
    - "/api/v1/sessions/"

entity_matcher:
  entities:
    - id: "my_entity"
      display_name: "My Entity"
      aliases:
        - "my entity"
        - "My Entity"

scoring:
  half_life_seconds: 86400
  signal_strength: 0.3
  score_cap: 1.0

state_store:
  sqlite_path: "/data/revok.db"
  hot_layer_max_entries: 256

logging:
  level: "INFO"
  format: "%(asctime)s %(levelname)s %(name)s %(message)s"
```

---

## Agent Configuration

Point your agent at Revok instead of directly at Zep CE:

```python
# Before (direct Zep)
zep_client = ZepClient(base_url="http://zep:8000", api_key="...")

# After (through Revok — zero other changes)
zep_client = ZepClient(base_url="http://revok:8080", api_key="...")
```

All headers (including `Authorization`) are forwarded verbatim by Revok.

---

## Verifying Entity Tracking

After your agent writes a memory containing a tracked entity:

```bash
# Query Revok's entity store
curl http://revok:8080/v1/entities/my_entity
```

Expected response:
```json
{
  "entity_key": "my_entity",
  "score": 0.3,
  "last_seen": 1749398400.0,
  "signal_count": 1,
  "pattern_name": "catalog"
}
```

---

## Docker Compose Snippet

```yaml
services:
  zep:
    image: ghcr.io/getzep/zep:latest
    ports: ["8000:8000"]

  revok:
    image: revok:latest
    ports: ["8080:8080"]
    volumes:
      - ./revok.yaml:/app/revok.yaml
      - revok_data:/data
    command: ["revok", "--config", "/app/revok.yaml"]
    depends_on: [zep]

volumes:
  revok_data:
```
