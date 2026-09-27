"""Paste-a-link media downloader.

- Instagram / TikTok / Twitter(X) links are auto-detected in any message
  and downloaded as video immediately (that's almost always what people
  want from those links).
- YouTube links are ambiguous (could be a 3-hour lecture or a song), so the
  bot instead offers two buttons: download as video, or extract just the
  audio as an mp3.
- Spotify track links have no video at all, so the bot always treats them
  as "give me the mp3": it reads the track/artist name off the Spotify page
  (Spotify itself never gives out the actual audio — its files are DRM
  protected), searches YouTube for that song, and downloads/extracts audio
  from whatever YouTube finds.

Both paths shell out to yt-dlp (a Python library, no external binary
needed) except mp3 extraction, which also needs the `ffmpeg` binary on the
host — see config.FFMPEG_AVAILABLE.
"""

import asyncio
import html
import json
import re
import tempfile
import uuid
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo, Update
from telegram.ext import ContextTypes

from ..config import (
    FFMPEG_AVAILABLE,
    MAX_DOWNLOAD_MB,
    MEDIA_DOWNLOAD_ENABLED,
    YTDLP_COOKIES_FILE,
    logger,
    requests,
    yt_dlp,
)

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
_SPOTIFY_TRACK_RE = re.compile(
    r"https?://open\.spotify\.com/(?:intl-\w+/)?track/\S+",
    re.IGNORECASE,
)
# Spotify's track page <title> looks like "Song Name - song by Artist Name | Spotify".
_SPOTIFY_TITLE_RE = re.compile(r"<title>(.*?) - song by (.*?) \| Spotify</title>")

MAX_BYTES = MAX_DOWNLOAD_MB * 1024 * 1024

# Pending YouTube links waiting for the person to pick video/mp3, keyed by a
# short token (the raw URL would blow past Telegram's 64-byte callback_data
# limit). Entries are removed once used; a restart just means old buttons
# stop working, which is an acceptable trade-off for staying in memory.
_pending_youtube_links: dict = {}


def _looks_downloadable(text: str) -> bool:
    return bool(text) and (
        _DIRECT_VIDEO_RE.search(text) or _YOUTUBE_RE.search(text) or _SPOTIFY_TRACK_RE.search(text)
    )


def _fetch_spotify_track(url: str) -> tuple[str, str] | None:
    """Reads the track/artist name off a Spotify track page's <title> tag.
    No Spotify API key needed — this is just the public page's HTML, the
    same thing a browser would show as the tab title. Returns None if the
    page couldn't be fetched or didn't match the expected title format
    (e.g. Spotify changed their markup, or it's a playlist/album link, not
    a track)."""
    if requests is None:
        return None
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=10,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.warning("Failed to fetch Spotify page %s: %s", url, e)
        return None
    match = _SPOTIFY_TITLE_RE.search(resp.text)
    if not match:
        return None
    title, artist = html.unescape(match.group(1)).strip(), html.unescape(match.group(2)).strip()
    return (title, artist) if title and artist else None


_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}


_TIKTOK_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.tiktok.com/",
}
_TIKTOK_JSON_RE = re.compile(
    r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>', re.DOTALL
)


