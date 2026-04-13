"""
utils/helpers.py — Miscellaneous helpers used across handlers.

Includes
────────
  • parse_telegram_link()    — extract (chat_id | username, msg_id) from a t.me link
  • check_force_sub()        — verify user has joined the required channel
  • auto_delete()            — delete a message after a delay
  • get_media_type()         — return normalised media type string
  • safe_send()              — send with basic FloodWait retry
  • log_to_channel()         — fire-and-forget log message
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Optional, Tuple, Union

from pyrogram import Client
from pyrogram.errors import FloodWait, UserNotParticipant, ChatAdminRequired
from pyrogram.types import Message

from config import Config

logger = logging.getLogger(__name__)


# ─── Telegram link parsing ───────────────────────────────────────────────────

# t.me/c/1234567890/100        → private channel message
_PRIVATE_RE  = re.compile(r"t\.me/c/(\d+)/(\d+)")

# t.me/username/100            → public channel/group message
_PUBLIC_RE   = re.compile(r"t\.me/([a-zA-Z][a-zA-Z0-9_]{3,})/(\d+)")

# t.me/+InviteCode  (not supported for direct fetch, but we detect it)
_INVITE_RE   = re.compile(r"t\.me/\+([a-zA-Z0-9_-]+)")


def parse_telegram_link(url: str) -> Optional[Tuple[Union[int, str], int]]:
    """
    Parse a Telegram message link.

    Returns
    ───────
    (chat_id_or_username, message_id)   on success
    None                                 if the URL is not a recognisable message link

    Private channels: chat_id is returned as -100{id}  (negative integer).
    Public channels:  chat_id is returned as the username string (without @).
    """
    url = url.strip().replace("https://", "").replace("http://", "")

    m = _PRIVATE_RE.search(url)
    if m:
        # -100 prefix turns the bare channel ID into the full Telegram ID
        chat_id = int("-100" + m.group(1))
        msg_id  = int(m.group(2))
        return chat_id, msg_id

    m = _PUBLIC_RE.search(url)
    if m:
        username = m.group(1)
        msg_id   = int(m.group(2))
        return username, msg_id

    return None


def is_telegram_link(text: str) -> bool:
    """Quick check: does this text look like a Telegram message link?"""
    return bool(_PRIVATE_RE.search(text) or _PUBLIC_RE.search(text))


def is_ytdlp_url(url: str) -> bool:
    """Return True if the URL belongs to a site yt-dlp can handle."""
    url_lower = url.lower()
    # We match a broad set of domains; yt-dlp itself will decide if it can handle it
    patterns = [
        r"youtube\.com/watch", r"youtu\.be/",
        r"instagram\.com/", r"facebook\.com/",
        r"tiktok\.com/", r"twitter\.com/status", r"x\.com/",
        r"vimeo\.com/", r"dailymotion\.com/", r"twitch\.tv/",
        r"reddit\.com/r/.*/comments",
        r"pinterest\.", r"linkedin\.com/",
        r"soundcloud\.com/", r"mixcloud\.com/",
        r"bilibili\.com/", r"nicovideo\.jp/",
    ]
    return any(re.search(p, url_lower) for p in patterns)


# ─── Force-subscribe check ───────────────────────────────────────────────────

async def check_force_sub(bot: Client, user_id: int) -> bool:
    """
    Returns True if the user has joined the FORCE_SUB_CHANNEL,
    or if force-sub is not configured.
    """
    channel = Config.FORCE_SUB_CHANNEL
    if not channel:
        return True
    try:
        member = await bot.get_chat_member(channel, user_id)
        return member.status.value not in ("left", "banned", "kicked")
    except UserNotParticipant:
        return False
    except ChatAdminRequired:
        logger.warning("Bot is not admin in FORCE_SUB_CHANNEL — force-sub disabled.")
        return True
    except Exception as e:
        logger.warning("check_force_sub error: %s", e)
        return True


async def force_sub_message(bot: Client, message: Message) -> None:
    """Send the 'please join' inline button if force-sub fails."""
    channel = Config.FORCE_SUB_CHANNEL
    if channel.lstrip("-").isdigit():
        invite = f"https://t.me/c/{channel.lstrip('-').removeprefix('100')}"
    else:
        invite = f"https://t.me/{channel.lstrip('@')}"

    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📢 Join Channel", url=invite),
        InlineKeyboardButton("✅ I've Joined", callback_data="check_sub"),
    ]])
    await message.reply(
        "⚠️ <b>You must join our channel to use this bot.</b>\n\n"
        "Click below, join, then press <b>✅ I've Joined</b>.",
        reply_markup=kb,
    )


# ─── Auto-delete helper ───────────────────────────────────────────────────────

async def auto_delete(message: Message, delay: int = Config.AUTO_DELETE_DELAY) -> None:
    """Delete a message after `delay` seconds (fire-and-forget coroutine)."""
    if delay <= 0:
        return
    await asyncio.sleep(delay)
    try:
        await message.delete()
    except Exception:
        pass


def schedule_delete(message: Message, delay: int = Config.AUTO_DELETE_DELAY) -> None:
    """Create an auto-delete task without awaiting it."""
    if delay > 0:
        asyncio.create_task(auto_delete(message, delay))


# ─── Media type helper ────────────────────────────────────────────────────────

def get_media_type(message: Message) -> Optional[str]:
    """
    Returns a normalised string describing the media in a message,
    or None if there is no media.
    """
    for attr in (
        "document", "video", "audio", "photo",
        "animation", "voice", "video_note", "sticker",
    ):
        if getattr(message, attr, None):
            return attr
    return None


# ─── Safe send (FloodWait-aware) ─────────────────────────────────────────────

async def safe_send(coro, retries: int = 3):
    """
    Await a Pyrogram send coroutine, automatically sleeping through
    FloodWait errors (up to `retries` attempts).
    """
    for attempt in range(retries):
        try:
            return await coro
        except FloodWait as fw:
            wait = fw.value + 2
            logger.warning("FloodWait %ds (attempt %d/%d)", wait, attempt + 1, retries)
            await asyncio.sleep(wait)
        except Exception as e:
            logger.error("safe_send error: %s", e)
            raise
    raise RuntimeError("safe_send: exceeded retry limit")


# ─── Log channel helper ───────────────────────────────────────────────────────

async def log_to_channel(bot: Client, text: str) -> None:
    """Send a message to LOG_CHANNEL; silently ignore failures."""
    if not Config.LOG_CHANNEL:
        return
    try:
        await bot.send_message(Config.LOG_CHANNEL, text)
    except Exception as e:
        logger.debug("log_to_channel: %s", e)
