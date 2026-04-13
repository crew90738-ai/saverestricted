"""
handlers/__init__.py — Handler registration hub.

Call register_handlers(bot, user, redis) from main.py to wire up every
command and message handler in one call.

Handler priority (Pyrogram groups)
────────────────────────────────────
  Group -1  : admin commands (owner-only, checked first)
  Group  0  : start / help / thumbnail / forward (default)
  Group  1  : yt-dlp URL handler (lower priority than Telegram links)
  Group  2  : batch state-machine interceptor
"""

from __future__ import annotations

import logging

import redis.asyncio as aioredis
from pyrogram import Client

from .admin        import register as register_admin
from .batch        import register as register_batch
from .forward      import register as register_forward
from .start        import register as register_start
from .thumbnail    import register as register_thumbnail
from .yt_dlp_handler import register as register_ytdlp

logger = logging.getLogger(__name__)


def register_handlers(bot: Client, user: Client, redis: aioredis.Redis) -> None:
    """Wire up all handlers.  Order matters only for same-group handlers."""

    logger.info("Registering handlers…")

    # Owner-only admin commands (group -1 = highest priority)
    register_admin(bot, user, redis)
    logger.info("  ✓ admin")

    # /start, /help, caption commands, force-sub callbacks
    register_start(bot, user, redis)
    logger.info("  ✓ start/help")

    # /setthumb, /delthumb, /showthumb
    register_thumbnail(bot, user, redis)
    logger.info("  ✓ thumbnail")

    # Core Telegram link forwarder (group 0)
    register_forward(bot, user, redis)
    logger.info("  ✓ forward")

    # yt-dlp URL downloader (group 1 — after Telegram link check)
    register_ytdlp(bot, user, redis)
    logger.info("  ✓ yt-dlp")

    # Batch state-machine interceptor (group 2 — needs to see messages
    # that forward.py already skipped because they're not Telegram links
    # but ARE part of an active batch flow)
    register_batch(bot, user, redis)
    logger.info("  ✓ batch")

    logger.info("All handlers registered ✓")


__all__ = ["register_handlers"]
