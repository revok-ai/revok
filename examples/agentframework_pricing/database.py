"""SQLite subscription state database for the agentframework_pricing demo.

Stores the current subscription profile for one customer account.
All column names and seed values are configurable via environment variables.
No hardcoded business data outside the seed defaults.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import aiosqlite

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config (from environment — all overridable)
# ---------------------------------------------------------------------------

DB_PATH: str = os.getenv("DB_PATH", "subscription_demo.db")
CUSTOMER_ID: str = os.getenv("DB_CUSTOMER_ID", "acme-corp")

# Enterprise tier seed values (initial state)
SEED_TIER: str = os.getenv("DB_SEED_TIER", "Enterprise")
SEED_SEATS: int = int(os.getenv("DB_SEED_SEATS", "50"))
SEED_FEATURES: str = os.getenv(
    "DB_SEED_FEATURES",
    "Advanced Analytics, SSO, Priority API Access, Custom Integrations",
)
SEED_API_RATE: str = os.getenv("DB_SEED_API_RATE", "1,000,000 requests/month")
SEED_BILLING: str = os.getenv(
    "DB_SEED_BILLING", "Annual contract, locked rate through 2027-06-01"
)

# Starter tier downgrade values
STARTER_TIER: str = os.getenv("DB_STARTER_TIER", "Starter")
STARTER_SEATS: int = int(os.getenv("DB_STARTER_SEATS", "5"))
STARTER_FEATURES: str = os.getenv("DB_STARTER_FEATURES", "Basic Analytics only")
STARTER_API_RATE: str = os.getenv("DB_STARTER_API_RATE", "10,000 requests/month")
STARTER_BILLING: str = os.getenv("DB_STARTER_BILLING", "Month-to-month, standard rate")

TABLE: str = "subscription_state"
COL_CUSTOMER_ID: str = "customer_id"
COL_TIER: str = "subscription_tier"
COL_SEATS: str = "seat_limit"
COL_FEATURES: str = "feature_entitlements"
COL_API_RATE: str = "api_rate_limit"
COL_BILLING: str = "billing_terms"
COL_UPDATED: str = "updated_at"


# ---------------------------------------------------------------------------
# Public async API
# ---------------------------------------------------------------------------


async def init_db() -> None:
    """Create the subscription_state table and seed the customer row if absent."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                {COL_CUSTOMER_ID}  TEXT PRIMARY KEY NOT NULL,
                {COL_TIER}         TEXT NOT NULL,
                {COL_SEATS}        INTEGER NOT NULL,
                {COL_FEATURES}     TEXT NOT NULL,
                {COL_API_RATE}     TEXT NOT NULL,
                {COL_BILLING}      TEXT NOT NULL,
                {COL_UPDATED}      TEXT NOT NULL
            )
            """
        )
        now = _now_iso()
        await db.execute(
            f"""
            INSERT OR IGNORE INTO {TABLE}
                ({COL_CUSTOMER_ID}, {COL_TIER}, {COL_SEATS}, {COL_FEATURES},
                 {COL_API_RATE}, {COL_BILLING}, {COL_UPDATED})
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                CUSTOMER_ID,
                SEED_TIER,
                SEED_SEATS,
                SEED_FEATURES,
                SEED_API_RATE,
                SEED_BILLING,
                now,
            ),
        )
        await db.commit()
    _log.info(
        "Database ready at %s  customer=%r  tier=%r",
        DB_PATH,
        CUSTOMER_ID,
        SEED_TIER,
    )


async def get_subscription() -> dict[str, Any] | None:
    """Return the current subscription profile for the customer, or ``None`` if not found.

    Returns:
        Dict with keys ``customer_id``, ``subscription_tier``, ``seat_limit``,
        ``feature_entitlements``, ``api_rate_limit``, ``billing_terms``,
        ``updated_at``.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT * FROM {TABLE} WHERE {COL_CUSTOMER_ID} = ?",
            (CUSTOMER_ID,),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row is not None else None


async def downgrade_to_starter() -> dict[str, Any]:
    """Downgrade the customer subscription to Starter tier.

    Updates all five subscription fields atomically to Starter-tier values.
    This simulates a billing system webhook that changes the customer's plan
    without the agent being notified.

    Returns:
        Updated subscription record dict.
    """
    now = _now_iso()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"""
            UPDATE {TABLE}
               SET {COL_TIER}     = ?,
                   {COL_SEATS}    = ?,
                   {COL_FEATURES} = ?,
                   {COL_API_RATE} = ?,
                   {COL_BILLING}  = ?,
                   {COL_UPDATED}  = ?
             WHERE {COL_CUSTOMER_ID} = ?
            """,
            (
                STARTER_TIER,
                STARTER_SEATS,
                STARTER_FEATURES,
                STARTER_API_RATE,
                STARTER_BILLING,
                now,
                CUSTOMER_ID,
            ),
        )
        await db.commit()
    _log.info("Downgraded customer %r to Starter tier", CUSTOMER_ID)
    return {
        COL_CUSTOMER_ID: CUSTOMER_ID,
        COL_TIER: STARTER_TIER,
        COL_SEATS: STARTER_SEATS,
        COL_FEATURES: STARTER_FEATURES,
        COL_API_RATE: STARTER_API_RATE,
        COL_BILLING: STARTER_BILLING,
        COL_UPDATED: now,
    }


async def reset_to_enterprise() -> dict[str, Any]:
    """Reset the customer subscription to Enterprise seed values.

    Returns:
        Reset subscription record dict.
    """
    now = _now_iso()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"""
            UPDATE {TABLE}
               SET {COL_TIER}     = ?,
                   {COL_SEATS}    = ?,
                   {COL_FEATURES} = ?,
                   {COL_API_RATE} = ?,
                   {COL_BILLING}  = ?,
                   {COL_UPDATED}  = ?
             WHERE {COL_CUSTOMER_ID} = ?
            """,
            (
                SEED_TIER,
                SEED_SEATS,
                SEED_FEATURES,
                SEED_API_RATE,
                SEED_BILLING,
                now,
                CUSTOMER_ID,
            ),
        )
        await db.commit()
    _log.info("Reset customer %r to Enterprise tier", CUSTOMER_ID)
    return {
        COL_CUSTOMER_ID: CUSTOMER_ID,
        COL_TIER: SEED_TIER,
        COL_SEATS: SEED_SEATS,
        COL_FEATURES: SEED_FEATURES,
        COL_API_RATE: SEED_API_RATE,
        COL_BILLING: SEED_BILLING,
        COL_UPDATED: now,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
