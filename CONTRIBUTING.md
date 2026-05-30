# Contributing to Revok

## Welcome

Revok is the memory validity layer for AI agents — a transparent async HTTP
proxy that intercepts agent memory writes, scores named entities with
exponential decay, and injects confidence metadata at retrieval time.

We welcome contributions that strengthen the OSS core: new memory adapters,
signal normalizers, entity matching improvements, scoring enhancements, tests,
and documentation. If you are unsure whether your change belongs in this repo
or in the enterprise tier, read the OSS boundary section below before opening
a PR.

---

## OSS boundary

Revok is AGPL v3. The OSS repo contains the core proxy pipeline and the
interfaces that enterprise builds on. Anything that creates vendor lock-in or
belongs to the commercial tier must not be merged here.

### Forbidden imports

The following packages must never appear in `revok/` or `tests/`:

| Package | Reason |
|---------|--------|
| `azure` / `azure-*` | Managed cloud SDK — enterprise tier only |
| `boto3` / `botocore` | Managed cloud SDK — enterprise tier only |
| `google.cloud` | Managed cloud SDK — enterprise tier only |
| `confluent_kafka` | Managed cloud SDK — enterprise tier only |
| `requests` | Sync I/O — all network I/O must use `aiohttp` |
| `spacy` | Heavyweight NLP — OSS uses `re` only |
| `rapidfuzz` | Third-party fuzzy match — OSS uses stdlib only |
| `redis` | External cache — OSS uses Python `dict` + SQLite WAL |

### Forbidden features

The following capabilities belong in the enterprise tier and must not be
implemented in this repo:

- Multi-tenancy (per-tenant isolation, tenant routing)
- SSO / RBAC / user authentication of any kind
- Audit logging to external systems
- SaaS dashboard or hosted UI
- Advanced scoring algorithms beyond exponential decay
- Managed cloud signal sources (Azure Event Hubs, AWS EventBridge, Google Pub/Sub)

OSS signal sources are webhooks and Redis Streams (self-hosted, no Confluent SDK).

If your feature touches any of the above, open an issue to discuss before
writing code.

---

## How to contribute

1. **Fork** the repository and create a branch from `main`:
   ```
   git checkout -b feat/your-feature-name
   ```
   Use prefixes: `feat/`, `fix/`, `test/`, `docs/`, `refactor/`.

2. **Install** dependencies:
   ```
   pip install -e ".[dev]"
   ```

3. **Write code** — see code standards below.

4. **Run the full check suite** locally before pushing:
   ```
   pytest
   ruff check revok/ tests/
   mypy revok/
   ```
   All three must pass with zero errors.

5. **Open a Pull Request** against `main`. Fill in the PR checklist (below).
   PRs without a completed checklist will not be reviewed.

6. **Address review feedback.** Maintainers may request changes; please
   respond within 14 days or the PR may be closed.

For significant changes — new adapters, new signal sources, changes to
`interfaces.py` — open an issue first so the design can be discussed before
implementation work begins.

---

## Code standards

### Type hints

Every public function and method must carry a fully type-annotated signature.
Use `from __future__ import annotations` at the top of every module.

```python
# Good
def compute_score(base: float, elapsed: float, half_life: float) -> float:
    ...

# Bad — missing return type, missing parameter type
def compute_score(base, elapsed, half_life):
    ...
```

### Docstrings

Every public function, method, and class must have a docstring describing its
purpose, parameters, and return value. One-liners are fine for simple helpers.

```python
def compute_score(base: float, elapsed: float, half_life: float) -> float:
    """Return the exponentially decayed score.

    Args:
        base: Starting score in [0.0, 1.0].
        elapsed: Seconds since the score was set.
        half_life: Seconds for the score to halve.

    Returns:
        Decayed score in [0.0, base].
    """
```

### Tests

- One test module per source module: `tests/test_<module>.py`.
- Every new public function must have at least one passing test.
- Use `pytest-asyncio` (`asyncio_mode = "auto"`) for async tests.
- No `print` statements anywhere — use `logging` only.
- Tests must not make real network calls; mock or use in-process fakes.

### AGPL v3 license header

Every `.py` file in `revok/` and `tests/` must start with:

```python
# SPDX-License-Identifier: AGPL-3.0-or-later
```

### Async I/O

All network and file I/O must be async. Use `aiohttp` for HTTP and
`aiosqlite` for SQLite. Never use `requests`, `urllib`, or synchronous file
reads inside the proxy pipeline.

### Linting and type checking

The project uses `ruff` for linting and `mypy` for static type checking,
both configured in `pyproject.toml`. Both must pass with zero errors or
warnings. Do not add `# noqa` or `# type: ignore` suppressions without an
accompanying comment explaining why.

---

## PR checklist

Copy this checklist into your PR description and check every item before
requesting review.

```
### OSS boundary check
- [ ] No `azure`, `boto3`, `botocore`, `google.cloud`, or `confluent_kafka` imports added
- [ ] No `requests` imports added (async I/O uses `aiohttp` only)
- [ ] No `spacy`, `rapidfuzz`, or `redis` imports added
- [ ] No multi-tenancy, SSO, RBAC, audit logging, or SaaS dashboard code added
- [ ] No managed cloud signal source implementations added

### Code quality
- [ ] Every new public function/method has a type-annotated signature
- [ ] Every new public function/method has a docstring
- [ ] New `.py` files in `revok/` and `tests/` include the AGPL-3.0-or-later SPDX header
- [ ] `pytest` passes with zero failures
- [ ] `ruff check revok/ tests/` passes with zero errors
- [ ] `mypy revok/` passes with zero errors
- [ ] No `print` statements introduced (use `logging`)

### Tests
- [ ] New functionality is covered by at least one test
- [ ] Tests do not make real network calls

### General
- [ ] PR targets `main`
- [ ] Branch name uses a valid prefix (`feat/`, `fix/`, `test/`, `docs/`, `refactor/`)
- [ ] Commit messages are descriptive
```