def _fetch_tiktok_photo_post(url: str) -> dict | None:
    """yt-dlp's TikTok extractor only knows how to handle /video/ posts, not
    the /photo/ (slideshow) ones — so for those, fall back to reading the
    same data TikTok's own web page uses to render itself: a JSON blob
    embedded directly in the HTML (no login/API key needed, it's public).
    Returns {'images': [url, ...], 'music': url|None}, or None if the page
    couldn't be fetched or its structure didn't match what we expect (TikTok
    can and does change this layout without notice)."""
    if requests is None:
        return None
    try:
        resp = requests.get(url, headers=_TIKTOK_HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        logger.warning("Failed to fetch TikTok photo post %s: %s", url, e)
        return None

    match = _TIKTOK_JSON_RE.search(resp.text)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
        item = data["__DEFAULT_SCOPE__"]["webapp.video-detail"]["itemInfo"]["itemStruct"]
        image_post = item.get("imagePost")
        if not image_post:
            return None
        images = []
        for img in image_post.get("images", []):
            url_list = (img.get("imageURL") or {}).get("urlList") or []
            if url_list:
                images.append(url_list[0])
        if not images:
            return None
        music_url = None
        play_url = (item.get("music") or {}).get("playUrl")
        if isinstance(play_url, dict):
            url_list = play_url.get("urlList") or []
            music_url = url_list[0] if url_list else None
        elif isinstance(play_url, str):
            music_url = play_url or None
        return {"images": images, "music": music_url}
    except Exception as e:
        logger.warning("Failed to parse TikTok photo post JSON for %s: %s", url, e)
        return None


async def _download_tiktok_photo_post(url: str, out_dir: str) -> dict:
    """Downloads a TikTok photo/slideshow post's images (and its background
    music, if any) straight over HTTP using the URLs _fetch_tiktok_photo_post
    found, bypassing yt-dlp entirely for this case. Returns
    {'images': [Path, ...], 'music': Path|None} — kept separate because the
    music is a plain audio track, not part of the photo album, and has to be
    sent to Telegram differently (reply_audio, not as an album item)."""
    info = await asyncio.to_thread(_fetch_tiktok_photo_post, url)
    if not info:
        raise RuntimeError("TikTok photo post extraction failed — page structure didn't match")

    def _download_all() -> dict:
        images = []
        for i, img_url in enumerate(info["images"]):
            r = requests.get(img_url, headers=_TIKTOK_HEADERS, timeout=20)
            r.raise_for_status()
            ext = ".jpg"
            content_type = r.headers.get("Content-Type", "")
            if "png" in content_type:
                ext = ".png"
            elif "webp" in content_type:
                ext = ".webp"
            path = Path(out_dir) / f"{i:03d}_photo{ext}"
            path.write_bytes(r.content)
            images.append(path)

        music_path = None
        if info.get("music"):
            try:
                r = requests.get(info["music"], headers=_TIKTOK_HEADERS, timeout=20)
                r.raise_for_status()
                music_path = Path(out_dir) / "music.mp3"
                music_path.write_bytes(r.content)
            except Exception as e:
                # Background music is a nice-to-have, not worth failing the
                # whole post over if TikTok's audio CDN hiccups.
                logger.warning("Failed to download TikTok photo post music for %s: %s", url, e)

        return {"images": images, "music": music_path}

    return await asyncio.to_thread(_download_all)


async def _run_ydl(url: str, out_dir: str, *, audio_only: bool, allow_playlist: bool = False) -> list[Path]:
    """Runs the actual (blocking) yt-dlp download in a worker thread and
    returns the paths to the resulting file(s) — usually one, but an
    Instagram/TikTok carousel post (several photos/videos in one post) can
    produce several, which is what allow_playlist is for: yt-dlp treats a
    carousel as a "playlist" of its items, and noplaylist=True (the default
    everywhere else, so a YouTube video that happens to be part of some
    playlist doesn't unexpectedly pull in the whole thing) would otherwise
    silently keep just the first item."""
    out_template = str(Path(out_dir) / "%(playlist_index)s_%(id)s.%(ext)s")

    if audio_only:
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": out_template,
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
            ],
            "noplaylist": not allow_playlist,
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
            "noplaylist": not allow_playlist,
            "ignoreerrors": "only_download" if allow_playlist else False,
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
    # the standard cookie-free workaround. BUT those mobile clients don't
    # use cookies at all — if we *do* have cookies configured, "web" is the
    # only client that actually benefits from them, so it has to go first
    # or yt-dlp fails on the cookie-blind mobile clients before ever trying
    # the one client that would have worked.
    youtube_clients = ["web", "android", "ios"] if YTDLP_COOKIES_FILE else ["android", "ios", "web"]

    # TikTok's web extractor sometimes gets served a bot-check/placeholder
    # page instead of the real one ("Unexpected response from webpage
    # request"), especially without a convincing desktop User-Agent, or
    # depending on which of TikTok's API edge hosts answers. Neither of
    # these fully guarantees success — TikTok's protection changes often —
    # but they're the standard workarounds and don't affect other sites.
    ydl_opts["extractor_args"] = {
        "youtube": {"player_client": youtube_clients},
        "tiktok": {"api_hostname": ["api16-normal-c-useast1a.tiktokv.com"]},
    }
    ydl_opts["http_headers"] = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    }

    def _download():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(url, download=True)

    await asyncio.to_thread(_download)

    # Rather than trust yt-dlp's pre-postprocessing filename (mp3 extraction
    # renames the file afterwards), just look at what actually landed in
    # the temp dir we gave it — sorted so a carousel's photos/videos keep
    # the order they were posted in (the playlist_index prefix in
    # out_template sorts correctly as a string up to 9999 items, plenty for
    # any real post).
    produced = sorted((p for p in Path(out_dir).iterdir() if p.is_file()), key=lambda p: p.name)
    if not produced:
        raise FileNotFoundError("yt-dlp produced no output file")
    return produced


