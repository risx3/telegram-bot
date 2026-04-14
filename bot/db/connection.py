"""asyncpg connection pool setup."""

import asyncpg
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None


async def create_pool() -> asyncpg.Pool:
    """Create and return a global asyncpg connection pool."""
    global _pool
    database_url = os.environ["DATABASE_URL"]
    # asyncpg expects postgresql:// not postgres://
    database_url = database_url.replace("postgres://", "postgresql://", 1)
    _pool = await asyncpg.create_pool(
        dsn=database_url,
        min_size=2,
        max_size=10,
        command_timeout=30,
    )
    logger.info("PostgreSQL connection pool created.")
    return _pool


async def close_pool() -> None:
    """Close the global connection pool gracefully."""
    global _pool
    if _pool:
        await _pool.close()
        logger.info("PostgreSQL connection pool closed.")
        _pool = None


def get_pool() -> asyncpg.Pool:
    """Return the active pool; raises if not initialised."""
    if _pool is None:
        raise RuntimeError("Database pool is not initialised. Call create_pool() first.")
    return _pool
