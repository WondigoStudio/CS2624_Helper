"""Personal reminders: /remind (create, via a short conversation with quick
one-tap options for the common cases) and /reminders (list + delete your
own). Each reminder belongs to whoever created it and fires in the chat it
was created in — see db.py's "Personal reminders" section for the schema
notes.
"""

from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from ..constants import WEEKDAY_NAMES_FULL_RU, WEEKDAY_NAMES_RU
from ..db import add_reminder, delete_reminder, get_reminder, get_reminders_for_user, register_chat
from ..formatting import format_reminder_line
from ..keyboards import reminder_when_keyboard, weekday_keyboard
from ..permissions import is_admin
from ..states import (
    REMIND_CUSTOM_DATE,
    REMIND_CUSTOM_TIME,
    REMIND_DAILY_TIME,
    REMIND_TEXT,
    REMIND_WEEKLY_DAY,
    REMIND_WEEKLY_TIME,
    REMIND_WHEN,
)
from ..utils import now_kz, parse_due_date, parse_due_time, reply_text_chunked


async def remind_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    await update.message.reply_text(
        "Напиши текст напоминания — о чём напомнить?"
    )
    return REMIND_TEXT


async def remind_text_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["rem_text"] = update.message.text.strip()
    await update.message.reply_text("Когда напомнить?", reply_markup=reminder_when_keyboard())
    return REMIND_WHEN


async def remind_when_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data.split(":", 1)[1]
    text = context.user_data.get("rem_text")
    if not text:
        await query.edit_message_text("Что-то пошло не так, начни заново: /remind")
        return ConversationHandler.END
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id

    if choice in ("q15", "q60", "q180", "tmr9"):
        now = now_kz()
        if choice == "q15":
            when = now + timedelta(minutes=15)
        elif choice == "q60":
            when = now + timedelta(hours=1)
        elif choice == "q180":
            when = now + timedelta(hours=3)
        else:  # tmr9
            when = (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        add_reminder(
            chat_id, user_id, text, "once", when.strftime("%H:%M"),
            remind_date=when.date().isoformat(),
        )
        context.user_data.pop("rem_text", None)
        await query.edit_message_text(
            f"Готово ✅ Напомню {when.strftime('%d.%m.%Y %H:%M')}: «{text}»"
        )
        return ConversationHandler.END

    if choice == "daily":
        await query.edit_message_text(
            "В какое время каждый день напоминать? Напиши в формате ЧЧ:ММ."
        )
        return REMIND_DAILY_TIME

    if choice == "weekly":
        await query.edit_message_text("В какой день недели?")
        await query.message.reply_text("День недели:", reply_markup=weekday_keyboard("remwd"))
        return REMIND_WEEKLY_DAY

    # choice == "custom"
    await query.edit_message_text(
        "Напиши дату в формате ДД.ММ или ДД.ММ.ГГГГ, либо «сегодня»/«завтра»."
    )
    return REMIND_CUSTOM_DATE


async def remind_daily_time_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = parse_due_time(update.message.text)
    if not t:
        await update.message.reply_text(
            "Не понял время. Напиши в формате ЧЧ:ММ, например 08:00."
        )
        return REMIND_DAILY_TIME
    text = context.user_data.pop("rem_text")
    add_reminder(update.effective_chat.id, update.effective_user.id, text, "daily", t)
    await update.message.reply_text(f"Готово ✅ Буду напоминать каждый день в {t}: «{text}»")
    return ConversationHandler.END


async def remind_weekly_day_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    weekday = int(query.data.split(":")[1])
    context.user_data["rem_weekday"] = weekday
    await query.edit_message_text(
        f"День: {WEEKDAY_NAMES_FULL_RU[weekday]}\n\nВо сколько? Напиши время в формате ЧЧ:ММ."
    )
    return REMIND_WEEKLY_TIME


async def remind_weekly_time_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = parse_due_time(update.message.text)
    if not t:
        await update.message.reply_text(
            "Не понял время. Напиши в формате ЧЧ:ММ, например 08:00."
        )
        return REMIND_WEEKLY_TIME
    text = context.user_data.pop("rem_text")
    weekday = context.user_data.pop("rem_weekday")
    add_reminder(update.effective_chat.id, update.effective_user.id, text, "weekly", t, weekday=weekday)
    await update.message.reply_text(
        f"Готово ✅ Буду напоминать каждую неделю по {WEEKDAY_NAMES_RU[weekday]} в {t}: «{text}»"
    )
    return ConversationHandler.END


async def remind_custom_date_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = parse_due_date(update.message.text)
    if d is None:
        await update.message.reply_text(
            "Не понял дату. Попробуй ещё раз, например: 05.10 или 05.10.2026"
        )
        return REMIND_CUSTOM_DATE
    context.user_data["rem_date"] = d.isoformat()
    await update.message.reply_text("Во сколько? Напиши время в формате ЧЧ:ММ.")
    return REMIND_CUSTOM_TIME


async def remind_custom_time_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = parse_due_time(update.message.text)
    if not t:
        await update.message.reply_text(
            "Не понял время. Напиши в формате ЧЧ:ММ, например 09:00."
        )
        return REMIND_CUSTOM_TIME
    text = context.user_data.pop("rem_text")
    d_iso = context.user_data.pop("rem_date")
    add_reminder(update.effective_chat.id, update.effective_user.id, text, "once", t, remind_date=d_iso)
    d = datetime.strptime(d_iso, "%Y-%m-%d").date()
    await update.message.reply_text(f"Готово ✅ Напомню {d.strftime('%d.%m.%Y')} в {t}: «{text}»")
    return ConversationHandler.END


async def remind_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END


async def reminders_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    rows = get_reminders_for_user(update.effective_user.id)
    if not rows:
        await update.message.reply_text(
            "У тебя пока нет активных напоминаний. Добавь через /remind."
        )
        return
    text = "🔔 Твои напоминания:\n\n" + "\n".join(format_reminder_line(r) for r in rows)
    await reply_text_chunked(update.message, text)
    buttons = [
        [InlineKeyboardButton(f"🗑 Удалить #{r['id']}", callback_data=f"remdel:{r['id']}")]
        for r in rows
    ]
    await update.message.reply_text(
        "Удалить напоминание:", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def reminder_delete_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    reminder_id = int(query.data.split(":")[1])
    row = get_reminder(reminder_id)
    if not row:
        await query.edit_message_text("Уже удалено.")
        return
    if row["user_id"] != update.effective_user.id and not is_admin(update.effective_user.id):
        await query.answer("Это не твоё напоминание.", show_alert=True)
        return
    delete_reminder(reminder_id)
    await query.edit_message_text("Удалено 🗑")


async def reminder_snooze_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Button attached to a fired reminder — recreates it as a new one-time
    reminder 10 minutes from now, same text/chat/person."""
    query = update.callback_query
    reminder_id = int(query.data.split(":")[1])
    row = get_reminder(reminder_id)
    if not row:
        await query.answer("Не нашёл исходное напоминание.", show_alert=True)
        return
    when = now_kz() + timedelta(minutes=10)
    add_reminder(
        row["chat_id"], row["user_id"], row["text"], "once", when.strftime("%H:%M"),
        remind_date=when.date().isoformat(),
    )
    await query.answer("Отложено на 10 минут ⏱")
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
