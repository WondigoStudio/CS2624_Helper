"""Translation features: reply-to-a-message @mention translation, the
dispatcher that also routes plain-word replies to the fun actions, and
inline mode (@botusername <text> from anywhere in Telegram).

The target language is Russian by default. Start with a language name to pick
another one: `@bot english привет`, `@bot на казахский привет`, or reply to a
message with `@bot english`."""

import asyncio
import html
import re

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

DEFAULT_LANG = "Russian"

# language name used in the prompt -> words people type for it
_LANGS = {
    "English": ["en", "eng", "english", "англ", "английский", "английском", "английскии"],
    "Russian": ["ru", "rus", "russian", "рус", "русский", "русском"],
    "Kazakh": ["kk", "kz", "kaz", "kazakh", "каз", "казахский", "казахском", "қазақша"],
    "Turkish": ["tr", "turkish", "турецкий", "турецком"],
    "German": ["de", "ger", "german", "немецкий", "немецком"],
    "French": ["fr", "french", "французский", "французском"],
    "Spanish": ["es", "spa", "spanish", "испанский", "испанском"],
    "Italian": ["it", "italian", "итальянский", "итальянском"],
    "Chinese": ["zh", "cn", "chinese", "китайский", "китайском"],
    "Japanese": ["ja", "jp", "japanese", "японский", "японском"],
    "Korean": ["ko", "korean", "корейский", "корейском"],
    "Arabic": ["ar", "arabic", "арабский", "арабском"],
    "Ukrainian": ["uk", "ua", "ukrainian", "украинский", "украинском"],
    "Portuguese": ["pt", "portuguese", "португальский", "португальском"],
    "Polish": ["pl", "polish", "польский", "польском"],
    "Uzbek": ["uz", "uzbek", "узбекский", "узбекском"],
}
_ALIAS = {w: name for name, words in _LANGS.items() for w in words}
_PREPOSITIONS = {"на", "to", "in", "в"}


def _lang_for(word: str):
    return _ALIAS.get(re.sub(r"[^\w]", "", word.lower()))


def split_language(text: str):
    """('English', 'привет') for 'english привет' / 'на английский привет';
    (None, text) when the text does not start with a language name. A language
    name alone (no text after it) is treated as ordinary text."""
    parts = text.strip().split(None, 2)
    if not parts:
        return None, text
    i = 1 if parts[0].lower() in _PREPOSITIONS and len(parts) > 1 else 0
    lang = _lang_for(parts[i])
    rest = " ".join(text.strip().split()[i + 1:])
    if lang and rest:
        return lang, rest
    return None, text


# ---------------------------------------------------------------------------
# Translation (reply + mention the bot, or inline) — Groq's free chat models
# ---------------------------------------------------------------------------
def _translate_with_groq(text: str, target: str = DEFAULT_LANG) -> str:
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
                        f"You are a translator. Translate the user's message into "
                        f"{target}, however many languages it mixes or whatever language "
                        f"it's already in. Output ONLY the translation itself — no "
                        f"quotes, no explanations, no language names, nothing else. "
                        f"If the text is already entirely in {target}, output it unchanged."
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


def _language_after_mention(text: str, bot_username: str) -> str:
    """'@bot english' (a reply) -> 'English'; anything else -> Russian."""
    rest = re.sub(rf"@{re.escape(bot_username)}", " ", text, flags=re.I).strip()
    if not rest:
        return DEFAULT_LANG
    words = rest.split()
    i = 1 if words[0].lower() in _PREPOSITIONS and len(words) > 1 else 0
    return _lang_for(words[i]) or DEFAULT_LANG


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

    target = _language_after_mention(msg.text or "", context.bot.username)
    status = await msg.reply_text("🌐 Перевожу…")
    try:
        translated = await asyncio.to_thread(_translate_with_groq, source_text, target)
        quoted = f"🌐 Перевод ({html.escape(target)}):\n<blockquote>{html.escape(translated)}</blockquote>"
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
                    title="Напиши текст для перевода (по умолчанию на русский)",
                    description="Другой язык: @имя_бота english привет · на казахский Hello",
                    input_message_content=InputTextMessageContent(
                        "Напиши текст после имени бота. Чтобы выбрать язык, начни с его названия: english привет."
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

    lang, body = split_language(text)
    target = lang or DEFAULT_LANG
    try:
        translated = await asyncio.to_thread(_translate_with_groq, body, target)
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
                title=f"🌐 Отправить перевод ({target})",
                description=translated[:100],
                input_message_content=InputTextMessageContent(translated),
            )
        ]
    await query.answer(results, cache_time=1, is_personal=True)
