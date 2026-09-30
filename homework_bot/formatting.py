"""Turning DB rows into the text the bot actually sends: task lines,
lesson lines, the "overdue" block, and Telegram-mention helpers."""

import html
from datetime import datetime, timedelta

from telegram import Update

from .constants import SUBJECT_EMOJI, SUBJECT_NAME, WEEKDAY_NAMES_RU
from .db import get_tasks
from .utils import is_task_overdue, today_kz


def format_task_line(row) -> str:
    d = datetime.strptime(row["due_date"], "%Y-%m-%d").date()
    today = today_kz()
    tag = ""
    if is_task_overdue(row):
        tag = " ⚠️ просрочено"
    elif d == today:
        tag = " 📌 сегодня"
    elif d == today + timedelta(days=1):
        tag = " ⏰ завтра"
    time_part = f" {row['due_time']}" if row["due_time"] else ""
    by_part = f" (добавил: {row['created_by']})" if row["created_by"] else ""
    attach_part = " 📎" if row["attachment_file_id"] else ""
    line = f"#{row['id']} [{SUBJECT_NAME[row['subject']]}] {row['title']} — {d.strftime('%d.%m.%Y')}{time_part}{tag}{attach_part}{by_part}"
    if row["description"]:
        line += f"\n    📝 {row['description']}"
    return line


def short_name(user) -> str:
    if not user:
        return "кто-то"
    return user.first_name or (f"@{user.username}" if user.username else f"id{user.id}")


def mention_html(user) -> str:
    """A clickable link to the user's Telegram profile, showing their short
    name as the link text. Works even for users with no @username, via
    Telegram's tg://user?id=... deep link. Requires parse_mode=HTML."""
    if not user:
        return "кто-то"
    return f'<a href="tg://user?id={user.id}">{html.escape(short_name(user))}</a>'


def user_short_name(update: Update) -> str:
    return short_name(update.effective_user)


def _overdue_rows(chat_id: int):
    """All not-done tasks whose deadline has already passed, regardless of
    due date window — so they show up even on /today or /week where their
    (past) due_date would otherwise exclude them."""
    rows = get_tasks(chat_id, only_undone=True)
    return [r for r in rows if is_task_overdue(r)]


def _overdue_block(chat_id: int) -> str:
    overdue = _overdue_rows(chat_id)
    if not overdue:
        return ""
    return "⚠️ Просрочено:\n" + "\n".join(format_task_line(r) for r in overdue) + "\n\n"


def format_reminder_line(row) -> str:
    if row["repeat"] == "daily":
        when = f"🔁 каждый день в {row['time']}"
    elif row["repeat"] == "weekly":
        when = f"🔁 каждую неделю по {WEEKDAY_NAMES_RU[row['weekday']]} в {row['time']}"
    else:
        d = datetime.strptime(row["remind_date"], "%Y-%m-%d").date()
        when = f"📅 {d.strftime('%d.%m.%Y')} в {row['time']}"
    return f"#{row['id']} {row['text']} — {when}"


def format_lesson_line(row) -> str:
    emoji = SUBJECT_EMOJI.get(row["subject"], "📘")
    subject = html.escape(SUBJECT_NAME[row["subject"]])
    room = html.escape(row["room"])
    return f"{emoji} <b>{row['time']}</b> — <b>{subject}</b> · каб. <code>{room}</code>"


def format_lessons_block(rows, heading: str) -> str:
    lines = [f"<b>{html.escape(heading)}</b>", "─" * 18]
    for i, r in enumerate(rows, start=1):
        lines.append(f"{i}. {format_lesson_line(r)}")
    return "\n".join(lines)
