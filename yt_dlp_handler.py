"""
handlers/yt_dlp_handler.py — Download videos from 1 000+ sites using yt-dlp.

Handler group
─────────────
group=1 — runs AFTER forward.py (group=0), so Telegram links are already
handled and never reach here.  Only non-Telegram URLs arrive.

yt-dlp is synchronous, so it runs inside asyncio.get_event_loop().run_in_executor().
A separate asyncio task polls a shared _ProgressState every few seconds
and edits the status message, giving a real-time progress bar.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import time
from typing import Any, Optional

from pyrogram import Client, filters
from pyrogram.types import Message

from config import Config
from utils import (
    check_force_sub,
    force_sub_message,
    get_caption,
    get_thumb,
    humanbytes,
    inc_downloads,
    is_ytdlp_url,
    log_to_channel,
    make_progress_callback,
    register_user,
    schedule_delete,
    time_formatter,
)

logger = logging.getLogger(__name__)

# ── yt-dlp availability ───────────────────────────────────────────────────────
try:
    import yt_dlp  # type: ignore
    YTDLP_AVAILABLE = True
except ImportError:
    YTDLP_AVAILABLE = False
    logger.warning("yt-dlp not installed — URL downloading disabled.")

# ── aiohttp availability (only needed for thumbnail fetching) ─────────────────
try:
    import aiohttp as _aiohttp
    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False


# ─── Progress tracking ────────────────────────────────────────────────────────

class _ProgressState:
    def __init__(self):
        self.downloaded: int  = 0
        self.total: int       = 0
        self.speed: float     = 0.0
        self.eta: float       = 0.0
        self.status: str      = "starting"
        self.filename: str    = ""
        self.error: str       = ""


def _make_hook(state: _ProgressState):
    def hook(d: dict) -> None:
        state.status = d.get("status", "unknown")
        if state.status == "downloading":
            state.downloaded = d.get("downloaded_bytes", 0)
            state.total      = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
            state.speed      = d.get("speed") or 0.0
            state.eta        = d.get("eta") or 0.0
            state.filename   = d.get("filename", "")
        elif state.status == "finished":
            state.filename = d.get("filename", state.filename)
        elif state.status == "error":
            state.error = str(d.get("error", "Unknown error"))
    return hook


async def _poll_progress(
    state: _ProgressState, status_msg: Message, interval: float = 4.0
) -> None:
    """Edit the status message with yt-dlp download progress every N seconds."""
    while state.status not in ("finished", "error"):
        await asyncio.sleep(interval)
        if state.total > 0:
            pct = state.downloaded * 100 / state.total
            bar = "█" * int(pct / 10) + "░" * (10 - int(pct / 10))
            text = (
                f"📥 <b>Downloading…</b>\n\n"
                f"<code>{bar}  {pct:.1f}%</code>\n\n"
                f"📦 {humanbytes(state.downloaded)} / {humanbytes(state.total)}\n"
                f"🚀 Speed: {humanbytes(int(state.speed))}/s\n"
                f"⏳ ETA: {time_formatter(int(state.eta))}"
            )
        else:
            text = f"📥 <b>Downloading…</b>\n{humanbytes(state.downloaded)} downloaded"
        try:
            await status_msg.edit_text(text)
        except Exception:
            pass


# ─── Synchronous helpers (run in executor) ────────────────────────────────────

def _ydl_download(
    url: str, out_dir: str, state: _ProgressState, fmt: str
) -> Optional[str]:
    """Download via yt-dlp.  Returns file path on success, None on failure."""
    ydl_opts: dict[str, Any] = {
        "format":              fmt,
        "outtmpl":             os.path.join(out_dir, "%(title).60s.%(ext)s"),
        "progress_hooks":      [_make_hook(state)],
        "quiet":               True,
        "no_warnings":         True,
        "noplaylist":          True,
        "merge_output_format": "mp4",
        "max_filesize":        Config.MAX_FILE_SIZE,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info     = ydl.extract_info(url, download=True)
            filename = ydl.prepare_filename(info)
            if not os.path.exists(filename):
                mp4 = os.path.splitext(filename)[0] + ".mp4"
                if os.path.exists(mp4):
                    filename = mp4
            state.filename = filename
            state.status   = "finished"
            return filename
    except Exception as e:
        state.status = "error"
        state.error  = str(e)
        logger.error("yt-dlp error: %s", e)
        return None


def _ydl_info(url: str) -> Optional[dict]:
    """Fetch metadata without downloading."""
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
            return ydl.extract_info(url, download=False)
    except Exception:
        return None


async def _fetch_thumbnail(url: str, dest: str) -> Optional[str]:
    """Download a thumbnail image to disk.  Requires aiohttp."""
    if not AIOHTTP_AVAILABLE:
        return None
    try:
        import aiohttp
        async with aiohttp.ClientSession() as sess:
            async with sess.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    with open(dest, "wb") as f:
                        f.write(await resp.read())
                    return dest
    except Exception:
        pass
    return None


def _cleanup_dir(path: str) -> None:
    import shutil
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


# ─── Handler registration ─────────────────────────────────────────────────────

def register(bot: Client, user: Client, _redis) -> None:

    if not YTDLP_AVAILABLE:
        logger.warning("Skipping yt-dlp handler registration (yt-dlp not installed).")
        return

    # group=1 — runs after forward.py (group=0) which already claimed Telegram links
    @bot.on_message(
        filters.private & filters.text,
        group=1,
    )
    async def handle_url(client: Client, message: Message) -> None:
        uid  = message.from_user.id
        text = message.text.strip()

        if not is_ytdlp_url(text):
            return  # not a supported URL — ignore

        await register_user(uid)

        if not await check_force_sub(client, uid):
            return await force_sub_message(client, message)

        status = await message.reply("🔍 <b>Fetching info…</b>")

        # 1. Fetch metadata
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, _ydl_info, text)

        if not info:
            await status.edit_text("❌ Could not fetch info for this URL.")
            schedule_delete(status)
            return

        title    = info.get("title", "video")[:80]
        duration = info.get("duration", 0) or 0
        uploader = info.get("uploader", "")

        await status.edit_text(
            f"📹 <b>{title}</b>\n"
            f"👤 {uploader}\n"
            f"⏱ {time_formatter(int(duration))}\n\n"
            f"⬇️ Downloading…"
        )

        # 2. Set up temp directory
        os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)
        tmp_dir = tempfile.mkdtemp(dir=Config.DOWNLOAD_DIR)

        # 3. Download in executor, poll progress concurrently
        prog_state = _ProgressState()
        dl_task    = loop.run_in_executor(
            None, _ydl_download, text, tmp_dir, prog_state, Config.YTDLP_FORMAT
        )
        poll_task  = asyncio.create_task(_poll_progress(prog_state, status))

        file_path: Optional[str] = await dl_task
        poll_task.cancel()

        if not file_path or not os.path.exists(file_path):
            err = prog_state.error or "Unknown error"
            await status.edit_text(f"❌ Download failed:\n<code>{err}</code>")
            schedule_delete(status)
            _cleanup_dir(tmp_dir)
            return

        # 4. Custom thumb / caption
        thumb_fid  = await get_thumb(uid)
        custom_cap = await get_caption(uid)
        caption    = custom_cap or f"<b>{title}</b>"
        if len(caption) > 1024:
            caption = caption[:1021] + "…"

        thumb_path: Optional[str] = None
        if thumb_fid:
            try:
                thumb_path = await bot.download_media(
                    thumb_fid, file_name=os.path.join(tmp_dir, "thumb_")
                )
            except Exception:
                thumb_path = None

        # Fall back to yt-dlp's own thumbnail
        if not thumb_path:
            thumb_url = info.get("thumbnail")
            if thumb_url:
                thumb_path = await _fetch_thumbnail(
                    thumb_url, os.path.join(tmp_dir, "yt_thumb.jpg")
                )

        # 5. Upload
        file_size = os.path.getsize(file_path)
        await status.edit_text(
            f"📤 <b>Uploading…</b>\n📦 {humanbytes(file_size)}"
        )

        up_start = time.time()
        up_cb    = make_progress_callback(status, "Uploading", up_start)
        ext      = os.path.splitext(file_path)[1].lower()

        try:
            if ext in (".mp4", ".mkv", ".webm", ".mov", ".avi"):
                await client.send_video(
                    chat_id=uid,
                    video=file_path,
                    caption=caption,
                    duration=int(duration),
                    width=info.get("width") or 0,
                    height=info.get("height") or 0,
                    thumb=thumb_path,
                    supports_streaming=True,
                    progress=up_cb,
                )
            elif ext in (".mp3", ".m4a", ".ogg", ".flac", ".wav", ".opus"):
                await client.send_audio(
                    chat_id=uid,
                    audio=file_path,
                    caption=caption,
                    duration=int(duration),
                    title=title,
                    performer=uploader,
                    thumb=thumb_path,
                    progress=up_cb,
                )
            else:
                await client.send_document(
                    chat_id=uid,
                    document=file_path,
                    caption=caption,
                    thumb=thumb_path,
                    progress=up_cb,
                )

            await inc_downloads()
            try:
                await status.delete()
            except Exception:
                pass

            await log_to_channel(
                client,
                f"📥 <b>yt-dlp download</b>\n"
                f"User: <code>{uid}</code>\n"
                f"URL: <code>{text[:200]}</code>\n"
                f"Title: {title}",
            )

        except Exception as e:
            logger.error("Upload error: %s", e)
            await status.edit_text(f"❌ Upload failed:\n<code>{e}</code>")
            schedule_delete(status)
        finally:
            _cleanup_dir(tmp_dir)
