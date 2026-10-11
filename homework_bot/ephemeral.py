"""Ephemeral messages (Bot API 10.2+): a group message that only one person
(and the bot) can see. python-telegram-bot 22.5 has no wrapper for it yet, so
the raw API call is used. Everything here is best-effort: if Telegram rejects
the request (old client/server, unsupported chat, ...), callers get False and
fall back to something else."""

import os

from telegram import Bot, InlineKeyboardMarkup

from .config import logger

EPHEMERAL_ENABLED = os.environ.get("EPHEMERAL_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")


def _params(user_id, callback_query_id):
    p = {"receiver_user_id": user_id}
    if callback_query_id:
        p["callback_query_id"] = callback_query_id
    return p


async def _call(bot: Bot, method: str, kwargs: dict) -> bool:
    if not EPHEMERAL_ENABLED:
        return False
    try:
        await bot.do_api_request(method, api_kwargs=kwargs)
        return True
    except Exception as e:
        logger.warning("Ephemeral %s failed (falling back): %s", method, e)
        return False


async def send_ephemeral(bot: Bot, chat_id: int, user_id: int, text: str,
                         callback_query_id: str = None, thread_id: int = None,
                         reply_markup: InlineKeyboardMarkup = None, parse_mode: str = None) -> bool:
    """Show `text` to `user_id` only. True if Telegram accepted it."""
    kwargs = {"chat_id": chat_id, "text": text[:4096],
              "ephemeral_message_parameters": _params(user_id, callback_query_id)}
    if thread_id:
        kwargs["message_thread_id"] = thread_id
    if reply_markup is not None:
        kwargs["reply_markup"] = reply_markup.to_dict()
    if parse_mode:
        kwargs["parse_mode"] = parse_mode
    return await _call(bot, "sendMessage", kwargs)


async def send_ephemeral_file(bot: Bot, chat_id: int, user_id: int, kind: str, file_id: str,
                              caption: str = None, callback_query_id: str = None,
                              thread_id: int = None) -> bool:
    """A photo or document that only `user_id` can see."""
    method, field = ("sendDocument", "document") if kind == "document" else ("sendPhoto", "photo")
    kwargs = {"chat_id": chat_id, field: file_id,
              "ephemeral_message_parameters": _params(user_id, callback_query_id)}
    if caption:
        kwargs["caption"] = caption[:1024]
    if thread_id:
        kwargs["message_thread_id"] = thread_id
    return await _call(bot, method, kwargs)
