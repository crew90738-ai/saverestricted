"""
utils/redis_helper.py — All Redis read/write logic in one place.

Key schema
──────────
premium_users          SET     → user IDs of premium members
bot_users              SET     → every user who has ever started the bot
user_thumb:{uid}       STRING  → Telegram file_id of custom thumbnail
user_caption:{uid}     STRING  → custom caption template
batch_state:{uid}      HASH    → batch-download state machine fields
stats:total_users      STRING  → int counter
stats:total_downloads  STRING  → int counter
stats:total_files      STRING  → int counter
"""

from __future__ import annotations

import json
import logging
from typing import Optional

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

# ─── module-level handle set by main.py ──────────────────────────────────────
_redis: aioredis.Redis | None = None


def set_redis(r: aioredis.Redis) -> None:
    """Called once from main.py after the Redis pool is created."""
    global _redis
    _redis = r


def get_redis() -> aioredis.Redis:
    if _redis is None:
        raise RuntimeError("Redis has not been initialised yet.")
    return _redis


# ─── Premium helpers ──────────────────────────────────────────────────────────

async def add_premium(user_id: int) -> None:
    await get_redis().sadd("premium_users", str(user_id))
    logger.info("Added premium: %d", user_id)


async def remove_premium(user_id: int) -> None:
    await get_redis().srem("premium_users", str(user_id))
    logger.info("Removed premium: %d", user_id)


async def is_premium(user_id: int) -> bool:
    return await get_redis().sismember("premium_users", str(user_id))


async def get_all_premium() -> list[int]:
    members = await get_redis().smembers("premium_users")
    return [int(m) for m in members]


# ─── User tracking ────────────────────────────────────────────────────────────

async def register_user(user_id: int) -> bool:
    """Add user to the global set.  Returns True if this is a new user."""
    r = get_redis()
    added = await r.sadd("bot_users", str(user_id))
    if added:
        await r.incr("stats:total_users")
    return bool(added)


async def get_all_users() -> list[int]:
    members = await get_redis().smembers("bot_users")
    return [int(m) for m in members]


async def total_users() -> int:
    return await get_redis().scard("bot_users")


# ─── Stats ────────────────────────────────────────────────────────────────────

async def inc_downloads(n: int = 1) -> None:
    await get_redis().incr("stats:total_downloads")


async def inc_files(n: int = 1) -> None:
    await get_redis().incrby("stats:total_files", n)


async def get_stats() -> dict:
    r = get_redis()
    users     = await r.scard("bot_users")
    downloads = int(await r.get("stats:total_downloads") or 0)
    files     = int(await r.get("stats:total_files") or 0)
    premium   = await r.scard("premium_users")
    return {
        "users":     users,
        "downloads": downloads,
        "files":     files,
        "premium":   premium,
    }


# ─── Custom thumbnail ─────────────────────────────────────────────────────────

async def set_thumb(user_id: int, file_id: str) -> None:
    await get_redis().set(f"user_thumb:{user_id}", file_id)


async def get_thumb(user_id: int) -> Optional[str]:
    return await get_redis().get(f"user_thumb:{user_id}")


async def del_thumb(user_id: int) -> None:
    await get_redis().delete(f"user_thumb:{user_id}")


# ─── Custom caption ───────────────────────────────────────────────────────────

async def set_caption(user_id: int, caption: str) -> None:
    await get_redis().set(f"user_caption:{user_id}", caption)


async def get_caption(user_id: int) -> Optional[str]:
    return await get_redis().get(f"user_caption:{user_id}")


async def del_caption(user_id: int) -> None:
    await get_redis().delete(f"user_caption:{user_id}")


# ─── Batch state machine ──────────────────────────────────────────────────────

async def set_batch_state(user_id: int, state: dict) -> None:
    """Persist batch-download state as JSON with a 10-minute TTL."""
    await get_redis().set(
        f"batch_state:{user_id}",
        json.dumps(state),
        ex=600,          # expire after 10 min of inactivity
    )


async def get_batch_state(user_id: int) -> Optional[dict]:
    raw = await get_redis().get(f"batch_state:{user_id}")
    return json.loads(raw) if raw else None


async def clear_batch_state(user_id: int) -> None:
    await get_redis().delete(f"batch_state:{user_id}")
