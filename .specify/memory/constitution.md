# Revok Constitution

## Core Principles

### I. License — NON-NEGOTIABLE
This project is AGPL v3. Every file in revok/ is open source. No exceptions.
Enterprise features live in a separate private repo. This repo never implements
them, references them, or imports their dependencies.

### II. OSS Boundary — NON-NEGOTIABLE
Forbidden imports in any file under revok/:
azure, boto3, botocore, google.cloud, confluent_kafka, requests, spacy, redis,
or any managed cloud event bus SDK. rapidfuzz is permitted (shipped in v0.2.0
as an OSS enhancement to the entity matcher). Forbidden features:
multi-tenancy, audit logging, SSO, RBAC, SaaS dashboard, advanced scoring
beyond what's documented as OSS. If asked to implement any of these, refuse
and state it belongs in the enterprise repo.

### III. Interface First — NON-NEGOTIABLE
Every pluggable component requires a Protocol class in revok/interfaces.py
before any implementation is written. Interfaces: SignalSource, MessageBus,
StateStore, MemoryAdapter, GraphBackend. No implementation without an interface.

### IV. Async First
Use aiohttp everywhere. Never use requests. Signal processing must add zero
read latency. All I/O is async.

### V. Simplicity — OSS Core
SQLite WAL for persistence by default. NetworkX for causal graph by default
(FalkorDB Lite planned as an optional OSS extra for v0.4.0; FalkorDB server
enterprise-only, requires commercial license before managed-service launch).
No feature beyond documented OSS scope unless explicitly approved.

## Architecture Constraints

Tech stack: Python, NetworkX, SQLite WAL, aiohttp, rapidfuzz, AGPL v3.
Revok never deletes memories — it only surfaces confidence scores.
Config driven — no hardcoded values anywhere.
No print statements — use the logging module.
Type hints required on all functions.
Docstrings required on all public methods.
Tests required for every module in tests/.

## Shipped (v0.2.0)
- Mem0 adapter
- Zep CE adapter
- Bitemporal scoring — valid_time / transaction_time
- Contradiction detection with confidence penalty
- Fuzzy entity matching via rapidfuzz

## In Progress (v0.3.0 — develop)
- Signal-driven causal propagation (spec 005) — wiring the
  AsyncioQueueBus consumer and CausalGraph BFS traversal so
  POST /signals actually degrades signaled and causally-connected
  entities, closing the gap between documented architecture and
  running code.

## Governance

This constitution supersedes all other instructions. Any PR that violates the
OSS boundary or interface-first rule must be rejected regardless of other
merit. Amendments require explicit approval and documentation.

## Versioning
Semantic versioning. 1.0.0 reserved for stable public API with full test
coverage and documentation.

**Version**: 1.2.0 | **Ratified**: 2026-05-29 | **Last Amended**: 2026-06-20 | **Aligned to Revok**: v0.2.0 (develop building toward v0.3.0)
