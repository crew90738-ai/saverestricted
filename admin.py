"""
handlers/admin.py — Owner-only commands.

/stats          — live bot statistics
/broadcast      — send a message to every bot user
/addpremium     — grant premium to a user
/removepremium  — revoke premium from a user

All commands are silently ignored for non-owners (no error sent, to avoid
giving information to malicious actors about what commands exist).
"""

from __future__ import annotations

import asyncio
import logging

from pyrogram import Client, filters
from pyrogram.errors import FloodWait, InputUserDeactivated, UserIsBlocked
from pyrogram.types import Message

from config import Config
from utils import (
    add_premium,
    get_all_users,
    get_stats,
    is_premium,
    log_to_channel,
    remove_premium,
    schedule_delete,
    total_users,
)

logger = logging.getLogger(__name__)


# ─── Owner filter ─────────────────────────────────────────────────────────────

def _is_owner(_, __, message: Message) -> bool:
    return message.from_user and message.from_user.id == Config.OWNER_ID


owner_filter = filters.create(_is_owner)


# ─── Handler registration ─────────────────────────────────────────────────────

def register(bot: Client, user: Client, _redis) -> None:

    # ── /stats ────────────────────────────────────────────────────────────
    @bot.on_message(filters.command("stats") & owner_filter)
    async def cmd_stats(client: Client, message: Message) -> None:
        s = await get_stats()
        text = (
            "📊 <b>Bot Statistics</b>\n\n"
            f"👥 Total users:   <b>{s['users']:,}</b>\n"
            f"⭐ Premium users: <b>{s['premium']:,}</b>\n"
            f"📥 Downloads:     <b>{s['downloads']:,}</b>\n"
            f"📁 Files sent:    <b>{s['files']:,}</b>\n"
        )
        reply = await message.reply(text)
        schedule_delete(reply, delay=120)

    # ── /broadcast ────────────────────────────────────────────────────────
    @bot.on_message(filters.command("broadcast") & owner_filter)
    async def cmd_broadcast(client: Client, message: Message) -> None:
        # Usage: reply to a message with /broadcast, or /broadcast <text>
        target = message.reply_to_message

        if not target:
            parts = message.text.split(None, 1)
            if len(parts) < 2:
                reply = await message.reply(
                    "Usage:\n"
                    "• Reply to any message with <code>/broadcast</code>\n"
                    "• Or: <code>/broadcast Your text here</code>"
                )
                schedule_delete(reply)
                return

        status = await message.reply("📡 <b>Broadcast started…</b>")

        users      = await get_all_users()
        sent_ok    = 0
        failed_ok  = 0
        total      = len(users)

        for idx, uid in enumerate(users, start=1):
            try:
                if target:
                    await target.copy(uid)
                else:
                    await client.send_message(uid, parts[1])
                sent_ok += 1
            except (UserIsBlocked, InputUserDeactivated):
                failed_ok += 1
            except FloodWait as fw:
                logger.warning("Broadcast FloodWait %ds", fw.value)
                await asyncio.sleep(fw.value + 1)
                try:
                    if target:
                        await target.copy(uid)
                    else:
                        await client.send_message(uid, parts[1])
                    sent_ok += 1
                except Exception:
                    failed_ok += 1
            except Exception as e:
                logger.debug("Broadcast skip %d: %s", uid, e)
                failed_ok += 1

            # Update status every 50 users
            if idx % 50 == 0 or idx == total:
                try:
                    await status.edit_text(
                        f"📡 <b>Broadcasting…</b>\n"
                        f"{idx}/{total} — ✅ {sent_ok} | ❌ {failed_ok}"
                    )
                except Exception:
                    pass

            await asyncio.sleep(0.05)   # stay well under Telegram's 30 msg/s global limit

        summary = (
            f"📡 <b>Broadcast complete</b>\n\n"
            f"👥 Total users: {total}\n"
            f"✅ Delivered:  {sent_ok}\n"
            f"❌ Failed:     {failed_ok}"
        )
        try:
            await status.edit_text(summary)
        except Exception:
            await message.reply(summary)

        await log_to_channel(client, summary)

    # ── /addpremium ───────────────────────────────────────────────────────
    @bot.on_message(filters.command("addpremium") & owner_filter)
    async def cmd_addpremium(client: Client, message: Message) -> None:
        target_id = _extract_user_id(message)
        if not target_id:
            reply = await message.reply(
                "Usage: <code>/addpremium USER_ID</code>\n"
                "Or reply to a user's message with <code>/addpremium</code>"
            )
            schedule_delete(reply)
            return

        already = await is_premium(target_id)
        await add_premium(target_id)

        action = "already had" if already else "granted"
        reply  = await message.reply(
            f"⭐ Premium {action} to <code>{target_id}</code>."
        )
        schedule_delete(reply)

        try:
            await client.send_message(
                target_id,
                "🌟 <b>You've been granted Premium access!</b>\n"
                "Enjoy 4 GB uploads and priority processing.",
            )
        except Exception:
            pass

        await log_to_channel(
            client, f"⭐ Premium added: <code>{target_id}</code> by owner."
        )

    # ── /removepremium ────────────────────────────────────────────────────
    @bot.on_message(filters.command("removepremium") & owner_filter)
    async def cmd_removepremium(client: Client, message: Message) -> None:
        target_id = _extract_user_id(message)
        if not target_id:
            reply = await message.reply(
                "Usage: <code>/removepremium USER_ID</code>\n"
                "Or reply to a user's message with <code>/removepremium</code>"
            )
            schedule_delete(reply)
            return

        await remove_premium(target_id)
        reply = await message.reply(
            f"🗑️ Premium removed from <code>{target_id}</code>."
        )
        schedule_delete(reply)

        await log_to_channel(
            client, f"🗑️ Premium removed: <code>{target_id}</code> by owner."
        )

    # ── /listpremium ──────────────────────────────────────────────────────
    @bot.on_message(filters.command("listpremium") & owner_filter)
    async def cmd_listpremium(client: Client, message: Message) -> None:
        from utils import get_all_premium
        members = await get_all_premium()
        if not members:
            reply = await message.reply("ℹ️ No premium users yet.")
        else:
            lines = "\n".join(f"• <code>{uid}</code>" for uid in members)
            reply = await message.reply(
                f"⭐ <b>Premium users ({len(members)})</b>\n\n{lines}"
            )
        schedule_delete(reply, delay=120)


# ─── Helper ───────────────────────────────────────────────────────────────────

def _extract_user_id(message: Message) -> int | None:
    """
    Try to get a target user ID from:
    1. The ID of the replied-to message sender.
    2. The first argument of the command.
    """
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id

    parts = message.text.split()
    if len(parts) >= 2:
        try:
            return int(parts[1])
        except ValueError:
            return None
    return None
