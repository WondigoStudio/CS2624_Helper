"""/audience — admin panel: where the bot may write on its own, and what.

Home: the default rule + lists of groups / people. A chat card has five switches
(auto → on → off → auto); a group can also be removed from the list (and the bot can leave it).
Callback data (all start with "aud:"):
  home | m:<private|all> | g:<page> | u:<page> | h | c:<chat_id> | t:<chat_id>:<kind>
  r:<chat_id> (reset) | d:<chat_id> (delete?) | dd:<chat_id>:<0|1> (confirm; 1 = also leave) | x:<chat_id> (restore)
"""

import html
import logging

from telegram import InlineKeyboardButton as Btn, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from .. import audience as A
from ..db import (delete_chat, display_name, group_flag, ignored_chats, list_known_users,
                  register_chat, set_chat_ignored)
from ..permissions import is_admin

log = logging.getLogger(__name__)
PAGE = 8
_FLAG_OF_KIND = {"schedule": "schedule", "weather": "weather", "tasks": "tasks"}  # group /settings mutes


def _row(chat_id: int):
    return next((r for r in list_known_users() if r["chat_id"] == chat_id), None)


def _marks(chat_id: int) -> str:
    return "".join("🟢" if A.allowed(chat_id, k) else "⚪" for k in A.KIND_KEYS)


def _lists():
    rows = list_known_users()
    return [r for r in rows if r["chat_id"] < 0], [r for r in rows if r["chat_id"] >= 0]


def _home():
    groups, people = _lists()
    m = A.mode()
    on_people = sum(1 for r in people if A.default_allowed(r["chat_id"]))
    if m == A.PRIVATE:
        rule = (f"🔒 <b>По умолчанию: только личка и только с разрешением</b>\n"
                f"Из людей получают {on_people} из {len(people)}, группам бот сам не пишет.")
    else:
        rule = (f"📣 <b>По умолчанию: все чаты (как раньше)</b>\n"
                f"{len(people)} людей и {len(groups)} групп.")
    text = ("📬 <b>Куда и что бот пишет сам</b>\n\n"
            "Расписание и погода по утрам, напоминания о заданиях, о парах (за 10 минут) и о дедлайнах (за час).\n"
            "Личные /remind, опрос группы (/setreport) и ответы на команды от этого не зависят.\n\n" + rule +
            "\n\nДля любого чата можно задать своё правило поверх этого — откройте нужный список.")
    rows = [
        [Btn(("✅ " if m == A.PRIVATE else "") + "🔒 Личка + разрешение", callback_data="aud:m:private")],
        [Btn(("✅ " if m == A.EVERYONE else "") + "📣 Все чаты (как раньше)", callback_data="aud:m:all")],
        [Btn(f"👥 Группы ({len(groups)})", callback_data="aud:g:0"),
         Btn(f"👤 Люди ({len(people)})", callback_data="aud:u:0")],
    ]
    hidden = len(ignored_chats())
    if hidden:
        rows.append([Btn(f"🙈 Удалённые группы ({hidden})", callback_data="aud:h")])
    return text, InlineKeyboardMarkup(rows)


