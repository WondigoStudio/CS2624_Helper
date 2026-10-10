import asyncio
import html

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ..config import GROQ_API_KEY, GROQ_WHISPER_MODEL, logger, requests


def _quote(text: str, first: bool = False) -> str:
    body = f"<blockquote expandable>{html.escape(text)}</blockquote>"
    return f"🗣 Транскрипция:\n{body}" if first else body


def _split_text(text: str, limit: int) -> list:
    """Cuts on spaces/line breaks so words are not broken in half."""
    parts = []
    while len(text) > limit:
        cut = max(text.rfind("\n", 0, limit), text.rfind(" ", 0, limit))
        if cut < limit // 2:
            cut = limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    parts.append(text)
    return parts


# ---------------------------------------------------------------------------
# Voice/audio/video transcription (Groq's free Whisper API)
# ---------------------------------------------------------------------------
def _transcribe_with_groq(audio_bytes: bytes, filename: str) -> str:
    """Blocking HTTP call — always run this via asyncio.to_thread so it
    doesn't stall the bot's event loop while waiting on the network."""
    resp = requests.post(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        files={"file": (filename, bytes(audio_bytes))},
        data={"model": GROQ_WHISPER_MODEL, "response_format": "json"},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json().get("text", "").strip()


async def handle_transcribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    media = msg.voice or msg.audio or msg.video_note or msg.video
    if media is None:
        return

    # Telegram's own size cap for bots downloading files is 20 MB; Groq's
    # free tier also caps around 25 MB — bail out early with a clear reason
    # instead of a confusing failure partway through.
    if media.file_size and media.file_size > 20 * 1024 * 1024:
        await msg.reply_text("Это сообщение слишком большое, чтобы распознать (лимит ~20 МБ).")
        return

    status = await msg.reply_text("🎙 Распознаю речь…")
    try:
        tg_file = await context.bot.get_file(media.file_id)
        audio_bytes = await tg_file.download_as_bytearray()

        if msg.voice:
            filename = "voice.ogg"
        elif msg.video_note:
            filename = "video_note.mp4"
        elif msg.video:
            filename = "video.mp4"
        else:
            filename = getattr(media, "file_name", None) or "audio.mp3"

        text = await asyncio.to_thread(_transcribe_with_groq, audio_bytes, filename)

        if not text:
            await status.edit_text("Не удалось разобрать речь — похоже, там тишина или шум.")
            return
        # A collapsible quote: long transcripts show a few lines with "expand" instead of a wall of text.
        # (Telegram caps a message at 4096 characters, so a very long text goes out in several messages.)
        parts = _split_text(text, 3500)
        await status.edit_text(_quote(parts[0], first=True), parse_mode=ParseMode.HTML)
        for extra in parts[1:]:
            await msg.reply_text(_quote(extra), parse_mode=ParseMode.HTML)
    except requests.exceptions.RequestException as e:
        logger.warning("Groq transcription request failed: %s", e)
        await status.edit_text("Не получилось распознать — сервис транскрипции сейчас недоступен.")
    except Exception as e:
        logger.warning("Transcription failed: %s", e)
        await status.edit_text("Не получилось распознать это сообщение.")