async def _send_downloaded_files(message, paths: list[Path], *, audio_only: bool, caption: str):
    oversized = [p for p in paths if p.stat().st_size > MAX_BYTES]
    paths = [p for p in paths if p not in oversized]
    if oversized and not paths:
        await message.reply_text(
            f"Файл весит больше лимита в {MAX_DOWNLOAD_MB} МБ, который разрешает "
            "загружать обычный Telegram-бот. Попробуй ссылку покороче/пониже качеством."
        )
        return
    if not paths:
        raise FileNotFoundError("nothing left to send")

    if audio_only:
        # Always exactly one file on this path (mp3 extraction of a single
        # track), so no album/multi-file case to handle here.
        with open(paths[0], "rb") as f:
            await message.reply_audio(audio=f, caption=caption)
    elif len(paths) == 1:
        path = paths[0]
        with open(path, "rb") as f:
            if path.suffix.lower() in _IMAGE_EXTS:
                await message.reply_photo(photo=f, caption=caption)
            else:
                await message.reply_video(video=f, caption=caption, supports_streaming=True)
    else:
        # A carousel post: several photos and/or video clips in one post.
        # Telegram's media-group ("album") API caps at 10 items per group,
        # and wants every file handle kept open until send_media_group
        # actually uploads them — hence the nested ExitStack instead of a
        # `with open(...) as f` per item.
        from contextlib import ExitStack

        for batch_start in range(0, len(paths), 10):
            batch = paths[batch_start : batch_start + 10]
            with ExitStack() as stack:
                media = []
                for i, path in enumerate(batch):
                    f = stack.enter_context(open(path, "rb"))
                    item_caption = caption if (batch_start == 0 and i == 0) else None
                    if path.suffix.lower() in _IMAGE_EXTS:
                        media.append(InputMediaPhoto(media=f, caption=item_caption))
                    else:
                        media.append(InputMediaVideo(media=f, caption=item_caption, supports_streaming=True))
                await message.reply_media_group(media=media)
    if oversized:
        await message.reply_text(
            f"{len(oversized)} файл(ов) из поста весят больше лимита в {MAX_DOWNLOAD_MB} МБ "
            "и не отправлены."
        )


async def _download_and_send(
    update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, *, audio_only: bool, allow_playlist: bool = False
):
    if not MEDIA_DOWNLOAD_ENABLED:
        return
    if audio_only and not FFMPEG_AVAILABLE:
        await update.effective_message.reply_text(
            "Извлечение аудио недоступно — на сервере не установлен ffmpeg."
        )
        return

    status = await update.effective_message.reply_text(
        "🎵 Скачиваю аудио…" if audio_only else "⏳ Скачиваю…"
    )
    is_tiktok_photo_post = "tiktok.com" in url and "/photo/" in url
    with tempfile.TemporaryDirectory(prefix="ytdl_") as tmp_dir:
        try:
            if is_tiktok_photo_post:
                # yt-dlp has no extractor at all for TikTok's photo/slideshow
                # posts (only real videos), so this bypasses it entirely and
                # reads the images straight off TikTok's own page data.
                post = await _download_tiktok_photo_post(url, tmp_dir)
                await _send_downloaded_files(
                    update.effective_message, post["images"], audio_only=False, caption=""
                )
                if post["music"]:
                    with open(post["music"], "rb") as f:
                        await update.effective_message.reply_audio(audio=f)
            else:
                paths = await _run_ydl(url, tmp_dir, audio_only=audio_only, allow_playlist=allow_playlist)
                await _send_downloaded_files(
                    update.effective_message, paths, audio_only=audio_only, caption=""
                )
            await status.delete()
        except Exception as e:
            logger.warning("Media download failed for %s: %s", url, e)
            msg = str(e)
            if is_tiktok_photo_post:
                await status.edit_text(
                    "Не получилось скачать фото из этого TikTok-поста — TikTok мог "
                    "поменять формат страницы. Попробуй ещё раз или другую ссылку."
                )
            elif "Sign in to confirm" in msg:
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
        await _download_and_send(update, context, direct_match.group(0), audio_only=False, allow_playlist=True)
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
