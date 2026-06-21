# Quickstart — Signal-Driven Causal Propagation (005)

## 1) Configure causal graph

Add to `revok.yaml`:

```yaml
causal_graph:
  relationships:
    - from: entity-a
      to: entity-b
      weight: 0.9
    - from: entity-a
      to: entity-c
      weight: 0.7
    - from: entity-b
      to: entity-d
      weight: 0.8
    - from: entity-c
      to: entity-d
      weight: 0.95
  propagation:
    min_pressure: 0.05
    max_hops: 3
    attenuation: 0.5
```

## 2) Start Revok

```bash
revok --config revok.yaml
```

## 3) Seed baseline entity records

Create or update memories/signals so target entities exist in state store.

## 4) Fire a root signal

```bash
curl -X POST http://localhost:8080/signals \
  -H "Content-Type: application/json" \
  -d '{
    "entity_refs": ["entity-a"],
    "severity": "high",
    "source": "webhook",
    "payload": {}
  }'
```

Expected immediate response:
- `202 Accepted`

## 5) Verify root and downstream degradation

```bash
curl http://localhost:8080/v1/entities/entity-a
curl http://localhost:8080/v1/entities/entity-b
curl http://localhost:8080/v1/entities/entity-c
curl http://localhost:8080/v1/entities/entity-d
```

Expected behavior:
- `entity-a` score decreases after signal is processed.
- `entity-b` / `entity-c` decrease according to edge weight × attenuation.
- `entity-d` decreases using the stronger path pressure (max of A->B->D vs A->C->D), not sum.

## 6) Cycle safety smoke test

Add a cycle in config (for test env), restart, fire signal, and confirm:
- request returns `202`
- scores update
- service remains responsive (no hang/infinite loop)

## 7) Test suite

```bash
pytest
ruff check revok/ tests/
mypy revok/
```
