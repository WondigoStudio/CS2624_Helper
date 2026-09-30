"""Inline-keyboard builders shared across handlers."""

import calendar
from datetime import date

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from .constants import MONTH_NAMES_RU, SUBJECTS, WEEKDAY_EMOJI, WEEKDAY_NAMES_FULL_RU, WEEKDAY_NAMES_RU
from .db import display_name, get_overdue_task_dates_in_month, get_task_dates_in_month, list_known_users
from .utils import today_kz


def subject_keyboard(prefix: str):
    buttons = [
        [InlineKeyboardButton(name, callback_data=f"{prefix}:{code}")]
        for code, name in SUBJECTS
    ]
    return InlineKeyboardMarkup(buttons)


def _edit_task_field_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Предмет", callback_data="editfield:subject")],
        [InlineKeyboardButton("Текст задания", callback_data="editfield:title")],
        [InlineKeyboardButton("Описание", callback_data="editfield:description")],
        [InlineKeyboardButton("Дата", callback_data="editfield:due_date")],
        [InlineKeyboardButton("Время", callback_data="editfield:due_time")],
        [InlineKeyboardButton("Вложение", callback_data="editfield:attachment")],
    ])


def reminder_when_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⏱ Через 15 минут", callback_data="remwhen:q15")],
        [InlineKeyboardButton("⏱ Через 1 час", callback_data="remwhen:q60")],
        [InlineKeyboardButton("⏱ Через 3 часа", callback_data="remwhen:q180")],
        [InlineKeyboardButton("🌅 Завтра в 9:00", callback_data="remwhen:tmr9")],
        [InlineKeyboardButton("🔁 Каждый день", callback_data="remwhen:daily")],
        [InlineKeyboardButton("📅 Каждую неделю", callback_data="remwhen:weekly")],
        [InlineKeyboardButton("🗓 Своя дата и время", callback_data="remwhen:custom")],
    ])


def weekday_keyboard(prefix: str):
    buttons = [
        [InlineKeyboardButton(f"{WEEKDAY_EMOJI[i]} {WEEKDAY_NAMES_FULL_RU[i]}", callback_data=f"{prefix}:{i}")]
        for i in range(7)
    ]
    return InlineKeyboardMarkup(buttons)


def target_user_keyboard(prefix: str):
    buttons = [
        [InlineKeyboardButton("Себе", callback_data=f"{prefix}:self")],
        [InlineKeyboardButton("🌐 Всем сразу", callback_data=f"{prefix}:all")],
    ]
    for u in list_known_users():
        buttons.append(
            [InlineKeyboardButton(display_name(u), callback_data=f"{prefix}:{u['chat_id']}")]
        )
    return InlineKeyboardMarkup(buttons)


def birthday_target_keyboard(prefix: str):
    buttons = [[InlineKeyboardButton("Себе", callback_data=f"{prefix}:self")]]
    for u in list_known_users():
        buttons.append(
            [InlineKeyboardButton(display_name(u), callback_data=f"{prefix}:{u['chat_id']}")]
        )
    return InlineKeyboardMarkup(buttons)


def _edit_lesson_field_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("День недели", callback_data="editlfield:weekday")],
        [InlineKeyboardButton("Предмет", callback_data="editlfield:subject")],
        [InlineKeyboardButton("Время", callback_data="editlfield:time")],
        [InlineKeyboardButton("Кабинет", callback_data="editlfield:room")],
    ])


def calendar_keyboard(chat_id: int, year: int, month: int) -> InlineKeyboardMarkup:
    busy_days = get_task_dates_in_month(chat_id, year, month)
    overdue_days = get_overdue_task_dates_in_month(chat_id, year, month)
    today = today_kz()

    rows = [
        [InlineKeyboardButton(f"« {MONTH_NAMES_RU[month]} {year} »", callback_data="noop")],
        [InlineKeyboardButton(wd, callback_data="noop") for wd in WEEKDAY_NAMES_RU],
    ]

    first_weekday, days_in_month = calendar.monthrange(year, month)
    week = [InlineKeyboardButton(" ", callback_data="noop") for _ in range(first_weekday)]

    for day_num in range(1, days_in_month + 1):
        d = date(year, month, day_num)
        iso_day = d.isoformat()
        # Bot API 9.4 / PTB 22.7+ added a real "style" field for inline
        # buttons, but only three values exist: primary (blue), success
        # (green), danger (red). There is no fourth "transparent" value —
        # that's simply what a button looks like with no style set at all.
        if d == today:
            style = "primary"       # синий
        elif iso_day in overdue_days:
            style = None            # прозрачный (дефолтный вид) — дедлайн прошёл
        elif iso_day in busy_days:
            style = "danger"        # красный
        else:
            style = "success"       # зелёный
        kwargs = {"style": style} if style else {}
        week.append(InlineKeyboardButton(str(day_num), callback_data=f"day:{iso_day}", **kwargs))

        if len(week) == 7:
            rows.append(week)
            week = []

    if week:
        while len(week) < 7:
            week.append(InlineKeyboardButton(" ", callback_data="noop"))
        rows.append(week)

    rows.append([
        InlineKeyboardButton("свободно", callback_data="noop", style="success"),
        InlineKeyboardButton("есть задание", callback_data="noop", style="danger"),
        InlineKeyboardButton("сегодня", callback_data="noop", style="primary"),
    ])
    rows.append([
        InlineKeyboardButton("дедлайн прошёл (без цвета)", callback_data="noop"),
    ])

    prev_month, prev_year = (12, year - 1) if month == 1 else (month - 1, year)
    next_month, next_year = (1, year + 1) if month == 12 else (month + 1, year)
    rows.append([
        InlineKeyboardButton("←", callback_data=f"cal:{prev_year}:{prev_month}"),
        InlineKeyboardButton("Сегодня", callback_data=f"cal:{today.year}:{today.month}"),
        InlineKeyboardButton("→", callback_data=f"cal:{next_year}:{next_month}"),
    ])

    return InlineKeyboardMarkup(rows)
