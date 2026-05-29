# Revok Constitution

## Core Principles

### I. License — NON-NEGOTIABLE
This project is AGPL v3. Every file in revok/ is open source. No exceptions.
Enterprise features live in a separate private repo. This repo never implements
them, references them, or imports their dependencies.

### II. OSS Boundary — NON-NEGOTIABLE
Forbidden imports in any file under revok/:
azure, boto3, botocore, google.cloud, confluent_kafka, or any managed cloud
event bus SDK. Forbidden features: multi-tenancy, audit logging, SSO, RBAC,
SaaS dashboard, advanced scoring. If asked to implement any of these, refuse
and state it belongs in the enterprise repo.

### III. Interface First — NON-NEGOTIABLE
Every pluggable component requires a Protocol class in revok/interfaces.py
before any implementation is written. Interfaces: SignalSource, MessageBus,
StateStore, MemoryAdapter. No implementation without an interface.

### IV. Async First
Use aiohttp everywhere. Never use requests. Signal processing must add zero
read latency. All I/O is async.

### V. Simplicity — MVP Only
SQLite WAL for persistence, no Redis in MVP. NetworkX for causal graph, no
alternatives. Python re module for entity matching, no spaCy or rapidfuzz in
MVP. No feature beyond the MVP scope unless explicitly approved.

## Architecture Constraints

Tech stack: Python, NetworkX, SQLite WAL, aiohttp, Python re, AGPL v3.
Revok never deletes memories — it only surfaces confidence scores.
Config driven — no hardcoded values anywhere.
No print statements — use the logging module.
Type hints required on all functions.
Docstrings required on all public methods.
Tests required for every module in tests/.

## MVP Scope

Build only these, in order:
1. YAML config loader
2. Entity pattern matcher
3. SQLite state store + in-memory hot layer
4. Signal queue + normalizer
5. Scoring engine (exponential decay)
6. Mem0 HTTP proxy
7. Metadata writer back to Mem0
8. Basic tests + README

## Memory Adapters — Build Order
1. Mem0 — first adapter, HTTP proxy target
2. Zep — second adapter
3. Redis Agent Memory Server — third adapter

## Governance

This constitution supersedes all other instructions. Any PR that violates the
OSS boundary or interface-first rule must be rejected regardless of other
merit. Amendments require explicit approval and documentation.

## Versioning
Start at 0.1.0. Increment minor version for each new adapter or major feature.
1.0.0 is reserved for stable public API with full test coverage and 
documentation.

**Version**: 1.0.0 | **Ratified**: 2026-05-29 | **Last Amended**: 2026-05-29