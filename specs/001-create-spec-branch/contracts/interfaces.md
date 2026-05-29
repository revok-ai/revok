# Contract: Protocol Interfaces

**Module**: `revok/interfaces.py` | **Version**: 0.1.0

All pluggable components must implement one of these Protocol interfaces. Concrete implementations are registered via config, not hardcoded. Interfaces are defined BEFORE any implementation module is written (FR-002, Constitution § III).

---

## SignalSource

A source that delivers raw memory signals to the Revok pipeline.

```python
from typing import Protocol, AsyncIterator
from revok.models import Signal

class SignalSource(Protocol):
    async def receive(self) -> AsyncIterator[Signal]:
        """Yield incoming signals one at a time.

        Yields:
            Signal: The next incoming signal from this source.

        The iterator should run until the source is exhausted or the
        coroutine is cancelled. Implementations must not block the event loop.
        """
        ...

    async def close(self) -> None:
        """Release any resources held by this source.

        Must be idempotent — safe to call multiple times.
        """
        ...
```

**MVP Implementation**: `revok.proxy.AiohttpSignalSource` — receives signals via the aiohttp web server.

---

## MessageBus

An async channel that carries signals between pipeline stages.

```python
from typing import Protocol
from revok.models import Signal

class MessageBus(Protocol):
    async def publish(self, signal: Signal) -> None:
        """Place a signal onto the bus for downstream consumption.

        Args:
            signal: The signal to publish.

        Must not block the event loop. Must not raise on a full queue;
        implementations should apply backpressure or drop with logging.
        """
        ...

    async def consume(self) -> Signal:
        """Wait for and return the next signal from the bus.

        Returns:
            Signal: The next signal available for processing.

        Blocks until a signal is available. Must be cancellation-safe.
        """
        ...

    async def close(self) -> None:
        """Drain the bus and release resources.

        Must be idempotent.
        """
        ...
```

**MVP Implementation**: `revok.signal_queue.AsyncioQueueBus` — backed by `asyncio.Queue`.

---

## StateStore

Persistent + cached storage for EntityRecord state.

```python
from typing import Protocol
from revok.models import EntityRecord

class StateStore(Protocol):
    async def get(self, entity_key: str) -> EntityRecord | None:
        """Fetch the current record for an entity key.

        Args:
            entity_key: Normalized entity identifier.

        Returns:
            The EntityRecord if it exists, None otherwise.

        Must check the hot layer first, then fall back to the durable store.
        """
        ...

    async def put(self, record: EntityRecord) -> None:
        """Persist an entity record, replacing any existing record for the same key.

        Args:
            record: The EntityRecord to persist.

        Must write to the durable store AND update the hot layer atomically
        from the caller's perspective (hot layer write must not be observable
        before the durable write completes).
        """
        ...

    async def close(self) -> None:
        """Flush pending writes and release resources.

        Must be idempotent.
        """
        ...
```

**MVP Implementation**: `revok.state_store.SqliteStateStore` — SQLite WAL + `OrderedDict` LRU hot layer.

---

## MemoryAdapter

An upstream memory store that Revok proxies writes and reads to.

```python
from typing import Protocol
from revok.models import EnrichedPayload, MemoryAdapterResponse, Signal

class MemoryAdapter(Protocol):
    async def write(self, payload: EnrichedPayload) -> MemoryAdapterResponse:
        """Forward an enriched memory-write payload to the upstream store.

        Args:
            payload: The original signal body merged with x_revok metadata.

        Returns:
            The response from the upstream store.

        Must not modify payload before forwarding. Must not raise on upstream
        HTTP errors — return the error status in MemoryAdapterResponse instead.
        """
        ...

    async def forward(self, signal: Signal) -> MemoryAdapterResponse:
        """Forward a non-write (read/delete) signal to the upstream store unchanged.

        Args:
            signal: The original signal with all headers and body intact.

        Returns:
            The unmodified response from the upstream store.
        """
        ...

    async def close(self) -> None:
        """Close the upstream connection and release resources.

        Must be idempotent.
        """
        ...
```

**MVP Implementation**: `revok.proxy.Mem0Adapter` — uses `aiohttp.ClientSession` to forward to the configured Mem0 HTTP endpoint.

---

## Implementation Rules

- All Protocol classes live exclusively in `revok/interfaces.py`.
- All concrete implementations import from `revok/interfaces.py` and declare `class FooImpl(FooProtocol):` (structural subtyping via `Protocol` — no registration needed).
- No module other than `interfaces.py` defines Protocol classes.
- Future adapters (Zep, Redis Agent Memory Server) must implement `MemoryAdapter` without modifying `interfaces.py`.
