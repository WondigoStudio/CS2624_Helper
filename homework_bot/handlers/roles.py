"""Group role commands. Roles work only in groups where an admin ran /roles_on."""

import html

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ..db import (
    display_name, find_user_by_username, list_group_roles, list_known_users, register_chat,
    remove_group_role, roles_enabled, set_group_role, set_roles_enabled,
)
from ..roles import ROLES, has_perm, is_group_admin, parse_role, role_label, user_role


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
        "Назначить: ответь на сообщение человека командой /setrole староста "
        "(или /setrole зам @username). Роли: староста, зам, редактор, дежурный, ведущий.\n"
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
    if not (await is_group_admin(context.bot, chat_id, uid) or has_perm(chat_id, uid, "manage_roles")):
        await update.message.reply_text("Назначать роли могут админы группы, староста и зам. старосты.")
        return
    args = context.args or []
    code = parse_role(args[0]) if args else None
    if not code:
        await update.message.reply_text("Напиши роль: староста, зам, редактор, дежурный или ведущий. Пример: ответом на сообщение /setrole дежурный")
        return
    target, label = await _resolve_target(update, context, args[1:])
    if not target:
        await update.message.reply_text(
            "Не понял, кому. Ответь командой на сообщение человека"
            + (f" ({label} не найден среди пользователей бота)." if label else " или укажи @username.")
        )
        return
    # only a group admin may create/replace a headman or deputy
    if code in ("headman", "deputy") and not await is_group_admin(context.bot, chat_id, uid):
        await update.message.reply_text("Старосту и зама назначает только админ группы.")
        return
    set_group_role(chat_id, target, code, uid)
    await update.message.reply_text(f"Готово: {html.escape(str(label))} — {role_label(code)}", parse_mode=ParseMode.HTML)


async def unrole_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    if not await _group_only(update):
        return
    chat_id, uid = update.effective_chat.id, update.effective_user.id
    if not (await is_group_admin(context.bot, chat_id, uid) or has_perm(chat_id, uid, "manage_roles")):
        await update.message.reply_text("Снимать роли могут админы группы, староста и зам. старосты.")
        return
    target, label = await _resolve_target(update, context, context.args or [])
    if not target:
        await update.message.reply_text("Ответь командой на сообщение человека или укажи @username.")
        return
    current = user_role(chat_id, target)
    if current in ("headman", "deputy") and not await is_group_admin(context.bot, chat_id, uid):
        await update.message.reply_text("Старосту и зама снимает только админ группы.")
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
        await update.message.reply_text("Роли включены, но пока никому не назначены. Ответом на сообщение: /setrole староста")
        return
    lines = [f"{role_label(r['role'])} — {html.escape(names.get(r['user_id'], 'id' + str(r['user_id'])))}" for r in rows if r["role"] in ROLES]
    await update.message.reply_text("🎭 <b>Роли группы</b>\n\n" + "\n".join(lines), parse_mode=ParseMode.HTML)
