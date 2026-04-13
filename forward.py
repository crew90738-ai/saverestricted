"""
handlers/forward.py — Core "save restricted content" logic.

Flow
────
1. User sends a t.me link.
2. User client (SESSION_STRING) fetches the message from the channel.
3. If media is present, user client downloads it to a temp file.
4. Bot re-uploads the file (with custom thumb / caption if set).
5. Temp file is cleaned up.

Handler group
─────────────
group=0 — runs before yt-dlp (group=1) and batch interceptor (group=2).
Only acts on Telegram links; all other text falls through to the next group.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

from pyrogram import Client, filters
from pyrogram.errors import (
    ChannelInvalid,
    ChannelPrivate,
    FloodWait,
    MessageIdInvalid,
    UsernameInvalid,
    UsernameNotOccupied,
)
from pyrogram.types import Message

from config import Config
from utils import (
    check_force_sub,
    force_sub_message,
    get_caption,
    get_media_type,
    get_thumb,
    humanbytes,
    inc_downloads,
    inc_files,
    is_telegram_link,
    log_to_channel,
    make_progress_callback,
    parse_telegram_link,
    register_user,
    schedule_delete,
)

logger = logging.getLogger(__name__)


# ─── Core processing function (imported and reused by batch.py) ───────────────

async def process_single_message(
    bot: Client,
    user: Client,
    dest_chat_id: int,
    source_chat,          # int (negative) or str (username)
    msg_id: int,
    status_msg: Optional[Message] = None,
) -> Optional[Message]:
    """
    Fetch one message via the user client and re-send it via the bot.

    Returns the sent Message on success, None on failure.
    status_msg — a previously-sent message that gets edited with progress.
    """
    # 1. Fetch the source message via the user client
    try:
        src_msg: Message = await user.get_messages(source_chat, msg_id)
    except ChannelPrivate:
        if status_msg:
            await status_msg.edit_text(
                "❌ The user account is not a member of this private channel."
            )
        return None
    except (ChannelInvalid, UsernameInvalid, UsernameNotOccupied):
        if status_msg:
            await status_msg.edit_text("❌ Invalid channel — check the link.")
        return None
    except MessageIdInvalid:
        if status_msg:
            await status_msg.edit_text(f"❌ Message #{msg_id} not found.")
        return None
    except FloodWait as fw:
        logger.warning("FloodWait %ds while fetching message", fw.value)
        await asyncio.sleep(fw.value)
        return await process_single_message(
            bot, user, dest_chat_id, source_chat, msg_id, status_msg
        )

    if src_msg.empty:
        return None

    # 2. Resolve caption and thumbnail for this user
    uid        = dest_chat_id
    custom_cap = await get_caption(uid)
    thumb_fid  = await get_thumb(uid)

    original_cap = src_msg.caption or src_msg.text or ""
    caption      = custom_cap if custom_cap else original_cap
    if len(caption) > 1024:
        caption = caption[:1021] + "…"

    # 3. Text-only message — just re-send the text
    media_type = get_media_type(src_msg)
    if not media_type:
        sent = await bot.send_message(dest_chat_id, caption or "​")
        await inc_downloads()
        return sent

    # 4. Download the media file via the user client
    if status_msg:
        await status_msg.edit_text("📥 <b>Downloading…</b>")

    os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)
    dl_start = time.time()

    try:
        dl_cb     = (
            make_progress_callback(status_msg, "Downloading", dl_start)
            if status_msg else None
        )
        file_path = await user.download_media(
            src_msg,
            file_name=Config.DOWNLOAD_DIR + "/",
            progress=dl_cb,
        )
    except Exception as e:
        logger.error("Download failed for msg %d: %s", msg_id, e)
        if status_msg:
            await status_msg.edit_text(f"❌ Download failed: <code>{e}</code>")
        return None

    if not file_path or not os.path.exists(file_path):
        if status_msg:
            await status_msg.edit_text("❌ Downloaded file not found on disk.")
        return None

    # 5. Guard against oversized files
    file_size = os.path.getsize(file_path)
    if file_size > Config.MAX_FILE_SIZE:
        os.remove(file_path)
        if status_msg:
            await status_msg.edit_text(
                f"❌ File too large: {humanbytes(file_size)}. Max is 4 GB."
            )
        return None

    if status_msg:
        await status_msg.edit_text(
            f"📤 <b>Uploading…</b>\n📦 Size: {humanbytes(file_size)}"
        )

    # 6. Resolve thumbnail (download from Telegram if user has one saved)
    thumb_path: Optional[str] = None
    if thumb_fid:
        try:
            thumb_path = await bot.download_media(
                thumb_fid, file_name=Config.DOWNLOAD_DIR + "/thumb_"
            )
        except Exception:
            thumb_path = None

    # 7. Upload via bot client
    up_start = time.time()
    up_cb    = (
        make_progress_callback(status_msg, "Uploading", up_start)
        if status_msg else None
    )
    send_kwargs = dict(chat_id=dest_chat_id, caption=caption, progress=up_cb)

    sent_msg: Optional[Message] = None
    try:
        if media_type == "video":
            v = src_msg.video
            sent_msg = await bot.send_video(
                file_name=os.path.basename(file_path),
                video=file_path,
                duration=v.duration if v else 0,
                width=v.width if v else 0,
                height=v.height if v else 0,
                thumb=thumb_path,
                supports_streaming=True,
                **send_kwargs,
            )
        elif media_type == "audio":
            a = src_msg.audio
            sent_msg = await bot.send_audio(
                audio=file_path,
                duration=a.duration if a else 0,
                performer=a.performer if a else None,
                title=a.title if a else None,
                thumb=thumb_path,
                **send_kwargs,
            )
        elif media_type == "document":
            sent_msg = await bot.send_document(
                document=file_path, thumb=thumb_path, **send_kwargs
            )
        elif media_type == "photo":
            sent_msg = await bot.send_photo(photo=file_path, **send_kwargs)
        elif media_type == "animation":
            sent_msg = await bot.send_animation(
                animation=file_path, thumb=thumb_path, **send_kwargs
            )
        elif media_type == "voice":
            vn = src_msg.voice
            sent_msg = await bot.send_voice(
                voice=file_path,
                duration=vn.duration if vn else 0,
                **send_kwargs,
            )
        elif media_type == "video_note":
            vn = src_msg.video_note
            sent_msg = await bot.send_video_note(
                video_note=file_path,
                duration=vn.duration if vn else 0,
                length=vn.length if vn else 1,
                thumb=thumb_path,
                chat_id=dest_chat_id,
                progress=up_cb,
            )
        elif media_type == "sticker":
            sent_msg = await bot.send_sticker(
                chat_id=dest_chat_id, sticker=file_path
            )
        else:
            sent_msg = await bot.send_document(
                document=file_path, thumb=thumb_path, **send_kwargs
            )

    except FloodWait as fw:
        logger.warning("FloodWait %ds during upload", fw.value)
        await asyncio.sleep(fw.value + 2)
    except Exception as e:
        logger.error("Upload failed: %s", e)
        if status_msg:
            await status_msg.edit_text(f"❌ Upload failed: <code>{e}</code>")
    finally:
        # Always clean up temp files
        for path in filter(None, [file_path, thumb_path]):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass

    if sent_msg:
        await inc_downloads()
        await inc_files()

    return sent_msg


# ─── Handler registration ─────────────────────────────────────────────────────

def register(bot: Client, user: Client, _redis) -> None:

    @bot.on_message(
        filters.private
        & filters.text
        & ~filters.command(["start", "help", "batch", "cancel",
                            "setthumb", "delthumb", "showthumb",
                            "setcaption", "delcaption", "showcaption",
                            "stats", "broadcast", "addpremium", "removepremium",
                            "listpremium"]),
        group=0,    # ← explicit: runs before yt-dlp (1) and batch interceptor (2)
    )
    async def handle_link(client: Client, message: Message) -> None:
        uid  = message.from_user.id
        text = message.text.strip()

        await register_user(uid)

        if not await check_force_sub(client, uid):
            return await force_sub_message(client, message)

        # Only act on Telegram message links
        if not is_telegram_link(text):
            return   # fall through to yt-dlp handler (group=1)

        parsed = parse_telegram_link(text)
        if not parsed:
            reply = await message.reply(
                "❓ Couldn't parse this link.\n\n"
                "Supported formats:\n"
                "• <code>https://t.me/username/123</code>\n"
                "• <code>https://t.me/c/1234567890/123</code>"
            )
            schedule_delete(reply)
            return

        source_chat, msg_id = parsed
        status = await message.reply("⏳ <b>Processing…</b>")

        sent = await process_single_message(
            bot=client,
            user=user,
            dest_chat_id=message.chat.id,
            source_chat=source_chat,
            msg_id=msg_id,
            status_msg=status,
        )

        if sent:
            try:
                await status.delete()
            except Exception:
                pass
            await log_to_channel(
                client,
                f"📥 <b>Forwarded</b>\n"
                f"User: {message.from_user.mention} (<code>{uid}</code>)\n"
                f"Link: <code>{text}</code>",
            )
        else:
            schedule_delete(status)
