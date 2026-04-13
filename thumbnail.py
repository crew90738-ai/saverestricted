"""
handlers/thumbnail.py — Custom thumbnail commands.

/setthumb  — Reply to a photo (or send a photo with this caption) to set your thumbnail.
/delthumb  — Delete your custom thumbnail.
/showthumb — Preview your current thumbnail.
"""

from __future__ import annotations

import logging

from pyrogram import Client, filters
from pyrogram.types import Message

from utils import (
    del_thumb,
    get_thumb,
    register_user,
    schedule_delete,
    set_thumb,
)

logger = logging.getLogger(__name__)


def register(bot: Client, user: Client, _redis) -> None:

    # ── /setthumb ─────────────────────────────────────────────────────────
    @bot.on_message(filters.command("setthumb") & filters.private)
    async def cmd_setthumb(client: Client, message: Message) -> None:
        uid = message.from_user.id
        await register_user(uid)

        # Accept the thumbnail from the replied-to message or from the
        # current message if it contains a photo.
        target = message.reply_to_message or message

        if not target.photo:
            reply = await message.reply(
                "📸 <b>How to set a thumbnail</b>\n\n"
                "Reply to any <b>photo</b> with <code>/setthumb</code>.\n"
                "OR send a photo with <code>/setthumb</code> as the caption."
            )
            schedule_delete(reply)
            return

        # Use the largest available photo size (last in the list)
        file_id = target.photo.file_id
        await set_thumb(uid, file_id)

        reply = await message.reply("✅ Custom thumbnail saved!")
        schedule_delete(reply)
        logger.info("Thumbnail set for user %d", uid)

    # ── /delthumb ─────────────────────────────────────────────────────────
    @bot.on_message(filters.command("delthumb") & filters.private)
    async def cmd_delthumb(client: Client, message: Message) -> None:
        uid = message.from_user.id
        existing = await get_thumb(uid)

        if not existing:
            reply = await message.reply("ℹ️ You don't have a custom thumbnail set.")
            schedule_delete(reply)
            return

        await del_thumb(uid)
        reply = await message.reply("🗑️ Custom thumbnail removed.")
        schedule_delete(reply)

    # ── /showthumb ────────────────────────────────────────────────────────
    @bot.on_message(filters.command("showthumb") & filters.private)
    async def cmd_showthumb(client: Client, message: Message) -> None:
        uid = message.from_user.id
        file_id = await get_thumb(uid)

        if not file_id:
            reply = await message.reply(
                "ℹ️ No thumbnail set.\n"
                "Use /setthumb (reply to a photo) to set one."
            )
            schedule_delete(reply)
            return

        try:
            sent = await client.send_photo(
                chat_id=uid,
                photo=file_id,
                caption="🖼️ <b>Your current thumbnail</b>",
            )
            schedule_delete(sent, delay=120)
        except Exception as e:
            logger.error("showthumb send error: %s", e)
            reply = await message.reply(
                f"❌ Could not load thumbnail (file_id may be stale).\n"
                f"Please set a new one with /setthumb."
            )
            await del_thumb(uid)          # clear stale file_id
            schedule_delete(reply)
