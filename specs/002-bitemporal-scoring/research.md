# Research: Bitemporal Scoring (Feature 002)

## Status: No open unknowns — all resolved during spec clarification.

---

## Decision Log

### 1. valid_time transport in Signal
- **Decision**: `Signal.valid_time: float | None = None` — always `None` in v0.2.0
- **Rationale**: Transport mechanism deferred; keeps Signal frozen dataclass backward-compatible
- **Alternatives considered**: Mandatory `float` field (requires all callers to set it), `Header X-Revok-Valid-Time` (HTTP-specific, deferred)

### 2. last_seen alias mechanism
- **Decision**: `@property def last_seen(self) -> float: return self.valid_time` on `EntityRecord`
- **Rationale**: Preserves external callers that already read `.last_seen`; `dataclasses.asdict` skips properties so the old field disappears from serialization — correct behaviour for FR-007
- **Alternatives considered**: Keep `last_seen` as a stored field (redundancy, sync issues), remove entirely (breaks existing test code without upside)

### 3. Future-dated valid_time handling
- **Decision**: Clamp `valid_time = min(valid_time, transaction_time)` in `metadata_writer.enrich()` + `logger.warning(...)`
- **Rationale**: Prevents negative `delta_t` in scoring; warning is observable in logs; spec defers silent-drop or exception approach
- **Alternatives considered**: Raise `ValueError` (too strict, breaks integrations), silent clamp without warning (unobservable)

### 4. transaction_time ≥ valid_time invariant enforcement
- **Decision**: Caller contract documented in `EntityRecord` docstring; NOT enforced in `__post_init__`
- **Rationale**: Structural guarantee comes from write-time clamp; runtime check adds noise to test setup
- **Alternatives considered**: `__post_init__` validation (rejected in Q4 clarification)

### 5. API response field exposure
- **Decision**: Additive — `dataclasses.asdict(record)` automatically includes `valid_time` and `transaction_time`; no proxy.py change needed
- **Rationale**: FR-008 is satisfied for free once `EntityRecord` is updated
- **Alternatives considered**: Manual serialization dict (unnecessary complexity)

---

## SQLite Migration Research

**Finding**: SQLite `ALTER TABLE` does NOT support `IF NOT EXISTS`. Safe pattern:
```python
try:
    await db.execute("ALTER TABLE entity_records ADD COLUMN valid_time REAL")
    await db.commit()
except aiosqlite.OperationalError:
    pass  # "duplicate column name: valid_time" — column already exists
```
`aiosqlite.OperationalError` wraps `sqlite3.OperationalError`.

**Finding**: Column default for `REAL` in SQLite is `NULL` unless specified with `DEFAULT`. For backward compat rows, `NULL` is the correct sentinel (distinguishes "old row" from "intentionally zero").

---

## @property on mutable @dataclass

**Finding**: Python allows `@property` on non-frozen dataclasses. The property must be defined AFTER the class body's field definitions to avoid conflicts. Since `last_seen` was previously a stored `float` field, it must be **removed** from the `@dataclass` fields and added back as a `@property`.

```python
@dataclass
class EntityRecord:
    entity_key: str
    score: float
    valid_time: float
    transaction_time: float
    signal_count: int
    pattern_name: str

    @property
    def last_seen(self) -> float:
        return self.valid_time
```

`dataclasses.asdict()` only serializes `__dataclass_fields__` — properties are excluded. Verified by Python docs and CPython source.

---

## Callsite Inventory

All sites that construct `EntityRecord` directly:

| File | Line | Context |
|------|------|---------|
| `revok/metadata_writer.py` | ~115 | Main enrichment pipeline |
| `revok/state_store.py` | ~135 | `get()` — load from SQLite |
| `revok/state_store.py` | ~193 | `_apply_decay()` — score copy |
| `revok/state_store.py` | ~230 | `list_all()` — if present |
| Test files | various | See test_scoring.py, test_state_store.py, test_metadata_writer.py, test_proxy.py |

All sites that read `.last_seen`:

| File | Line | Context |
|------|------|---------|
| `revok/scoring.py` | ~95 | `score()` → `existing.last_seen` → change to `valid_time` |
| `revok/scoring.py` | ~114 | `decay_at()` → `record.last_seen` → change to `valid_time` |
| `revok/models.py` | ~131 | `to_upstream_dict()` / display — property still works |
| `revok/metadata_writer.py` | ~115 | Construction only; no read |

`revok/proxy.py`: `_handle_get_entity` uses `dataclasses.asdict(record)` — no `last_seen` read.
`last_seen` property satisfies existing external callers transparently.
