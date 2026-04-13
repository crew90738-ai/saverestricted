"""
handlers/batch.py — /batch and /cancel commands.

State machine (stored in Redis)
────────────────────────────────
  step == "awaiting_start"   → bot has asked user for the first link
  step == "awaiting_end"     → bot has the start link, waiting for end link
  step == "processing"       → batch is running (cancel is possible)

Redis key: batch_state:{user_id}  (expires after 10 min of inactivity)
"""

from __future__ import annotations

import asyncio
import logging

from pyrogram import Client, filters
from pyrogram.handlers.handler import StopPropagation
from pyrogram.types import Message

from config import Config
from handlers.forward import process_single_message
from utils import (
    check_force_sub,
    clear_batch_state,
    force_sub_message,
    get_batch_state,
    is_telegram_link,
    log_to_channel,
    parse_telegram_link,
    register_user,
    schedule_delete,
    set_batch_state,
)

logger = logging.getLogger(__name__)

# Tracks active batch tasks so /cancel can stop them
_active_tasks: dict[int, asyncio.Task] = {}


# ─── Batch processing coroutine ───────────────────────────────────────────────

async def _run_batch(
    bot: Client,
    user: Client,
    uid: int,
    source_chat,
    start_id: int,
    end_id: int,
    status_msg: Message,
) -> None:
    """Download messages from start_id to end_id (inclusive), sending each to uid."""
    total   = end_id - start_id + 1
    sent_ok = 0
    failed  = 0

    await status_msg.edit_text(
        f"🚀 <b>Batch started</b>\n"
        f"📋 Messages: <b>{total}</b>\n"
        f"⏳ Processing…"
    )

    for idx, msg_id in enumerate(range(start_id, end_id + 1), start=1):
        # Check if the user cancelled
        state = await get_batch_state(uid)
        if not state or state.get("cancelled"):
            await status_msg.edit_text(
                f"🛑 <b>Batch cancelled</b>\n"
                f"✅ Sent: {sent_ok}   ❌ Failed: {failed}"
            )
            await clear_batch_state(uid)
            return

        # Update progress every 5 messages or on the last one
        if idx % 5 == 0 or idx == total:
            try:
                await status_msg.edit_text(
                    f"⏳ <b>Batch in progress…</b>\n"
                    f"📋 {idx}/{total}  ✅ {sent_ok}  ❌ {failed}"
                )
            except Exception:
                pass

        sent = await process_single_message(
            bot=bot,
            user=user,
            dest_chat_id=uid,
            source_chat=source_chat,
            msg_id=msg_id,
            status_msg=None,
        )

        if sent:
            sent_ok += 1
        else:
            failed += 1

        await asyncio.sleep(1.2)

    # Done
    await clear_batch_state(uid)
    summary = (
        f"✅ <b>Batch complete!</b>\n\n"
        f"📋 Total: {total}\n"
        f"✅ Sent:   {sent_ok}\n"
        f"❌ Failed: {failed}"
    )
    try:
        await status_msg.edit_text(summary)
    except Exception:
        await bot.send_message(uid, summary)

    await log_to_channel(
        bot,
        f"📦 <b>Batch done</b>\n"
        f"User: <code>{uid}</code>\n"
        f"Range: {start_id}–{end_id} ({total} msgs)\n"
        f"✅ {sent_ok}  ❌ {failed}",
    )


# ─── Handler registration ─────────────────────────────────────────────────────

