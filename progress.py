"""
utils/progress.py — Telegram progress-bar helpers.

Provides:
  • humanbytes()          — format bytes as KB / MB / GB
  • time_formatter()      — seconds → "1h 2m 3s" string
  • progress_bar()        — ASCII █░ bar string
  • progress_callback()   — Pyrogram download/upload hook
  • edit_progress()       — rate-limited message editor
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from pyrogram.types import Message


# ─── Formatters ──────────────────────────────────────────────────────────────

def humanbytes(size: int) -> str:
    """Convert raw byte count to a human-readable string."""
    if not size:
        return "0 B"
    units = ("B", "KB", "MB", "GB", "TB")
    i = 0
    while size >= 1024 and i < len(units) - 1:
        size /= 1024
        i += 1
    return f"{size:.2f} {units[i]}"


def time_formatter(seconds: float) -> str:
    """Convert seconds to 'Xh Ym Zs' (omitting zero-value parts)."""
    seconds = int(seconds)
    parts = []
    for unit, div in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        v, seconds = divmod(seconds, div)
        if v:
            parts.append(f"{v}{unit}")
    return " ".join(parts) if parts else "0s"


def progress_bar(current: int, total: int, length: int = 10) -> str:
    """Return an ASCII progress bar: ██████░░░░  60%"""
    if total == 0:
        return "░" * length + "  0%"
    filled = int(length * current / total)
    bar    = "█" * filled + "░" * (length - filled)
    pct    = current * 100 / total
    return f"{bar}  {pct:.1f}%"


# ─── Pyrogram progress callback factory ─────────────────────────────────────

def make_progress_callback(
    message: Message,
    action: str = "Processing",
    start_time: Optional[float] = None,
    update_interval: int = 5,
) -> object:
    """
    Return a coroutine-compatible progress function for Pyrogram's
    download_media / send_* progress callbacks.

    Usage
    ─────
        cb = make_progress_callback(status_msg, "Uploading", time.time())
        await bot.send_document(..., progress=cb)
    """
    last_update: list[float] = [0.0]  # mutable container for closure
    if start_time is None:
        start_time = time.time()

    async def _callback(current: int, total: int) -> None:
        now = time.time()
        if now - last_update[0] < update_interval and current != total:
            return
        last_update[0] = now

        elapsed  = now - start_time
        speed    = current / elapsed if elapsed > 0 else 0
        eta      = (total - current) / speed if speed > 0 else 0

        bar = progress_bar(current, total)
        text = (
            f"<b>{action}</b>\n\n"
            f"<code>{bar}</code>\n\n"
            f"📦 {humanbytes(current)} / {humanbytes(total)}\n"
            f"🚀 Speed: {humanbytes(speed)}/s\n"
            f"⏳ ETA: {time_formatter(eta)}"
        )
        try:
            await message.edit_text(text)
        except Exception:
            pass  # ignore edit failures (flood wait, message not changed, etc.)

    return _callback


# ─── Standalone rate-limited message editor ──────────────────────────────────

_last_edit: dict[int, float] = {}


async def edit_progress(
    message: Message,
    text: str,
    min_interval: float = 3.0,
    force: bool = False,
) -> None:
    """
    Edit a status message, but only if enough time has passed since the
    last edit (prevents Telegram 429 flood waits).
    Pass force=True to always edit (e.g. on completion / error).
    """
    key = message.id
    now = time.time()
    if not force and now - _last_edit.get(key, 0) < min_interval:
        return
    _last_edit[key] = now
    try:
        await message.edit_text(text)
    except Exception:
        pass
