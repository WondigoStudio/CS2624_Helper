"""The button calendar: /calendar, month navigation, and tapping a day to
see (and drill into) that day's tasks."""

import html
import re
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ..config import SHARED_TASKS_ID, logger
from ..constants import MONTH_NAMES_RU, SUBJECT_NAME
from ..db import get_task, get_task_attachments, get_tasks, register_chat
from ..ephemeral import send_ephemeral, send_ephemeral_file
from ..keyboards import calendar_keyboard
from ..utils import today_kz


def _viewer(update: Update):
    # private chat: hide what this person finished; group: show everything
    return update.effective_user.id if update.effective_chat.type == "private" else None


async def calendar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    today = today_kz()
    year, month = today.year, today.month
    if context.args:
        try:
            if "." in context.args[0]:
                m, y = context.args[0].split(".")
                month, year = int(m), int(y)
            else:
                month = int(context.args[0])
        except ValueError:
            pass
    await update.message.reply_text(
        f"{MONTH_NAMES_RU[month]} {year}\nНажми на день, чтобы посмотреть задания.",
        reply_markup=calendar_keyboard(SHARED_TASKS_ID, year, month, _viewer(update)),
    )


async def calendar_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, year, month = query.data.split(":")
    year, month = int(year), int(month)
    await query.edit_message_text(
        f"{MONTH_NAMES_RU[month]} {year}\nНажми на день, чтобы посмотреть задания.",
        reply_markup=calendar_keyboard(SHARED_TASKS_ID, year, month, _viewer(update)),
    )


async def calendar_noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()


def _extra_buttons(rows, prefix: str = ""):
    """📝 / 📎 buttons for tasks that have a description or attachments.
    prefix "e" marks buttons living in a private (ephemeral) message."""
    buttons = []
    for r in rows:
        if not (r["description"] or get_task_attachments(r["id"])):
            continue
        label = f"{SUBJECT_NAME[r['subject']]}: {r['title'][:30]}"
        if r["description"]:
            buttons.append([InlineKeyboardButton(f"📝 {label}", callback_data=f"{prefix}taskdesc:{r['id']}")])
        if get_task_attachments(r["id"]):
            buttons.append([InlineKeyboardButton(f"📎 {label}", callback_data=f"{prefix}taskfile:{r['id']}")])
    return buttons


def _day_lines(d, rows):
    lines = [f"{d.strftime('%d.%m.%Y')}:"]
    for r in rows:
        mark = "✅" if r["my_done"] else "▫️"
        time_part = f" ({r['due_time']})" if r["due_time"] else ""
        has_files = bool(get_task_attachments(r["id"]))
        attach_part = " 📎" if has_files else ""
        desc_part = " 📝" if r["description"] else ""
        lines.append(f"{mark} [{SUBJECT_NAME[r['subject']]}] {r['title']}{time_part}{attach_part}{desc_part}")
    return lines


