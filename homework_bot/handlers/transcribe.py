import asyncio
import html

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ..config import GROQ_API_KEY, GROQ_WHISPER_MODEL, logger, requests


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
        quoted = f"🗣 Транскрипция:\n<blockquote>{html.escape(text)}</blockquote>"
        await status.edit_text(quoted, parse_mode=ParseMode.HTML)
    except requests.exceptions.RequestException as e:
        logger.warning("Groq transcription request failed: %s", e)
        await status.edit_text("Не получилось распознать — сервис транскрипции сейчас недоступен.")
    except Exception as e:
        logger.warning("Transcription failed: %s", e)
        await status.edit_text("Не получилось распознать это сообщение.")


