"""Group role commands. Roles work only in groups where an admin ran /roles_on."""

import html

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ..db import (
    display_name, find_user_by_username, list_group_roles, list_known_users, register_chat,
    remove_group_role, roles_enabled, set_group_role, set_roles_enabled,
)
from ..roles import SUGGESTIONS, clean_title, is_group_admin, role_label


def _is_group(update: Update) -> bool:
    return update.effective_chat.type in ("group", "supergroup")


async def _group_only(update: Update) -> bool:
    if not _is_group(update):
        await update.message.reply_text("Роли работают только в группах. Добавь меня в группу и включи их там: /roles_on")
        return False
    return True


async def roles_on_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    if not await _group_only(update):
        return
    chat_id, uid = update.effective_chat.id, update.effective_user.id
    if not await is_group_admin(context.bot, chat_id, uid):
        await update.message.reply_text("Включить роли может только админ группы.")
        return
    set_roles_enabled(chat_id, True, uid)
    await update.message.reply_text(
        "✅ Роли включены в этой группе.\n\n"
        "Роли — это просто звания без особых прав. Назначить: ответь на сообщение человека "
        "командой /setrole шут (или /setrole староста @username). Название любое, например: "
        + ", ".join(SUGGESTIONS) + ".\n"
        "Список: /roles · снять: /unrole · выключить роли: /roles_off"
    )


async def roles_off_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    if not await _group_only(update):
        return
    chat_id, uid = update.effective_chat.id, update.effective_user.id
    if not await is_group_admin(context.bot, chat_id, uid):
        await update.message.reply_text("Выключить роли может только админ группы.")
        return
    set_roles_enabled(chat_id, False, uid)
    await update.message.reply_text("Роли выключены. Назначения сохранены и вернутся, если снова включить: /roles_on")


async def _resolve_target(update: Update, context: ContextTypes.DEFAULT_TYPE, rest: list):
    """Who the command is about: the replied-to person, or an @username from the bot's database."""
    reply = update.message.reply_to_message
    if reply and reply.from_user and not reply.from_user.is_bot:
        u = reply.from_user
        return u.id, (f"@{u.username}" if u.username else u.first_name)
    for part in rest:
        if part.startswith("@"):
            row = find_user_by_username(part)
            if row:
                return row["chat_id"], display_name(row)
            return None, part
    return None, None


async def setrole_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    if not await _group_only(update):
        return
    chat_id, uid = update.effective_chat.id, update.effective_user.id
    if not roles_enabled(chat_id):
        await update.message.reply_text("В этой группе роли выключены. Админ группы может включить: /roles_on")
        return
    if not await is_group_admin(context.bot, chat_id, uid):
        await update.message.reply_text("Назначать роли может только админ группы.")
        return
    args = context.args or []
    title = clean_title(" ".join(a for a in args if not a.startswith("@")))
    if not title:
        await update.message.reply_text("Напиши роль. Пример: ответом на сообщение /setrole шут")
        return
    target, label = await _resolve_target(update, context, [a for a in args if a.startswith("@")])
    if not target:
        await update.message.reply_text(
            "Не понял, кому. Ответь командой на сообщение человека"
            + (f" ({label} не найден среди пользователей бота)." if label else " или добавь @username.")
        )
        return
    set_group_role(chat_id, target, title, uid)
    await update.message.reply_text(f"Готово: {html.escape(str(label))} — {html.escape(role_label(title))}", parse_mode=ParseMode.HTML)


async def unrole_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    if not await _group_only(update):
        return
    chat_id, uid = update.effective_chat.id, update.effective_user.id
    if not await is_group_admin(context.bot, chat_id, uid):
        await update.message.reply_text("Снимать роли может только админ группы.")
        return
    target, label = await _resolve_target(update, context, context.args or [])
    if not target:
        await update.message.reply_text("Ответь командой на сообщение человека или укажи @username.")
        return
    remove_group_role(chat_id, target)
    await update.message.reply_text(f"Роль снята: {html.escape(str(label))}", parse_mode=ParseMode.HTML)


async def roles_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    if not await _group_only(update):
        return
    chat_id = update.effective_chat.id
    if not roles_enabled(chat_id):
        await update.message.reply_text("В этой группе роли выключены. Админ группы может включить: /roles_on")
        return
    names = {r["chat_id"]: display_name(r) for r in list_known_users()}
    rows = list_group_roles(chat_id)
    if not rows:
        await update.message.reply_text("Роли включены, но пока никому не назначены. Ответом на сообщение: /setrole шут")
        return
    lines = [f"{html.escape(role_label(r['role']))} — {html.escape(names.get(r['user_id'], 'id' + str(r['user_id'])))}" for r in rows]
    await update.message.reply_text("🎭 <b>Роли группы</b>\n\n" + "\n".join(lines), parse_mode=ParseMode.HTML)