def register(bot: Client, user: Client, _redis) -> None:

    # ── /batch ────────────────────────────────────────────────────────────
    @bot.on_message(filters.command("batch") & filters.private)
    async def cmd_batch(client: Client, message: Message) -> None:
        uid = message.from_user.id

        if not await check_force_sub(client, uid):
            return await force_sub_message(client, message)

        state = await get_batch_state(uid)
        if state and state.get("step") == "processing":
            reply = await message.reply(
                "⚠️ A batch is already running. Use /cancel to stop it."
            )
            schedule_delete(reply)
            return

        await set_batch_state(uid, {"step": "awaiting_start"})
        reply = await message.reply(
            "📋 <b>Batch Download</b>\n\n"
            "Send the <b>first message link</b> of the range.\n\n"
            "<i>Example:</i> <code>https://t.me/c/1234567890/100</code>\n\n"
            "Use /cancel to abort."
        )
        schedule_delete(reply, delay=120)

    # ── /cancel ───────────────────────────────────────────────────────────
    @bot.on_message(filters.command("cancel") & filters.private)
    async def cmd_cancel(client: Client, message: Message) -> None:
        uid   = message.from_user.id
        state = await get_batch_state(uid)

        if not state:
            reply = await message.reply("ℹ️ No active batch to cancel.")
            schedule_delete(reply)
            return

        state["cancelled"] = True
        await set_batch_state(uid, state)

        task = _active_tasks.pop(uid, None)
        if task and not task.done():
            task.cancel()

        reply = await message.reply("🛑 Batch cancelled.")
        schedule_delete(reply)

    # ── Batch state-machine interceptor.
    #
    # group=2  → fires AFTER forward.py (group=0) and yt-dlp (group=1).
    #            But if the user IS in a batch flow, we raise StopPropagation
    #            so those lower-group handlers never see the message.
    #
    # How Pyrogram groups work:
    #   Handlers are called in ascending group order (0, 1, 2…).
    #   StopPropagation stops ALL further handlers, including lower groups
    #   that haven't fired yet — so raising it here effectively cancels
    #   forward.py and yt-dlp for this specific message.
    # ─────────────────────────────────────────────────────────────────────
    @bot.on_message(
        filters.private & filters.text
        & ~filters.command(["start", "help", "batch", "cancel",
                            "setthumb", "delthumb", "showthumb",
                            "setcaption", "delcaption", "showcaption",
                            "stats", "broadcast", "addpremium", "removepremium",
                            "listpremium"]),
        group=2,
    )
    async def batch_state_handler(client: Client, message: Message) -> None:
        uid  = message.from_user.id
        text = message.text.strip()

        state = await get_batch_state(uid)
        if not state:
            return  # Not in batch flow — other handlers already handled it

        step = state.get("step")

        # ── Waiting for the START link ────────────────────────────────────
        if step == "awaiting_start":
            if not is_telegram_link(text):
                reply = await message.reply(
                    "❌ That doesn't look like a Telegram message link.\n"
                    "Please send a valid link, e.g.:\n"
                    "<code>https://t.me/c/1234567890/100</code>"
                )
                schedule_delete(reply)
                raise StopPropagation

            parsed = parse_telegram_link(text)
            if not parsed:
                await message.reply("❌ Could not parse the link. Try again.")
                raise StopPropagation

            source_chat, start_id = parsed
            state.update({
                "step":        "awaiting_end",
                "source_chat": str(source_chat),
                "start_id":    start_id,
                "start_link":  text,
            })
            await set_batch_state(uid, state)

            reply = await message.reply(
                f"✅ Start message: <code>#{start_id}</code>\n\n"
                "Now send the <b>last message link</b> of the range."
            )
            schedule_delete(reply, delay=120)
            raise StopPropagation

        # ── Waiting for the END link ──────────────────────────────────────
        elif step == "awaiting_end":
            if not is_telegram_link(text):
                reply = await message.reply(
                    "❌ That doesn't look like a Telegram message link.\n"
                    "Please send the end link."
                )
                schedule_delete(reply)
                raise StopPropagation

            parsed = parse_telegram_link(text)
            if not parsed:
                await message.reply("❌ Could not parse the link.")
                raise StopPropagation

            _, end_id = parsed
            start_id  = int(state["start_id"])

            if end_id < start_id:
                start_id, end_id = end_id, start_id

            total = end_id - start_id + 1
            if total > Config.BATCH_MAX:
                reply = await message.reply(
                    f"❌ Range too large: {total} messages.\n"
                    f"Maximum allowed: {Config.BATCH_MAX}.\n"
                    "Try a smaller range."
                )
                schedule_delete(reply)
                await clear_batch_state(uid)
                raise StopPropagation

            raw_chat    = state["source_chat"]
            source_chat = int(raw_chat) if raw_chat.lstrip("-").isdigit() else raw_chat

            state.update({"step": "processing", "end_id": end_id, "cancelled": False})
            await set_batch_state(uid, state)

            status_msg = await message.reply(
                f"⏳ <b>Starting batch…</b>\n"
                f"📋 {total} messages (#{start_id} → #{end_id})"
            )

            task = asyncio.create_task(
                _run_batch(client, user, uid, source_chat, start_id, end_id, status_msg)
            )
            _active_tasks[uid] = task
            task.add_done_callback(lambda t: _active_tasks.pop(uid, None))
            raise StopPropagation

        # ── Already processing ────────────────────────────────────────────
        else:
            reply = await message.reply(
                "⚙️ Batch is already running. Use /cancel to stop it."
            )
            schedule_delete(reply)
            raise StopPropagation
