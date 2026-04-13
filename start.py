"""
handlers/start.py — /start, /help, /setcaption, /delcaption + force-sub callback.
"""

from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import Config
from utils import (
    check_force_sub,
    force_sub_message,
    log_to_channel,
    register_user,
    schedule_delete,
    set_caption,
    del_caption,
    get_caption,
)

logger = logging.getLogger(__name__)

# ─── Help text ────────────────────────────────────────────────────────────────

HELP_TEXT = """
<b>🤖 Save Restricted Content Bot</b>

<b>How to use</b>
Send me any Telegram message link and I'll re-deliver the content without restrictions.

<b>Supported link formats</b>
• <code>https://t.me/channelname/123</code> — public channel
• <code>https://t.me/c/1234567890/123</code> — private channel / group

<b>Batch download</b>
Use /batch to download a range of messages at once.

<b>Custom thumbnail</b>
/setthumb — set a custom thumbnail (reply to a photo)
/delthumb  — remove your custom thumbnail
/showthumb — preview your current thumbnail

<b>Custom caption</b>
/setcaption [text] — set a caption appended to every file
/delcaption        — remove custom caption

<b>yt-dlp downloader</b>
Send a YouTube / Instagram / TikTok / Facebook URL and I'll download + send the video.

<b>Commands</b>
/start   — welcome message
/help    — this help page
/batch   — batch download
/cancel  — cancel active batch
"""


def build_start_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📖 Help", callback_data="cb_help"),
            InlineKeyboardButton("📊 Stats", callback_data="cb_stats"),
        ],
        [
            InlineKeyboardButton(
                "➕ Add to Group",
                url=f"https://t.me/{Config.BOT_USERNAME}?startgroup=true",
            ),
        ],
    ])


# ─── Handler registration ─────────────────────────────────────────────────────

def register(bot: Client, user: Client, _redis) -> None:

    # ── /start ────────────────────────────────────────────────────────────
    @bot.on_message(filters.command("start") & filters.private)
    async def cmd_start(client: Client, message: Message) -> None:
        uid = message.from_user.id
        is_new = await register_user(uid)

        if not await check_force_sub(client, uid):
            return await force_sub_message(client, message)

        await message.reply(
            f"👋 <b>Hello {message.from_user.mention}!</b>\n\n"
            "I can save content from <b>restricted Telegram channels</b> and "
            "download videos from YouTube, Instagram, TikTok, and 100+ other sites.\n\n"
            "Send me a <b>Telegram message link</b> or any <b>video URL</b> to get started.",
            reply_markup=build_start_kb(),
        )

        if is_new:
            await log_to_channel(
                client,
                f"👤 New user: {message.from_user.mention} (<code>{uid}</code>)",
            )

    # ── /help ─────────────────────────────────────────────────────────────
    @bot.on_message(filters.command("help") & filters.private)
    async def cmd_help(client: Client, message: Message) -> None:
        if not await check_force_sub(client, message.from_user.id):
            return await force_sub_message(client, message)
        reply = await message.reply(HELP_TEXT, disable_web_page_preview=True)
        schedule_delete(reply)

    # ── /setcaption ───────────────────────────────────────────────────────
    @bot.on_message(filters.command("setcaption") & filters.private)
    async def cmd_setcaption(client: Client, message: Message) -> None:
        if not await check_force_sub(client, message.from_user.id):
            return await force_sub_message(client, message)

        text = message.text.split(None, 1)
        if len(text) < 2:
            return await message.reply(
                "Usage: <code>/setcaption Your custom caption here</code>"
            )

        caption = text[1].strip()
        await set_caption(message.from_user.id, caption)
        reply = await message.reply(
            f"✅ Custom caption saved:\n<code>{caption}</code>"
        )
        schedule_delete(reply)

    # ── /delcaption ───────────────────────────────────────────────────────
    @bot.on_message(filters.command("delcaption") & filters.private)
    async def cmd_delcaption(client: Client, message: Message) -> None:
        await del_caption(message.from_user.id)
        reply = await message.reply("🗑️ Custom caption removed.")
        schedule_delete(reply)

    # ── /showcaption ──────────────────────────────────────────────────────
    @bot.on_message(filters.command("showcaption") & filters.private)
    async def cmd_showcaption(client: Client, message: Message) -> None:
        cap = await get_caption(message.from_user.id)
        if cap:
            reply = await message.reply(f"📝 Your caption:\n<code>{cap}</code>")
        else:
            reply = await message.reply("ℹ️ No custom caption is set.")
        schedule_delete(reply)

    # ── Inline button: Help ───────────────────────────────────────────────
    @bot.on_callback_query(filters.regex(r"^cb_help$"))
    async def cb_help(client: Client, query: CallbackQuery) -> None:
        await query.message.edit_text(
            HELP_TEXT,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Back", callback_data="cb_start")]]
            ),
            disable_web_page_preview=True,
        )

    # ── Inline button: Back to start ──────────────────────────────────────
    @bot.on_callback_query(filters.regex(r"^cb_start$"))
    async def cb_start(client: Client, query: CallbackQuery) -> None:
        await query.message.edit_text(
            f"👋 <b>Hello {query.from_user.mention}!</b>\n\n"
            "Send me a <b>Telegram message link</b> or any <b>video URL</b>.",
            reply_markup=build_start_kb(),
        )

    # ── Inline button: force-sub re-check ────────────────────────────────
    @bot.on_callback_query(filters.regex(r"^check_sub$"))
    async def cb_check_sub(client: Client, query: CallbackQuery) -> None:
        if await check_force_sub(client, query.from_user.id):
            await query.message.edit_text(
                "✅ <b>Verified!</b> You can now use the bot.\n\n"
                "Send me a message link or video URL."
            )
        else:
            await query.answer("❌ You haven't joined yet!", show_alert=True)

    # ── Inline button: Stats (placeholder; full stats in admin.py) ────────
    @bot.on_callback_query(filters.regex(r"^cb_stats$"))
    async def cb_stats(client: Client, query: CallbackQuery) -> None:
        from utils import get_stats
        s = await get_stats()
        await query.answer(
            f"👥 Users: {s['users']}\n"
            f"📥 Downloads: {s['downloads']}\n"
            f"⭐ Premium: {s['premium']}",
            show_alert=True,
        )
