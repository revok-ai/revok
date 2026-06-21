# Data Model: Bitemporal Scoring (Feature 002)

## Overview

This document describes all data model changes required for FR-001 through FR-008.
The key change is promoting `EntityRecord.last_seen` from a stored field to a
read-only `@property` alias, while introducing `valid_time` and `transaction_time`
as the authoritative timestamp fields.

---

## EntityRecord

### Before (v0.1.x)

```python
@dataclass
class EntityRecord:
    entity_key: str
    score: float
    last_seen: float         # stored — Unix epoch seconds of most recent signal
    signal_count: int
    pattern_name: str
```

### After (v0.2.0)

```python
@dataclass
class EntityRecord:
    entity_key: str
    score: float
    valid_time: float        # stored — time the event actually occurred (event time)
    transaction_time: float  # stored — time the record was written to the store (wall clock)
    signal_count: int
    pattern_name: str

    @property
    def last_seen(self) -> float:
        """Backward-compat alias — returns valid_time.

        Caller contract: transaction_time >= valid_time.
        Future-dated valid_time values are clamped to transaction_time at
        write time (metadata_writer.enrich). No runtime check enforced here.
        """
        return self.valid_time
```

### Field Semantics

| Field | Type | Description |
|-------|------|-------------|
| `entity_key` | `str` | Normalized entity identifier (unchanged) |
| `score` | `float` | Current confidence score after decay (unchanged) |
| `valid_time` | `float` | Unix epoch seconds when the signal event actually occurred |
| `transaction_time` | `float` | Unix epoch seconds when the record was written |
| `signal_count` | `int` | Cumulative number of signals for this entity (unchanged) |
| `pattern_name` | `str` | Name of the matching pattern (unchanged) |
| `last_seen` | `float` | **Read-only property** — returns `valid_time`; backward-compat alias |

### Constructor Migration

Old:
```python
EntityRecord(entity_key=k, score=s, last_seen=t, signal_count=n, pattern_name=p)
```

New:
```python
EntityRecord(entity_key=k, score=s, valid_time=t, transaction_time=now, signal_count=n, pattern_name=p)
```

---

## Signal

### Before (v0.1.x)

```python
@dataclass(frozen=True)
class Signal:
    raw_content: str
    source_id: str
    timestamp: float
    http_method: str
    http_path: str
    original_body: bytes
    headers: dict[str, str]
```

### After (v0.2.0)

```python
@dataclass(frozen=True)
class Signal:
    raw_content: str
    source_id: str
    timestamp: float
    http_method: str
    http_path: str
    original_body: bytes
    headers: dict[str, str]
    valid_time: float | None = None   # Transport of event-time; always None in v0.2.0
```

### Field Semantics

| Field | Type | Description |
|-------|------|-------------|
| `valid_time` | `float \| None` | Override for event time; `None` means use `signal.timestamp` |

---

## SQLite Schema

### Before (v0.1.x)

```sql
CREATE TABLE IF NOT EXISTS entity_records (
    entity_key   TEXT    PRIMARY KEY,
    score        REAL    NOT NULL DEFAULT 0.0,
    last_seen    REAL    NOT NULL,
    signal_count INTEGER NOT NULL DEFAULT 1,
    pattern_name TEXT    NOT NULL DEFAULT ''
);
```

### After (v0.2.0 DDL for new databases)

```sql
CREATE TABLE IF NOT EXISTS entity_records (
    entity_key       TEXT    PRIMARY KEY,
    score            REAL    NOT NULL DEFAULT 0.0,
    last_seen        REAL    NOT NULL,
    valid_time       REAL,
    transaction_time REAL,
    signal_count     INTEGER NOT NULL DEFAULT 1,
    pattern_name     TEXT    NOT NULL DEFAULT ''
);
```

> `last_seen` column retained for backward compatibility. New writes still populate
> it (set to `valid_time`) to keep old software readable.

### Migration (existing databases)

Run at `SqliteStateStore.open()` — idempotent:

```python
for col in ("valid_time", "transaction_time"):
    try:
        await self._db.execute(
            f"ALTER TABLE entity_records ADD COLUMN {col} REAL"
        )
        await self._db.commit()
    except aiosqlite.OperationalError:
        pass  # column already exists
```

### Backward Compatibility on Load

When reading a row where `valid_time IS NULL` (old row):

```python
raw_valid_time = row["valid_time"]
raw_transaction_time = row["transaction_time"]
legacy_last_seen = row["last_seen"]

valid_time = raw_valid_time if raw_valid_time is not None else legacy_last_seen
transaction_time = raw_transaction_time if raw_transaction_time is not None else legacy_last_seen
```

---

## Scoring Engine Invariant

After this change, `ScoringEngine.score()` and `ScoringEngine.decay_at()` use
`valid_time` for `delta_t` calculation — meaning decay reflects **event time**,
not when the record was written.

```python
# Before:
delta_t = max(0.0, now - existing.last_seen)

# After:
delta_t = max(0.0, now - existing.valid_time)
```

---

## State Transitions

```
Signal arrives
    │
    ├─ signal.valid_time is not None? → raw_vt = signal.valid_time
    └─ signal.valid_time is None?     → raw_vt = signal.timestamp (= now for live signals)
    │
    ├─ raw_vt > now (future)?
    │   ├─ logger.WARNING(...)
    │   └─ raw_vt = now  (clamp)
    │
    ▼
EntityRecord(
    valid_time = raw_vt,
    transaction_time = now,   # always wall clock
    ...
)
    │
    ▼
SQLite + hot layer
```
