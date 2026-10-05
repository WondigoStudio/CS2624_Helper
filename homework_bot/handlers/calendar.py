"""The button calendar: /calendar, month navigation, and tapping a day to
see (and drill into) that day's tasks."""

from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ..config import SHARED_TASKS_ID, logger
from ..constants import MONTH_NAMES_RU, SUBJECT_NAME
from ..db import get_task_attachments, get_tasks, register_chat
from ..keyboards import calendar_keyboard
from ..utils import today_kz


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
        reply_markup=calendar_keyboard(SHARED_TASKS_ID, year, month, update.effective_user.id),
    )


async def calendar_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, year, month = query.data.split(":")
    year, month = int(year), int(month)
    await query.edit_message_text(
        f"{MONTH_NAMES_RU[month]} {year}\nНажми на день, чтобы посмотреть задания.",
        reply_markup=calendar_keyboard(SHARED_TASKS_ID, year, month, update.effective_user.id),
    )


async def calendar_noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()


async def calendar_day_tap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    iso_day = query.data.split(":")[1]
    d = datetime.strptime(iso_day, "%Y-%m-%d").date()
    rows = get_tasks(SHARED_TASKS_ID, only_undone=False, start=iso_day, end=iso_day,
                     viewer_id=update.effective_user.id)
    if not rows:
        alert_text = f"{d.strftime('%d.%m.%Y')} — заданий нет 🎉"
        full_text = None
    else:
        lines = [f"{d.strftime('%d.%m.%Y')}:"]
        for r in rows:
            mark = "✅" if r["my_done"] else "▫️"
            time_part = f" ({r['due_time']})" if r["due_time"] else ""
            has_files = bool(get_task_attachments(r["id"]))
            attach_part = " 📎" if has_files else ""
            desc_part = " 📝" if r["description"] else ""
            lines.append(f"{mark} [{SUBJECT_NAME[r['subject']]}] {r['title']}{time_part}{attach_part}{desc_part}")
        full_text = "\n".join(lines)
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
    with_extras = [r for r in rows if get_task_attachments(r["id"]) or r["description"]]
    if with_extras:
        buttons = []
        for r in with_extras:
            label = f"{SUBJECT_NAME[r['subject']]}: {r['title'][:30]}"
            if r["description"]:
                buttons.append([InlineKeyboardButton(f"📝 {label}", callback_data=f"taskdesc:{r['id']}")])
            if get_task_attachments(r["id"]):
                buttons.append([InlineKeyboardButton(f"📎 {label}", callback_data=f"taskfile:{r['id']}")])
        await query.message.reply_text(
            f"Подробнее про задания на {d.strftime('%d.%m.%Y')}:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
