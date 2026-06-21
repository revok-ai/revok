# Quickstart: Bitemporal Scoring (Feature 002)

## What changed

| Component | Change |
|-----------|--------|
| `EntityRecord` | Two new stored fields: `valid_time`, `transaction_time`; `last_seen` → read-only `@property` |
| `Signal` | New optional field: `valid_time: float \| None = None` |
| `ScoringEngine` | Uses `valid_time` for decay calculation instead of `last_seen` |
| `SqliteStateStore` | Two new columns; auto-migration on `open()` |
| `enrich()` | Populates both new fields; clamps future-dated `valid_time` |
| `GET /v1/entities/{key}` | Response now contains `valid_time` + `transaction_time` instead of `last_seen` |

## Running tests

```powershell
# From repo root with venv active
. .venv\Scripts\Activate.ps1

# Baseline (must be 184 before implementation)
python -m pytest tests/ -q

# After implementation (must be ≥ 194)
python -m pytest tests/ -q

# Quality gates
python -m ruff check revok/ tests/
python -m mypy revok/ tests/
```

## Implementation sequence

1. **`revok/models.py`** — Add fields, convert `last_seen` to property  
2. **`revok/scoring.py`** — `last_seen` → `valid_time` (2 lines)  
3. **`revok/state_store.py`** — DDL, migration, SELECT/INSERT/UPDATE, `_apply_decay`  
4. **`revok/metadata_writer.py`** — Clamp logic, populate both fields  
5. **Update tests** — Existing construction sites (mechanical)  
6. **New tests** — `tests/test_bitemporal_scoring.py` (≥ 10 tests)  

## Verifying backward compat

After implementation, the existing SQLite file (if any) should be loadable without
data loss. Test with:

```python
# Insert an old-style row (no valid_time / transaction_time)
await db.execute(
    "INSERT INTO entity_records (entity_key, score, last_seen, signal_count, pattern_name) "
    "VALUES ('test:compat', 0.5, 1717612800.0, 1, 'header')"
)
await db.commit()
record = await store.get('test:compat')
assert record.valid_time == 1717612800.0
assert record.transaction_time == 1717612800.0
assert record.last_seen == 1717612800.0   # property alias
```

This is covered by `test_backward_compat_old_record_loads` in `tests/test_bitemporal_scoring.py`.
