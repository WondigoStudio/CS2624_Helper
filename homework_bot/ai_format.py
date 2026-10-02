"""Structuring long task descriptions with Groq's free chat models, so a
wall of pasted text turns into something with headings/bold/bullets instead
of one dense blob. Reuses the same GROQ_API_KEY as transcription/translation
(see config.py) — if it's not set, structuring is simply unavailable and
callers fall back to the plain text unchanged."""

import html
import re

from .config import GROQ_API_KEY, GROQ_TRANSLATE_MODEL, logger, requests

# Only a long-ish description is worth structuring — a one-liner gains
# nothing and would just waste an API call.
STRUCTURE_MIN_LENGTH = 220

# After the model answers, only <b> and <i> are trusted — anything else
# (real or hallucinated tags, stray angle brackets) gets HTML-escaped away
# rather than sent through, so a malformed reply can never break the
# Telegram message or inject something unexpected.
_ALLOWED_TAGS = ("b", "i")


def _sanitize_telegram_html(text: str) -> str:
    escaped = html.escape(text)
    for tag in _ALLOWED_TAGS:
        escaped = escaped.replace(f"&lt;{tag}&gt;", f"<{tag}>")
        escaped = escaped.replace(f"&lt;/{tag}&gt;", f"</{tag}>")
    return escaped


def _structure_with_groq(text: str) -> str:
    """Blocking HTTP call — run via asyncio.to_thread."""
    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        json={
            "model": GROQ_TRANSLATE_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You format messy homework-task descriptions for Telegram. "
                        "Restructure the user's text so it's easy to scan: split it "
                        "into short paragraphs or a bullet list (using a plain '- ' at "
                        "the start of a line), and use ONLY <b>...</b> and <i>...</i> "
                        "HTML tags to mark headings/key points/emphasis — no other HTML "
                        "tags, no markdown (**, #, etc.), no code blocks. Keep the "
                        "original language and every piece of actual content — do not "
                        "summarize, shorten, or add information that wasn't there. "
                        "Output ONLY the restructured text, nothing else."
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


async def maybe_structure_description(text: str):
    """Returns (final_text, is_html). If the text is short, Groq isn't
    configured, or the request fails for any reason, returns the original
    text unchanged with is_html=False — structuring is a nice-to-have, never
    something that should block adding a task."""
    if not GROQ_API_KEY or len(text) < STRUCTURE_MIN_LENGTH:
        return text, False
    import asyncio

    try:
        structured = await asyncio.to_thread(_structure_with_groq, text)
    except requests.exceptions.RequestException as e:
        logger.warning("Groq description structuring request failed: %s", e)
        return text, False
    except Exception as e:
        logger.warning("Description structuring failed: %s", e)
        return text, False
    if not structured:
        return text, False
    return _sanitize_telegram_html(structured), True


def strip_html_preview(text: str) -> str:
    """Plain-text preview of a (possibly HTML-structured) description, for
    contexts that aren't sent with parse_mode=HTML (e.g. the task list)."""
    return re.sub(r"<[^>]+>", "", text)
