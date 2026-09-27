"""Paste-a-link media downloader.

- Instagram / TikTok / Twitter(X) links are auto-detected in any message
  and downloaded as video immediately (that's almost always what people
  want from those links).
- YouTube links are ambiguous (could be a 3-hour lecture or a song), so the
  bot instead offers two buttons: download as video, or extract just the
  audio as an mp3.

Both paths shell out to yt-dlp (a Python library, no external binary
needed) except mp3 extraction, which also needs the `ffmpeg` binary on the
host — see config.FFMPEG_AVAILABLE.
"""

import asyncio
import re
import tempfile
import uuid
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ..config import FFMPEG_AVAILABLE, MAX_DOWNLOAD_MB, MEDIA_DOWNLOAD_ENABLED, YTDLP_COOKIES_FILE, logger, yt_dlp

# Matches a URL whose host is one of the supported platforms. Doesn't try
# to validate the whole URL shape — just finds "https://.../..." starting
# at one of these domains, wherever it sits in the message.
_DIRECT_VIDEO_RE = re.compile(
    r"https?://(?:www\.|vt\.|vm\.)?"
    r"(?:instagram\.com|instagr\.am|tiktok\.com|twitter\.com|x\.com)"
    r"/\S+",
    re.IGNORECASE,
)
_YOUTUBE_RE = re.compile(
    r"https?://(?:www\.|m\.)?(?:youtube\.com/\S+|youtu\.be/\S+)",
    re.IGNORECASE,
)

MAX_BYTES = MAX_DOWNLOAD_MB * 1024 * 1024

# Pending YouTube links waiting for the person to pick video/mp3, keyed by a
# short token (the raw URL would blow past Telegram's 64-byte callback_data
# limit). Entries are removed once used; a restart just means old buttons
# stop working, which is an acceptable trade-off for staying in memory.
_pending_youtube_links: dict = {}


def _looks_downloadable(text: str) -> bool:
    return bool(text) and (_DIRECT_VIDEO_RE.search(text) or _YOUTUBE_RE.search(text))


async def _run_ydl(url: str, out_dir: str, *, audio_only: bool) -> Path:
    """Runs the actual (blocking) yt-dlp download in a worker thread and
    returns the path to the resulting file."""
    out_template = str(Path(out_dir) / "%(id)s.%(ext)s")

    if audio_only:
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": out_template,
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
            ],
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
        }
    else:
        ydl_opts = {
            # yt-dlp's own recommended general-purpose selector: best
            # video+audio it can merge, falling back to a single combined
            # format if that's all the site offers (some Shorts/TikTok
            # clips only expose one format — the old height<=1080 filter
            # could reject all of them and fail with "Requested format is
            # not available").
            "format": "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b",
            "outtmpl": out_template,
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
        }

    # Only YouTube tends to demand this ("Sign in to confirm you're not a
    # bot"); harmless to pass for every site, yt-dlp just ignores it if the
    # extractor doesn't use cookies.
    if YTDLP_COOKIES_FILE:
        ydl_opts["cookiefile"] = YTDLP_COOKIES_FILE

    # YouTube's normal "web" client increasingly demands a PO token before
    # it will even list any playable formats — without one, yt-dlp gets an
    # empty format list and fails with "Requested format is not available",
    # even though the video is perfectly public. The mobile app clients
    # (android/ios) don't enforce this as strictly, so trying them first is
    # the standard workaround and needs no extra setup or cookies. Only
    # relevant for YouTube; other extractors ignore this option entirely.
    ydl_opts["extractor_args"] = {"youtube": {"player_client": ["android", "ios", "web"]}}

    def _download():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(url, download=True)

    await asyncio.to_thread(_download)

    # Rather than trust yt-dlp's pre-postprocessing filename (mp3 extraction
    # renames the file afterwards), just look at what actually landed in
    # the empty temp dir we gave it — there's exactly one output file.
    produced = [p for p in Path(out_dir).iterdir() if p.is_file()]
    if not produced:
        raise FileNotFoundError("yt-dlp produced no output file")
    return produced[0]


