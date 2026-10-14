"""/settings — what the bot sends to a group. Run it in the group, or in a
private chat to pick one of the groups you administer. Only group admins
(and bot admins) can change anything. /morning_off and /morning_on are
shortcuts for the four morning switches."""

import html

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ..db import (
    group_flag, list_known_users, poll_enabled, register_chat, roles_enabled,
    set_group_flag, set_poll_enabled, set_roles_enabled,
)
from ..roles import is_group_admin

# key, label, what it does
SWITCHES = [
    ("schedule", "🗓 Расписание утром", "утреннее расписание группы"),
    ("weather", "🌤 Погода утром", "утренняя сводка погоды"),
    ("tasks", "📚 Напоминание о заданиях", "утреннее напоминание о заданиях"),
    ("poll", "📊 Утренний опрос", "опрос «кто придёт» с закрепом"),
    ("lessons", "⏰ За 30 мин до пары", "напоминание перед парой и фото кабинета"),
    ("deadlines", "⏳ За час до дедлайна", "напоминание о скором дедлайне"),
    ("roles", "🎭 Роли", "звания участников (/roles)"),
]


def _get(chat_id: int, key: str) -> bool:
    if key == "poll":
        return poll_enabled(chat_id)
    if key == "roles":
        return roles_enabled(chat_id)
    return group_flag(chat_id, key, True)


def _set(chat_id: int, key: str, value: bool, by: int):
    if key == "poll":
        set_poll_enabled(chat_id, value)
    elif key == "roles":
        set_roles_enabled(chat_id, value, by)
    else:
        set_group_flag(chat_id, key, value)


def _menu(chat_id: int, title: str):
    text = (
        f"⚙️ <b>Настройки группы</b>\n{html.escape(title)}\n\n"
        "Нажми на пункт, чтобы включить или выключить. ✅ — приходит, ❌ — не приходит."
    )
    rows = [
        [InlineKeyboardButton(("✅ " if _get(chat_id, k) else "❌ ") + label, callback_data=f"gs:{chat_id}:{k}")]
        for k, label, _ in SWITCHES
    ]
    return text, InlineKeyboardMarkup(rows)


async def settings_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    chat = update.effective_chat
    uid = update.effective_user.id
    if chat.type in ("group", "supergroup"):
        if not await is_group_admin(context.bot, chat.id, uid):
            await update.message.reply_text("Настройки группы может менять только админ группы.")
            return
        text, kb = _menu(chat.id, chat.title or "")
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
        return
    rows = []
    for r in list_known_users():
        if r["chat_id"] < 0 and await is_group_admin(context.bot, r["chat_id"], uid):
            try:
                title = (await context.bot.get_chat(r["chat_id"])).title or str(r["chat_id"])
            except Exception:
                continue
            rows.append([InlineKeyboardButton(f"👥 {title[:40]}", callback_data=f"gs:pick:{r['chat_id']}")])
    if not rows:
        await update.message.reply_text("Не нашёл групп, где ты админ и есть бот. Напиши /settings прямо в группе.")
        return
    await update.message.reply_text("Какую группу настроить?", reply_markup=InlineKeyboardMarkup(rows[:20]))


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    parts = query.data.split(":")
    uid = update.effective_user.id
    if parts[1] == "pick":
        chat_id = int(parts[2])
        key = None
    else:
        chat_id, key = int(parts[1]), parts[2]
    if not await is_group_admin(context.bot, chat_id, uid):
        await query.answer("Менять настройки может только админ группы.", show_alert=True)
        return
    if key:
        if key not in {k for k, _, _ in SWITCHES}:
            await query.answer()
            return
        _set(chat_id, key, not _get(chat_id, key), uid)
    try:
        title = (await context.bot.get_chat(chat_id)).title or ""
    except Exception:
        title = ""
    text, kb = _menu(chat_id, title)
    await query.answer("Сохранено ✅" if key else None)
    await query.edit_message_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)


async def _morning(update: Update, context: ContextTypes.DEFAULT_TYPE, on: bool):
    register_chat(update)
    chat, uid = update.effective_chat, update.effective_user.id
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Эта команда работает в группе. Для выбора группы из лички: /settings")
        return
    if not await is_group_admin(context.bot, chat.id, uid):
        await update.message.reply_text("Менять это может только админ группы.")
        return
    for key in ("schedule", "weather", "tasks"):
        _set(chat.id, key, on, uid)
    if not on:
        _set(chat.id, "poll", False, uid)
    await update.message.reply_text(
        "🔔 Утренние сообщения включены (расписание, погода, задания). Опрос — отдельно: /settings"
        if on else "🔕 Утренние сообщения выключены (расписание, погода, задания, опрос). Тонкая настройка: /settings"
    )


async def morning_off_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _morning(update, context, False)


async def morning_on_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _morning(update, context, True)
