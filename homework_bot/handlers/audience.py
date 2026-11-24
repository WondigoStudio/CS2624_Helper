"""/audience — admin switch: who gets the bot's own (unprompted) messages."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from .. import audience as A
from ..db import list_known_users, register_chat
from ..permissions import is_admin, user_perms


def _count():
    people = groups = allowed = 0
    for r in list_known_users():
        if r["chat_id"] < 0:
            groups += 1
        else:
            people += 1
            if A.is_admin(r["chat_id"]) or user_perms(r["chat_id"]):
                allowed += 1
    return people, groups, allowed


def _panel():
    m = A.mode()
    people, groups, allowed = _count()
    if m == A.PRIVATE:
        state = (f"🔒 <b>Только личка и только с разрешением</b>\nПолучают: {allowed} из {people} людей. "
                 f"Групп ({groups}) бот сам не пишет.")
    else:
        state = f"📣 <b>Как раньше: все чаты</b>\nПолучают все известные чаты: {people} людей и {groups} групп."
    text = ("📬 <b>Кому бот пишет сам</b>\n\nРасписание и погода по утрам, напоминания о заданиях, "
            "о парах и о дедлайнах.\nЛичные /remind и опрос группы (/setreport) от этого не зависят. "
            "Ответы на команды работают везде.\n\n" + state)
    rows = [[InlineKeyboardButton(("✅ " if m == A.PRIVATE else "") + "🔒 Личка + разрешение", callback_data="aud:private")],
            [InlineKeyboardButton(("✅ " if m == A.EVERYONE else "") + "📣 Все чаты (как раньше)", callback_data="aud:all")]]
    return text, InlineKeyboardMarkup(rows)


async def audience_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Эта команда доступна только администраторам бота.")
        return
    text, kb = _panel()
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def audience_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("Только для администраторов.", show_alert=True)
        return
    A.set_mode(A.EVERYONE if query.data == "aud:all" else A.PRIVATE)
    await query.answer("Сохранено")
    text, kb = _panel()
    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass
