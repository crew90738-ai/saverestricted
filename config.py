"""
config.py — Centralised configuration loaded from environment variables.
Every setting has a sensible default or raises a clear error at startup.
"""

import os
from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    """Raise at startup if a mandatory env var is missing."""
    val = os.environ.get(name, "").strip()
    if not val:
        raise ValueError(f"[Config] Required environment variable '{name}' is not set.")
    return val


def _optional_int(name: str, default: int = 0) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default if default != 0 else None
    try:
        return int(raw)
    except ValueError:
        return None


class Config:
    # ── Telegram credentials ──────────────────────────────────────────────
    API_ID: int         = int(_require("API_ID"))
    API_HASH: str       = _require("API_HASH")
    BOT_TOKEN: str      = _require("BOT_TOKEN")
    SESSION_STRING: str = _require("SESSION_STRING")   # Pyrogram user session

    # ── Bot owner ─────────────────────────────────────────────────────────
    OWNER_ID: int = int(_require("OWNER_ID"))

    # ── Redis (Render Key Value / Upstash / any Redis URL) ────────────────
    REDIS_URL: str = os.environ.get("REDIS_URL", "redis://localhost:6379")

    # ── Optional features ─────────────────────────────────────────────────
    # Numeric channel ID (e.g. -1001234567890) for logging every action
    LOG_CHANNEL: int | None = _optional_int("LOG_CHANNEL")

    # Username (without @) or numeric ID of the channel users must join
    FORCE_SUB_CHANNEL: str = os.environ.get("FORCE_SUB_CHANNEL", "").strip()

    # The bot's own username without '@' (used in deep-links, help text)
    BOT_USERNAME: str = os.environ.get("BOT_USERNAME", "SaveRestrictedBot")

    # ── File / download limits ────────────────────────────────────────────
    MAX_FILE_SIZE: int = 4 * 1024 * 1024 * 1024     # 4 GB (premium session)
    DOWNLOAD_DIR: str  = os.environ.get("DOWNLOAD_DIR", "/tmp/dl")

    # ── UX tunables ───────────────────────────────────────────────────────
    # How often (seconds) the "Uploading…" progress message is edited
    PROGRESS_UPDATE_INTERVAL: int = 5

    # Seconds before a bot command-reply is auto-deleted (0 = disabled)
    AUTO_DELETE_DELAY: int = int(os.environ.get("AUTO_DELETE_DELAY", "60"))

    # Maximum messages processed in a single /batch request
    BATCH_MAX: int = int(os.environ.get("BATCH_MAX", "50"))

    # ── yt-dlp quality ───────────────────────────────────────────────────
    # Default video format string passed to yt-dlp
    YTDLP_FORMAT: str = os.environ.get(
        "YTDLP_FORMAT",
        "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
    )

    # ── Web health-check (required by Render Web Service) ─────────────────
    PORT: int = int(os.environ.get("PORT", "8080"))