async def _send_downloaded_file(message, path: Path, *, audio_only: bool, caption: str):
    size = path.stat().st_size
    if size > MAX_BYTES:
        await message.reply_text(
            f"Файл весит {size / 1024 / 1024:.0f} МБ — это больше лимита в "
            f"{MAX_DOWNLOAD_MB} МБ, который разрешает загружать обычный Telegram-бот. "
            "Попробуй ссылку покороче/пониже качеством."
        )
        return
    with open(path, "rb") as f:
        if audio_only:
            await message.reply_audio(audio=f, caption=caption)
        else:
            await message.reply_video(video=f, caption=caption, supports_streaming=True)


async def _download_and_send(update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, *, audio_only: bool):
    if not MEDIA_DOWNLOAD_ENABLED:
        return
    if audio_only and not FFMPEG_AVAILABLE:
        await update.effective_message.reply_text(
            "Извлечение аудио недоступно — на сервере не установлен ffmpeg."
        )
        return

    status = await update.effective_message.reply_text(
        "🎵 Скачиваю аудио…" if audio_only else "⏳ Скачиваю видео…"
    )
    with tempfile.TemporaryDirectory(prefix="ytdl_") as tmp_dir:
        try:
            path = await _run_ydl(url, tmp_dir, audio_only=audio_only)
            if not path.exists():
                raise FileNotFoundError(path)
            await _send_downloaded_file(
                update.effective_message, path, audio_only=audio_only, caption=""
            )
            await status.delete()
        except Exception as e:
            logger.warning("Media download failed for %s: %s", url, e)
            msg = str(e)
            if "Sign in to confirm" in msg:
                await status.edit_text(
                    "YouTube попросил подтвердить, что это не бот, и заблокировал "
                    "скачивание — так теперь бывает почти со всеми YouTube-ссылками. "
                    "Нужно один раз настроить cookies для бота (см. README, "
                    "переменная YTDLP_COOKIES_FILE). Instagram/TikTok/Twitter это не "
                    "затрагивает — там всё работает как обычно."
                )
            elif "Unexpected response from webpage request" in msg or "Requested format is not available" in msg:
                await status.edit_text(
                    "Платформа только что поменяла что-то в своём сайте, и наш "
                    "инструмент (yt-dlp) пока это не понимает — такое бывает и "
                    "обычно чинится в течение пары дней после обновления yt-dlp "
                    "до новой версии. Попробуй другую ссылку или зайди позже."
                )
            else:
                await status.edit_text(
                    "Не получилось скачать 😕 Ссылка приватная, недоступна в нашем "
                    "регионе, или платформа изменила формат — такое бывает."
                )


async def handle_media_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fires on any message containing a supported link. Instagram/TikTok/
    Twitter download immediately; YouTube asks video-or-mp3 first."""
    if not MEDIA_DOWNLOAD_ENABLED:
        return
    text = update.effective_message.text or update.effective_message.caption or ""

    direct_match = _DIRECT_VIDEO_RE.search(text)
    if direct_match:
        await _download_and_send(update, context, direct_match.group(0), audio_only=False)
        return

    yt_match = _YOUTUBE_RE.search(text)
    if yt_match:
        url = yt_match.group(0)
        token = uuid.uuid4().hex[:12]
        _pending_youtube_links[token] = url
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("🎬 Видео", callback_data=f"ytdl:video:{token}"),
                InlineKeyboardButton("🎵 Только звук (mp3)", callback_data=f"ytdl:audio:{token}"),
            ]
        ])
        await update.effective_message.reply_text(
            "Нашёл ссылку на YouTube — что прислать?", reply_markup=keyboard
        )


async def youtube_download_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, kind, token = query.data.split(":", 2)
    url = _pending_youtube_links.pop(token, None)
    if url is None:
        await query.edit_message_text("Ссылка устарела, пришли её ещё раз.")
        return
    await query.edit_message_reply_markup(reply_markup=None)
    await _download_and_send(update, context, url, audio_only=(kind == "audio"))
