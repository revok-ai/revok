"""SQLite product pricing database for the crewai_pricing demo.

All table/column names and seed values come from environment variables.
No hardcoded business data.
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

DB_PATH: str = os.getenv("DB_PATH", "pricing_demo.db")
PRODUCT_NAME: str = os.getenv("DB_PRODUCT_NAME", "Redis Enterprise")
SEED_PRICE: float = float(os.getenv("DB_SEED_PRICE", "500"))

TABLE: str = "products"
COL_ID: str = "id"
COL_NAME: str = "name"
COL_PRICE: str = "price"
COL_UPDATED: str = "updated_at"


# ---------------------------------------------------------------------------
# Public async API
# ---------------------------------------------------------------------------


async def init_db() -> None:
    """Create the products table and seed the configured product row if absent."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {TABLE} (
                {COL_ID}      INTEGER PRIMARY KEY AUTOINCREMENT,
                {COL_NAME}    TEXT    UNIQUE NOT NULL,
                {COL_PRICE}   REAL    NOT NULL,
                {COL_UPDATED} TEXT    NOT NULL
            )
            """
        )
        await db.execute(
            f"""
            INSERT OR IGNORE INTO {TABLE} ({COL_NAME}, {COL_PRICE}, {COL_UPDATED})
            VALUES (?, ?, ?)
            """,
            (PRODUCT_NAME, SEED_PRICE, _now_iso()),
        )
        await db.commit()
    _log.info(
        "Database ready at %s  product=%r  seed_price=%.2f",
        DB_PATH,
        PRODUCT_NAME,
        SEED_PRICE,
    )


async def get_price(product_name: str) -> dict[str, Any] | None:
    """Return the current price record for *product_name*, or ``None`` if not found.

    Args:
        product_name: The exact product name as stored in the database.

    Returns:
        Dict with keys ``name``, ``price``, ``updated_at``, or ``None``.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            f"SELECT {COL_NAME}, {COL_PRICE}, {COL_UPDATED}"
            f"  FROM {TABLE}"
            f" WHERE {COL_NAME} = ?",
            (product_name,),
        ) as cur:
            row = await cur.fetchone()
    return dict(row) if row is not None else None


async def update_price(product_name: str, new_price: float) -> dict[str, Any]:
    """Update the price for *product_name* and return the updated record.

    Args:
        product_name: The exact product name as stored in the database.
        new_price:    New price value (must be positive).

    Returns:
        Dict with keys ``name``, ``price``, ``updated_at``.
    """
    now = _now_iso()
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE {TABLE} SET {COL_PRICE} = ?, {COL_UPDATED} = ? WHERE {COL_NAME} = ?",
            (new_price, now, product_name),
        )
        await db.commit()
    _log.info("Updated price for %r → %.2f", product_name, new_price)
    return {COL_NAME: product_name, COL_PRICE: new_price, COL_UPDATED: now}


async def reset_to_seed() -> dict[str, Any]:
    """Reset the configured product to its seed price.

    Returns:
        Updated record dict.
    """
    return await update_price(PRODUCT_NAME, SEED_PRICE)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()
