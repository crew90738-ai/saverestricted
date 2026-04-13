"""
main.py — Entry point for the Save Restricted Content Bot.

Responsibilities
────────────────
1. Start the async health-check web server (required by Render Web Service).
2. Connect to Redis.
3. Initialise both Pyrogram clients (bot + user session).
4. Register all handlers.
5. Keep everything running until a signal is received.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal

from aiohttp import web
from pyrogram import Client
from pyrogram.enums import ParseMode

from config import Config
from handlers import register_handlers
from utils.redis_helper import set_redis

import redis.asyncio as aioredis

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# Quiet down overly chatty third-party loggers
logging.getLogger("pyrogram").setLevel(logging.WARNING)
logging.getLogger("aiohttp").setLevel(logging.WARNING)
logging.getLogger("yt_dlp").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# ─── Ensure download directory exists ────────────────────────────────────────
os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)

# ─── Pyrogram clients ─────────────────────────────────────────────────────────
# Bot client — handles all incoming updates and sends replies
bot = Client(
    name=":memory:",            # no SQLite file; persists via BOT_TOKEN
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN,
    parse_mode=ParseMode.HTML,
    workers=8,
)

# User client — used to READ restricted messages that the bot cannot access
user = Client(
    name="SaveRestrictedUser",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    session_string=Config.SESSION_STRING,
    parse_mode=ParseMode.HTML,
    workers=4,
    no_updates=True,            # user client doesn't need to receive any updates
)


# ─── Health-check web server ──────────────────────────────────────────────────

async def _health(_request: web.Request) -> web.Response:
    return web.Response(text="OK", content_type="text/plain")


async def start_health_server() -> web.AppRunner:
    """
    Bind a minimal HTTP server to $PORT (default 8080).
    Render pings GET / to decide whether the service is healthy.
    """
    app = web.Application()
    app.router.add_get("/",       _health)
    app.router.add_get("/health", _health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", Config.PORT)
    await site.start()
    logger.info("Health server listening on port %d", Config.PORT)
    return runner


# ─── Main coroutine ───────────────────────────────────────────────────────────

async def main() -> None:
    # 1. Health server (must bind port before Render's health check fires)
    health_runner = await start_health_server()

    # 2. Redis
    logger.info("Connecting to Redis: %s", Config.REDIS_URL)
    redis_client = aioredis.from_url(
        Config.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
        max_connections=20,
        socket_keepalive=True,
        retry_on_timeout=True,
    )
    await redis_client.ping()
    set_redis(redis_client)
    logger.info("Redis connected ✓")

    # 3. Register handlers (must happen BEFORE clients start)
    register_handlers(bot, user, redis_client)

    # 4. Start Pyrogram clients
    logger.info("Starting Pyrogram clients…")
    await bot.start()
    await user.start()

    bot_me = await bot.get_me()
    logger.info("Bot  client: @%s (%d) ✓", bot_me.username, bot_me.id)

    user_me = await user.get_me()
    logger.info("User client: @%s (%d) ✓", user_me.username, user_me.id)

    # 5. Log startup to channel
    if Config.LOG_CHANNEL:
        try:
            await bot.send_message(
                Config.LOG_CHANNEL,
                f"🚀 <b>Bot started</b>\n"
                f"Bot:  @{bot_me.username}\n"
                f"User: @{user_me.username}",
            )
        except Exception as e:
            logger.warning("Could not log to channel: %s", e)

    logger.info("=" * 60)
    logger.info("  Bot is running.  Press Ctrl-C to stop.")
    logger.info("=" * 60)

    # 6. Wait until a stop signal arrives
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Stop signal received.")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler for all signals
            pass

    await stop_event.wait()

    # 7. Graceful shutdown
    logger.info("Shutting down…")
    await bot.stop()
    await user.stop()
    await redis_client.aclose()
    await health_runner.cleanup()
    logger.info("Goodbye 👋")


# ─── Entry ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    asyncio.run(main())
