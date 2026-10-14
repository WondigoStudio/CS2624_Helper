"""/morning_off and /morning_on: switch the bot's morning messages in a group."""

from telegram import Update
from telegram.ext import ContextTypes

from ..db import register_chat, set_morning_off
from ..roles import is_group_admin


async def _toggle(update: Update, context: ContextTypes.DEFAULT_TYPE, off: bool):
    register_chat(update)
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Эта команда работает в группе. В личке рассылки отключает /start → настройки или остановка бота.")
        return
    chat_id, uid = update.effective_chat.id, update.effective_user.id
    if not await is_group_admin(context.bot, chat_id, uid):
        await update.message.reply_text("Менять это может только админ группы.")
        return
    set_morning_off(chat_id, off, uid)
    if off:
        await update.message.reply_text(
            "🔕 Утренние сообщения в этой группе выключены: расписание, погода, напоминание о заданиях "
            "и утренний опрос. Вернуть: /morning_on"
        )
    else:
        await update.message.reply_text(
            "🔔 Утренние сообщения снова включены (расписание, погода, напоминание о заданиях). "
            "Утренний опрос включается отдельно командой /set_report."
        )


async def morning_off_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _toggle(update, context, True)


async def morning_on_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _toggle(update, context, False)