def _list(kind: str, page: int):
    groups, people = _lists()
    items = groups if kind == "g" else people
    pages = max(1, -(-len(items) // PAGE))
    page = max(0, min(page, pages - 1))
    chunk = items[page * PAGE:(page + 1) * PAGE]
    title = "👥 Группы" if kind == "g" else "👤 Люди"
    text = (f"{title}: {len(items)}\n🟢 — бот пишет, ⚪ — нет. Порядок значков: "
            + " ".join(k_label.split()[0] for _, k_label in A.KINDS) +
            "\nОткройте чат, чтобы изменить." + ("" if items else "\n\nСписок пуст."))
    rows = []
    for r in chunk:
        name = display_name(r)[:30]
        rows.append([Btn(f"{name}  {_marks(r['chat_id'])}", callback_data=f"aud:c:{r['chat_id']}")])
    nav = []
    if page > 0:
        nav.append(Btn("◀️", callback_data=f"aud:{kind}:{page - 1}"))
    if pages > 1:
        nav.append(Btn(f"{page + 1}/{pages}", callback_data="aud:noop"))
    if page < pages - 1:
        nav.append(Btn("▶️", callback_data=f"aud:{kind}:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([Btn("⬅️ Назад", callback_data="aud:home")])
    return text, InlineKeyboardMarkup(rows)


def _card(chat_id: int):
    r = _row(chat_id)
    if not r:
        return None
    is_group = chat_id < 0
    lines = [f"{'👥' if is_group else '👤'} <b>{html.escape(display_name(r).lstrip('👥 '))}</b>", f"id <code>{chat_id}</code>", ""]
    rows = []
    for kind, label in A.KINDS:
        forced = A.override(chat_id, kind)
        eff = A.allowed(chat_id, kind)
        state = ("авто: " if forced is None else "") + ("включено" if eff else "выключено")
        if forced is None:
            icon = "🟢" if eff else "⚪"
        else:
            icon = "✅" if forced else "🚫"
        rows.append([Btn(f"{icon} {label} — {state}", callback_data=f"aud:t:{chat_id}:{kind}")])
        flag = _FLAG_OF_KIND.get(kind)
        if is_group and eff and flag and not group_flag(chat_id, flag):
            lines.append(f"• {label}: выключено ещё и в /settings самой группы")
    lines.insert(3, "Нажатие переключает: авто → включить → выключить → авто.\n"
                    "Авто = общее правило (по умолчанию группам бот сам не пишет).")
    if any(A.override(chat_id, k) is not None for k in A.KIND_KEYS):
        rows.append([Btn("↩️ Сбросить на авто", callback_data=f"aud:r:{chat_id}")])
    if is_group:
        rows.append([Btn("🗑 Удалить из списка", callback_data=f"aud:d:{chat_id}")])
    rows.append([Btn("⬅️ К списку", callback_data=f"aud:{'g' if is_group else 'u'}:0")])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def _confirm(chat_id: int):
    r = _row(chat_id)
    name = html.escape(display_name(r).lstrip("👥 ")) if r else f"id{chat_id}"
    text = (f"🗑 Удалить группу <b>{name}</b> из списка?\n\n"
            "Будут стёрты её расписание, фото кабинетов, напоминания и настройки. Задания, оценки и история "
            "остаются. Группа больше не появится в списке, пока вы не вернёте её (кнопка в «Удалённых группах»).")
    rows = [[Btn("🗑 Удалить", callback_data=f"aud:dd:{chat_id}:0")],
            [Btn("🚪 Удалить и выйти из группы", callback_data=f"aud:dd:{chat_id}:1")],
            [Btn("Отмена", callback_data=f"aud:c:{chat_id}")]]
    return text, InlineKeyboardMarkup(rows)


def _hidden():
    ids = sorted(ignored_chats())
    text = ("🙈 <b>Удалённые группы</b>\nБот игнорирует их и не добавляет в список. "
            "«Вернуть» снимает запрет: группа появится снова, когда там кто-то напишет боту."
            + ("" if ids else "\n\nПусто."))
    rows = [[Btn(f"↩️ Вернуть id{i}", callback_data=f"aud:x:{i}")] for i in ids[:20]]
    rows.append([Btn("⬅️ Назад", callback_data="aud:home")])
    return text, InlineKeyboardMarkup(rows)


async def audience_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Эта команда доступна только администраторам бота.")
        return
    text, kb = _home()
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def audience_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("Только для администраторов.", show_alert=True)
        return
    parts = query.data.split(":")
    act = parts[1] if len(parts) > 1 else "home"
    toast = None
    view = None
    try:
        if act == "noop":
            await query.answer()
            return
        if act == "m":
            A.set_mode(A.EVERYONE if parts[2] == "all" else A.PRIVATE)
            toast = "Сохранено"
            view = _home()
        elif act in ("g", "u"):
            view = _list(act, int(parts[2]))
        elif act == "h":
            view = _hidden()
        elif act == "c":
            view = _card(int(parts[2]))
        elif act == "t":
            cid, kind = int(parts[2]), parts[3]
            A.cycle(cid, kind)
            view = _card(cid)
        elif act == "r":
            cid = int(parts[2])
            A.reset(cid)
            toast = "Сброшено"
            view = _card(cid)
        elif act == "d":
            view = _confirm(int(parts[2]))
        elif act == "dd":
            cid, leave = int(parts[2]), parts[3] == "1"
            if cid >= 0:  # people are not removed from here
                await query.answer("Удалять можно только группы.", show_alert=True)
                return
            delete_chat(cid)
            set_chat_ignored(cid, True)
            toast = "Группа удалена"
            if leave:
                try:
                    await context.bot.leave_chat(cid)
                    toast = "Группа удалена, бот вышел"
                except Exception as e:  # noqa: BLE001
                    log.warning("leave_chat %s failed: %s", cid, e)
                    toast = "Группа удалена, но выйти не удалось"
            view = _list("g", 0)
        elif act == "x":
            set_chat_ignored(int(parts[2]), False)
            toast = "Группа возвращена"
            view = _hidden()
        else:
            view = _home()
    except (ValueError, IndexError):
        await query.answer("Устаревшая кнопка.")
        return
    if view is None:  # the chat disappeared meanwhile
        toast = "Чат не найден"
        view = _home()
    await query.answer(toast)
    text, kb = view
    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:  # noqa: BLE001 — "message is not modified"
        pass
