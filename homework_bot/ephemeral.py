"""Ephemeral messages (Bot API 10.2+): a group message that only one person
(and the bot) can see. python-telegram-bot 22.5 has no wrapper for it yet, so
the raw API call is used. Everything here is best-effort: if Telegram rejects
the request (old client/server, unsupported chat, ...), callers get False and
fall back to the ordinary public reply."""

import os

from telegram import Bot

from .config import logger

EPHEMERAL_ENABLED = os.environ.get("EPHEMERAL_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")


async def send_ephemeral(bot: Bot, chat_id: int, user_id: int, text: str,
                         callback_query_id: str = None, thread_id: int = None) -> bool:
    """Show `text` to `user_id` only. True if Telegram accepted it."""
    if not EPHEMERAL_ENABLED:
        return False
    params = {"receiver_user_id": user_id}
    if callback_query_id:
        params["callback_query_id"] = callback_query_id
    kwargs = {"chat_id": chat_id, "text": text[:4096], "ephemeral_message_parameters": params}
    if thread_id:
        kwargs["message_thread_id"] = thread_id
    try:
        await bot.do_api_request("sendMessage", api_kwargs=kwargs)
        return True
    except Exception as e:
        logger.warning("Ephemeral message failed (falling back to a normal reply): %s", e)
        return False