async def calendar_day_tap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    iso_day = query.data.split(":")[1]
    d = datetime.strptime(iso_day, "%Y-%m-%d").date()
    chat = update.effective_chat
    in_group = chat.type != "private"

    # In a group the answer goes to the person who tapped, as a message only
    # they can see (Bot API 10.2 ephemeral message), so the shared chat does
    # not fill up with everyone's lookups. It also uses *their* done marks.
    if in_group:
        mine = get_tasks(SHARED_TASKS_ID, only_undone=False, start=iso_day, end=iso_day,
                         viewer_id=update.effective_user.id)
        if not mine:
            text = f"{d.strftime('%d.%m.%Y')} — заданий нет 🎉"
        else:
            text = "\n".join(_day_lines(d, mine))
        thread_id = getattr(query.message, "message_thread_id", None) if query.message else None
        buttons = _extra_buttons(mine, prefix="e")
        if await send_ephemeral(context.bot, chat.id, update.effective_user.id, text,
                                callback_query_id=query.id, thread_id=thread_id,
                                reply_markup=InlineKeyboardMarkup(buttons) if buttons else None):
            try:
                await query.answer()  # harmless if Telegram already treated the send as the answer
            except Exception:
                pass
            return

    rows = get_tasks(SHARED_TASKS_ID, only_undone=False, start=iso_day, end=iso_day,
                     viewer_id=_viewer(update))
    if not rows:
        alert_text = f"{d.strftime('%d.%m.%Y')} — заданий нет 🎉"
        full_text = None
    else:
        full_text = "\n".join(_day_lines(d, rows))
        alert_text = full_text
        # Telegram caps a callback-query alert at 200 characters and errors
        # out (BadRequest) if it's longer, so keep the popup short and send
        # the complete list as a normal message below instead of truncating
        # and losing tasks from view.
        if len(alert_text) > 200:
            alert_text = f"{d.strftime('%d.%m.%Y')}: {len(rows)} заданий(-е) — список ниже 👇"

    try:
        await query.answer(text=alert_text, show_alert=True)
    except Exception as e:
        logger.warning("Could not show calendar day alert: %s", e)
        await query.answer()

    if full_text and full_text != alert_text:
        await query.message.reply_text(full_text)

    # buttons underneath to see the full description or open an attachment —
    # the popup alert itself can't carry buttons, send files, or fit a long
    # description (it's capped around 200 characters by Telegram)
    buttons = _extra_buttons(rows)
    if buttons:
        await query.message.reply_text(
            f"Подробнее про задания на {d.strftime('%d.%m.%Y')}:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

def _ephemeral_target(update: Update):
    """(chat_id, thread_id) of the group the private message lives in."""
    msg = update.callback_query.message
    chat_id = update.effective_chat.id if update.effective_chat else msg.chat.id
    return chat_id, getattr(msg, "message_thread_id", None)


async def private_task_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A 📝 button inside a private (ephemeral) calendar message: the full
    description goes back to that person only."""
    query = update.callback_query
    task_id = int(query.data.split(":")[1])
    task = get_task(task_id)
    if not task or not task["description"]:
        await query.answer("Описания нет.", show_alert=True)
        return
    head = f"📝 [{SUBJECT_NAME[task['subject']]}] {task['title']}"
    sent = False
    try:
        chat_id, thread_id = _ephemeral_target(update)
        if task["description_html"] and len(task["description"]) < 3500:
            text = f"📝 [{html.escape(SUBJECT_NAME[task['subject']])}] {html.escape(task['title'])}:\n\n{task['description']}"
            sent = await send_ephemeral(context.bot, chat_id, update.effective_user.id, text,
                                        callback_query_id=query.id, thread_id=thread_id,
                                        parse_mode=ParseMode.HTML)
        if not sent:
            plain = f"{head}:\n\n{task['description']}"
            if task["description_html"]:
                plain = f"{head}:\n\n" + html.unescape(re.sub(r"<[^>]+>", "", task["description"]))
            if len(plain) > 3900:
                plain = plain[:3900].rstrip() + "…\n(описание длинное — целиком оно в кнопке 📝 у задания в личке с ботом)"
            sent = await send_ephemeral(context.bot, chat_id, update.effective_user.id, plain,
                                        callback_query_id=query.id, thread_id=thread_id)
    except Exception as e:
        logger.warning("Private description failed: %s", e)
    try:
        if sent:
            await query.answer()
        else:  # last resort: a popup only this person sees
            snippet = re.sub(r"<[^>]+>", "", task["description"])
            await query.answer(html.unescape(snippet)[:190], show_alert=True)
    except Exception:
        pass


async def private_task_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """A 📎 button inside a private (ephemeral) calendar message."""
    query = update.callback_query
    task_id = int(query.data.split(":")[1])
    task = get_task(task_id)
    attachments = get_task_attachments(task_id) if task else []
    if not task or not attachments:
        await query.answer("Вложение не найдено.", show_alert=True)
        return
    caption = f"[{SUBJECT_NAME[task['subject']]}] {task['title']}"
    ok = True
    try:
        chat_id, thread_id = _ephemeral_target(update)
        for i, att in enumerate(attachments):
            ok = await send_ephemeral_file(
                context.bot, chat_id, update.effective_user.id, att["kind"], att["file_id"],
                caption=caption if i == 0 else None,
                callback_query_id=query.id if i == 0 else None, thread_id=thread_id,
            ) and ok
    except Exception as e:
        logger.warning("Private file failed: %s", e)
        ok = False
    try:
        if ok:
            await query.answer()
        else:
            await query.answer("Не получилось показать файл здесь — открой его командой /taskfile.", show_alert=True)
    except Exception:
        pass
