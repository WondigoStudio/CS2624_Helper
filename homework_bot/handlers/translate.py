"""Translation features: reply-to-a-message @mention translation, the
dispatcher that also routes plain-word replies to the fun actions, and
inline mode (@botusername <text> from anywhere in Telegram)."""

import asyncio
import html

from telegram import InlineQueryResultArticle, InputTextMessageContent, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ..config import GROQ_API_KEY, GROQ_TRANSLATE_MODEL, TRANSLATE_ENABLED, logger, requests
from ..constants import ACTIONS
from .social import handle_action_reply

# Tracks the most recent inline query id per user, so that if they keep
# typing, only the latest keystroke actually triggers a Groq call — avoids
# hammering the API (and its rate limit) once per character.
_latest_inline_query_id: dict = {}


# ---------------------------------------------------------------------------
# Translation to Russian (reply + mention the bot) — Groq's free chat models
# ---------------------------------------------------------------------------
def _translate_with_groq(text: str) -> str:
    """Blocking HTTP call — run via asyncio.to_thread. Uses a plain chat
    completion (Groq's Whisper endpoint only transcribes, it doesn't
    translate arbitrary already-written text)."""
    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        json={
            "model": GROQ_TRANSLATE_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a translator. Translate the user's message into "
                        "Russian, however many languages it mixes or whatever language "
                        "it's already in. Output ONLY the translation itself — no "
                        "quotes, no explanations, no language names, nothing else. "
                        "If the text is already entirely in Russian, output it unchanged."
                    ),
                },
                {"role": "user", "content": text},
            ],
            "temperature": 0.2,
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def _message_mentions_bot(update: Update, bot_username: str) -> bool:
    msg = update.message
    if not msg.text or not bot_username:
        return False
    return f"@{bot_username.lower()}" in msg.text.lower()



async def handle_translate_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    original = msg.reply_to_message
    if original is None:
        return

    action_key = (msg.text or "").strip().lower()
    if action_key in ACTIONS:
        await handle_action_reply(update, context, action_key)
        return

    if not TRANSLATE_ENABLED or not _message_mentions_bot(update, context.bot.username):
        return

    source_text = original.text or original.caption
    if not source_text:
        await msg.reply_text("В этом сообщении нет текста для перевода.")
        return

    status = await msg.reply_text("🌐 Перевожу…")
    try:
        translated = await asyncio.to_thread(_translate_with_groq, source_text)
        quoted = f"🌐 Перевод:\n<blockquote>{html.escape(translated)}</blockquote>"
        await status.edit_text(quoted, parse_mode=ParseMode.HTML)
    except requests.exceptions.RequestException as e:
        logger.warning("Groq translation request failed: %s", e)
        await status.edit_text("Не получилось перевести — сервис сейчас недоступен.")
    except Exception as e:
        logger.warning("Translation failed: %s", e)
        await status.edit_text("Не получилось перевести это сообщение.")

 



async def inline_translate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query
    text = query.query.strip()
    user_id = update.effective_user.id if update.effective_user else 0

    if not text:
        await query.answer(
            [
                InlineQueryResultArticle(
                    id="hint",
                    title="Напиши текст для перевода на русский",
                    description="Например: @имя_бота Hello, how are you?",
                    input_message_content=InputTextMessageContent(
                        "Напиши что-нибудь после имени бота, чтобы перевести на русский."
                    ),
                )
            ],
            cache_time=1,
            is_personal=True,
        )
        return

    # debounce: wait a beat, then bail out if a newer keystroke already
    # superseded this query (Telegram fires a new inline_query on every
    # pause in typing, not just when the person is "done")
    _latest_inline_query_id[user_id] = query.id
    await asyncio.sleep(0.6)
    if _latest_inline_query_id.get(user_id) != query.id:
        return

    try:
        translated = await asyncio.to_thread(_translate_with_groq, text)
    except Exception as e:
        logger.warning("Inline translation failed: %s", e)
        translated = None

    if not translated:
        results = [
            InlineQueryResultArticle(
                id="error",
                title="Не получилось перевести — попробуй ещё раз",
                description=text[:80],
                input_message_content=InputTextMessageContent(text),
            )
        ]
    else:
        results = [
            InlineQueryResultArticle(
                id="translation",
                title="🌐 Отправить перевод",
                description=translated[:100],
                input_message_content=InputTextMessageContent(translated),
            )
        ]
    await query.answer(results, cache_time=1, is_personal=True)


