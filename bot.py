"""
Homework planner Telegram bot.

Subjects: ICT, ITP, Psychology, Sociology, Discrete Mathematics,
Foreign Language B2, Китайский язык, Физра.
Timezone: Kazakhstan, single nationwide zone UTC+5 (Asia/Almaty), no DST.

Commands:
  /start        - welcome message
  /add          - add a new homework task (subject -> title -> date -> time)
  /today        - tasks due today
  /week         - tasks due in the next 7 days
  /all          - all upcoming (not done) tasks
  /done         - mark a task as done (pick from a list)
  /delete       - delete a task
  /calendar     - colored button calendar for a month
  /schedule_add    - add a lesson to the weekly timetable (day/time/room)
  /schedule        - show today's timetable
  /schedule_week   - show the whole week's timetable
  /schedule_day    - show the timetable (+ room photos) for a chosen day
  /schedule_delete - remove a lesson from the timetable
  /addroomphoto    - attach a photo (map/location) to a room number
  /roomphotos      - list rooms that have a saved photo
  /cancel       - cancel the current guided flow

Reminders:
  - once a day (default 08:00 Kazakhstan time): tasks due today and tomorrow
  - once a day (default 07:30 Kazakhstan time): today's timetable (subject,
    time, room) plus the saved photo for every room used that day

Storage: a local SQLite file (homework.db) - one file, no external DB needed.
"""

import calendar
import html
import logging
import os
import sqlite3
import threading
from datetime import datetime, date, time as dtime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Token comes from an environment variable (BOT_TOKEN) so it's never
# committed to git or hard-coded in the file. Set it in Render's dashboard
# under Environment. Locally you can export it before running, e.g.:
#   export BOT_TOKEN="123456:ABC..."   (Windows: set BOT_TOKEN=123456:ABC...)
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise SystemExit(
        "BOT_TOKEN is not set. Set it as an environment variable "
        "(locally: export BOT_TOKEN=...; on Render: Environment tab)."
    )

# Where the SQLite file lives. On Render, point this (via the DB_PATH env
# var) at your persistent disk's mount path, e.g. /var/data/homework.db —
# otherwise the database is wiped on every deploy/restart (Render's default
# filesystem is ephemeral). Locally this just defaults to a file next to
# this script.
DB_PATH = Path(os.environ.get("DB_PATH", str(Path(__file__).parent / "homework.db")))

TIMEZONE = ZoneInfo("Asia/Almaty")

REMINDER_HOUR = 8
REMINDER_MINUTE = 0

SCHEDULE_HOUR = 23
SCHEDULE_MINUTE = 00

ADMIN_IDS = {1762280778}

# Every homework task is shared: everyone who talks to the bot sees the same
# list, regardless of who added it. Internally this is done by always
# storing/reading tasks under one fixed pseudo chat_id instead of each
# person's own chat_id. (Schedules and room photos are NOT affected by this
# — those stay personal per user, as before.)
SHARED_TASKS_ID = 0


def now_kz() -> datetime:
    return datetime.now(TIMEZONE)


def today_kz() -> date:
    return now_kz().date()

SUBJECTS = [
    ("ict", "ICT"),
    ("itp", "ITP"),
    ("psy", "Psychology"),
    ("soc", "Sociology"),
    ("dm", "Discrete Mathematics"),
    ("flb2", "Foreign Language B2"),
    ("chn", "Китайский язык"),
    ("pe", "Физра"),
]
SUBJECT_NAME = dict(SUBJECTS)

SUBJECT_EMOJI = {
    "ict": "💻", "itp": "🖥", "psy": "🧠", "soc": "🧑‍🤝‍🧑",
    "dm": "🔢", "flb2": "🌍", "chn": "🇨🇳", "pe": "🏃",
}

CHOOSING_SUBJECT, TYPING_TITLE, TYPING_DATE, TYPING_TIME = range(4)
CHOOSING_TARGET_USER, SCH_WEEKDAY, SCH_SUBJECT, SCH_TIME, SCH_ROOM = range(4, 9)
PHOTO_TARGET_USER, PHOTO_ROOM_NAME, PHOTO_WAITING = range(9, 12)

WEEKDAY_NAMES_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
WEEKDAY_NAMES_FULL_RU = [
    "Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье",
]
WEEKDAY_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣"]

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)


def start_health_check_server():
    """Render's free 'Web Service' plan requires the process to bind to
    $PORT and answer HTTP requests, or it kills the instance as unhealthy.
    A background worker (paid plan) does NOT need this at all. This only
    starts if a PORT env var is present, so running locally or as a
    background worker is unaffected."""
    port = os.environ.get("PORT")
    if not port:
        return

    class _Health(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass  # keep the bot's own logs clean

    server = HTTPServer(("0.0.0.0", int(port)), _Health)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info("Health-check server listening on port %s", port)

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            subject TEXT NOT NULL,
            title TEXT NOT NULL,
            due_date TEXT NOT NULL,
            due_time TEXT,
            done INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            created_by TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chats (
            chat_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            weekday INTEGER NOT NULL,
            time TEXT NOT NULL,
            subject TEXT NOT NULL,
            room TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS room_photos (
            chat_id INTEGER NOT NULL,
            room TEXT NOT NULL,
            file_id TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'photo',
            PRIMARY KEY (chat_id, room)
        )
        """
    )
    existing_cols = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)")}
    if "due_time" not in existing_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN due_time TEXT")
    if "created_by" not in existing_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN created_by TEXT")
    room_cols = {row["name"] for row in conn.execute("PRAGMA table_info(room_photos)")}
    if "kind" not in room_cols:
        conn.execute("ALTER TABLE room_photos ADD COLUMN kind TEXT NOT NULL DEFAULT 'photo'")
    chat_cols = {row["name"] for row in conn.execute("PRAGMA table_info(chats)")}
    for col in ("username", "first_name", "last_name", "updated_at"):
        if col not in chat_cols:
            conn.execute(f"ALTER TABLE chats ADD COLUMN {col} TEXT")
    conn.commit()
    conn.close()


def register_chat(update: Update):
    chat_id = update.effective_chat.id
    user = update.effective_user
    username = user.username if user else None
    first_name = user.first_name if user else None
    last_name = user.last_name if user else None
    conn = db()
    conn.execute(
        """
        INSERT INTO chats (chat_id, username, first_name, last_name, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET
            username = excluded.username,
            first_name = excluded.first_name,
            last_name = excluded.last_name,
            updated_at = excluded.updated_at
        """,
        (chat_id, username, first_name, last_name, now_kz().isoformat()),
    )
    conn.commit()
    conn.close()


def add_task(chat_id: int, subject: str, title: str, due_date: str, due_time: str = None, created_by: str = None):
    conn = db()
    conn.execute(
        "INSERT INTO tasks (chat_id, subject, title, due_date, due_time, created_at, created_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (chat_id, subject, title, due_date, due_time, now_kz().isoformat(), created_by),
    )
    conn.commit()
    conn.close()


def get_tasks(chat_id: int, only_undone=True, start=None, end=None):
    conn = db()
    q = "SELECT * FROM tasks WHERE chat_id = ?"
    params = [chat_id]
    if only_undone:
        q += " AND done = 0"
    if start:
        q += " AND due_date >= ?"
        params.append(start)
    if end:
        q += " AND due_date <= ?"
        params.append(end)
    q += " ORDER BY due_date ASC"
    rows = conn.execute(q, params).fetchall()
    conn.close()
    return rows


def mark_done(task_id: int):
    conn = db()
    conn.execute("UPDATE tasks SET done = 1 WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()


def delete_task(task_id: int):
    conn = db()
    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()


def get_task_dates_in_month(chat_id: int, year: int, month: int):
    start = date(year, month, 1).isoformat()
    last_day = calendar.monthrange(year, month)[1]
    end = date(year, month, last_day).isoformat()
    rows = get_tasks(chat_id, only_undone=True, start=start, end=end)
    return {r["due_date"] for r in rows}


def all_chat_ids():
    conn = db()
    rows = conn.execute("SELECT chat_id FROM chats").fetchall()
    conn.close()
    return [r["chat_id"] for r in rows]


def list_known_users():
    conn = db()
    rows = conn.execute("SELECT * FROM chats ORDER BY updated_at DESC").fetchall()
    conn.close()
    return rows


def display_name(row) -> str:
    name = " ".join(p for p in (row["first_name"], row["last_name"]) if p)
    username = f"@{row['username']}" if row["username"] else None
    if name and username:
        return f"{name} ({username})"
    if name:
        return name
    if username:
        return username
    return f"id{row['chat_id']}"


def add_lesson(chat_id: int, weekday: int, time_str: str, subject: str, room: str):
    conn = db()
    conn.execute(
        "INSERT INTO schedule (chat_id, weekday, time, subject, room, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (chat_id, weekday, time_str, subject, room, now_kz().isoformat()),
    )
    conn.commit()
    conn.close()


def get_lessons(chat_id: int, weekday: int = None):
    conn = db()
    if weekday is None:
        rows = conn.execute(
            "SELECT * FROM schedule WHERE chat_id = ? ORDER BY weekday ASC, time ASC",
            (chat_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM schedule WHERE chat_id = ? AND weekday = ? ORDER BY time ASC",
            (chat_id, weekday),
        ).fetchall()
    conn.close()
    return rows


def delete_lesson(lesson_id: int):
    conn = db()
    conn.execute("DELETE FROM schedule WHERE id = ?", (lesson_id,))
    conn.commit()
    conn.close()


def set_room_photo(chat_id: int, room: str, file_id: str, kind: str = "photo"):
    conn = db()
    conn.execute(
        "INSERT INTO room_photos (chat_id, room, file_id, kind) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(chat_id, room) DO UPDATE SET file_id = excluded.file_id, kind = excluded.kind",
        (chat_id, room, file_id, kind),
    )
    conn.commit()
    conn.close()


def get_room_photo(chat_id: int, room: str):
    conn = db()
    row = conn.execute(
        "SELECT file_id, kind FROM room_photos WHERE chat_id = ? AND room = ?",
        (chat_id, room),
    ).fetchone()
    conn.close()
    if not row:
        return None
    return row["file_id"], row["kind"]


def list_room_photos(chat_id: int):
    conn = db()
    rows = conn.execute(
        "SELECT room FROM room_photos WHERE chat_id = ? ORDER BY room ASC", (chat_id,)
    ).fetchall()
    conn.close()
    return [r["room"] for r in rows]


def format_task_line(row) -> str:
    d = datetime.strptime(row["due_date"], "%Y-%m-%d").date()
    today = today_kz()
    tag = ""
    if d < today:
        tag = " ⚠️ просрочено"
    elif d == today:
        tag = " 📌 сегодня"
    elif d == today + timedelta(days=1):
        tag = " ⏰ завтра"
    time_part = f" {row['due_time']}" if row["due_time"] else ""
    by_part = f" (добавил: {row['created_by']})" if row["created_by"] else ""
    return f"#{row['id']} [{SUBJECT_NAME[row['subject']]}] {row['title']} — {d.strftime('%d.%m.%Y')}{time_part}{tag}{by_part}"


def subject_keyboard(prefix: str):
    buttons = [
        [InlineKeyboardButton(name, callback_data=f"{prefix}:{code}")]
        for code, name in SUBJECTS
    ]
    return InlineKeyboardMarkup(buttons)


def parse_due_date(text: str):
    text = text.strip().lower()
    today = today_kz()
    if text in ("сегодня", "today"):
        return today
    if text in ("завтра", "tomorrow"):
        return today + timedelta(days=1)
    for fmt in ("%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    try:
        d = datetime.strptime(text, "%d.%m").date()
        return d.replace(year=today.year)
    except ValueError:
        pass
    return None


SKIP_TIME_WORDS = ("нет", "без времени", "skip", "-", "пропустить")


def parse_due_time(text: str):
    text = text.strip().lower()
    if text in SKIP_TIME_WORDS:
        return ""
    for fmt in ("%H:%M", "%H.%M", "%H-%M"):
        try:
            t = datetime.strptime(text, fmt).time()
            return t.strftime("%H:%M")
        except ValueError:
            pass
    return None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    await update.message.reply_text(
        "Привет! Я помогу не забывать про домашние задания.\n\n"
        "⚠️ Список домашних заданий — общий для всех, кто пишет этому боту: "
        "если кто-то добавит задание, его увидят все, и наоборот.\n\n"
        "Предметы: ICT, ITP, Psychology, Sociology, Discrete Mathematics, "
        "Foreign Language B2, Китайский язык, Физра.\n\n"
        "Команды:\n"
        "/add — добавить задание\n"
        "/today — что сдавать сегодня\n"
        "/week — что сдавать на этой неделе\n"
        "/all — все предстоящие задания\n"
        "/done — отметить задание выполненным\n"
        "/delete — удалить задание\n"
        "/calendar — календарь месяца кнопками: зелёная — свободный день, "
        "красная — есть задание, синяя — сегодня. Нажми на день — покажу "
        "что на него задано\n\n"
        "Расписание пар и кабинеты:\n"
        "/schedule_add — добавить пару в расписание (день недели → предмет → время → кабинет)\n"
        "/schedule — расписание на сегодня\n"
        "/schedule_week — расписание на всю неделю\n"
        "/schedule_day — расписание на выбранный день недели + фото кабинетов\n"
        "/schedule_delete — удалить пару из расписания\n"
        "/addroomphoto — прикрепить фото (карту/фото) к кабинету\n"
        "/roomphotos — список кабинетов с сохранённым фото\n"
        "/testphoto — сразу прислать сохранённое фото кабинета (проверить, что оно сохранилось)\n"
        "/testmorning — прогнать утреннюю рассылку расписания+фото прямо сейчас, "
        "не дожидаясь 07:30\n"
        "/users — (только для админа) список пользователей, писавших боту\n"
        "/viewschedule — (только для админа) посмотреть расписание любого "
        "пользователя\n\n"
        f"Каждый день в {REMINDER_HOUR:02d}:{REMINDER_MINUTE:02d} по времени Казахстана "
        "(UTC+5) я буду присылать напоминание о заданиях на сегодня и завтра.\n"
        f"А в {SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d} — расписание на сегодня и фото "
        "кабинетов, если они сохранены."
    )


async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    await update.message.reply_text(
        "Выбери предмет:", reply_markup=subject_keyboard("addsub")
    )
    return CHOOSING_SUBJECT


async def add_subject_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    subject = query.data.split(":")[1]
    context.user_data["new_subject"] = subject
    await query.edit_message_text(
        f"Предмет: {SUBJECT_NAME[subject]}\n\nНапиши, что нужно сделать:"
    )
    return TYPING_TITLE


async def add_title_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_title"] = update.message.text.strip()
    await update.message.reply_text(
        "Когда сдавать? Напиши дату в формате ДД.ММ или ДД.ММ.ГГГГ, "
        "либо просто «сегодня» / «завтра»."
    )
    return TYPING_DATE


async def add_date_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = parse_due_date(update.message.text)
    if d is None:
        await update.message.reply_text(
            "Не понял дату. Попробуй ещё раз, например: 05.10 или 05.10.2026"
        )
        return TYPING_DATE

    context.user_data["new_date"] = d.isoformat()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Без точного времени", callback_data="notime")]
    ])
    await update.message.reply_text(
        "Во сколько (время сдачи/пары)? Напиши в формате ЧЧ:ММ, например 14:30.\n"
        "Или нажми кнопку, если точное время не нужно.",
        reply_markup=keyboard,
    )
    return TYPING_TIME


async def _finish_add_task(target_message, context: ContextTypes.DEFAULT_TYPE, chat_id: int, due_time: str, creator_name: str = None):
    subject = context.user_data.pop("new_subject")
    title = context.user_data.pop("new_title")
    due_date_iso = context.user_data.pop("new_date")
    add_task(chat_id, subject, title, due_date_iso, due_time or None, created_by=creator_name)

    d = datetime.strptime(due_date_iso, "%Y-%m-%d").date()
    time_part = f", {due_time}" if due_time else ""
    text = f"Готово ✅\n[{SUBJECT_NAME[subject]}] {title} — {d.strftime('%d.%m.%Y')}{time_part}"
    await target_message(text)


def user_short_name(update: Update) -> str:
    user = update.effective_user
    if not user:
        return "кто-то"
    return user.first_name or (f"@{user.username}" if user.username else f"id{user.id}")


async def add_time_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = parse_due_time(update.message.text)
    if t is None:
        await update.message.reply_text(
            "Не понял время. Напиши в формате ЧЧ:ММ (например 09:00), "
            "или «нет», если время не нужно."
        )
        return TYPING_TIME

    await _finish_add_task(
        update.message.reply_text, context, SHARED_TASKS_ID, t, user_short_name(update)
    )
    return ConversationHandler.END


async def add_time_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _finish_add_task(
        query.edit_message_text, context, SHARED_TASKS_ID, "", user_short_name(update)
    )
    return ConversationHandler.END


async def add_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END


async def today_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    d = today_kz().isoformat()
    rows = get_tasks(SHARED_TASKS_ID, start=d, end=d)
    if not rows:
        await update.message.reply_text("На сегодня заданий нет 🎉")
        return
    text = "Сегодня:\n" + "\n".join(format_task_line(r) for r in rows)
    await update.message.reply_text(text)


async def week_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    start = today_kz().isoformat()
    end = (today_kz() + timedelta(days=7)).isoformat()
    rows = get_tasks(SHARED_TASKS_ID, start=start, end=end)
    if not rows:
        await update.message.reply_text("На эту неделю заданий нет 🎉")
        return
    text = "На неделю:\n" + "\n".join(format_task_line(r) for r in rows)
    await update.message.reply_text(text)


async def all_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    rows = get_tasks(SHARED_TASKS_ID)
    if not rows:
        await update.message.reply_text("Список пуст 🎉")
        return
    text = "Все предстоящие задания:\n" + "\n".join(format_task_line(r) for r in rows)
    await update.message.reply_text(text)


async def done_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    rows = get_tasks(SHARED_TASKS_ID)
    if not rows:
        await update.message.reply_text("Нет незавершённых заданий.")
        return
    buttons = [
        [InlineKeyboardButton(
            f"{SUBJECT_NAME[r['subject']]}: {r['title'][:35]}",
            callback_data=f"done:{r['id']}",
        )]
        for r in rows
    ]
    await update.message.reply_text(
        "Какое задание выполнено?", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def done_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split(":")[1])
    mark_done(task_id)
    await query.edit_message_text("Отмечено как выполненное ✅")


async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    rows = get_tasks(SHARED_TASKS_ID, only_undone=False)
    if not rows:
        await update.message.reply_text("Список пуст.")
        return
    buttons = [
        [InlineKeyboardButton(
            f"{SUBJECT_NAME[r['subject']]}: {r['title'][:35]}",
            callback_data=f"del:{r['id']}",
        )]
        for r in rows
    ]
    await update.message.reply_text(
        "Что удалить?", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def delete_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split(":")[1])
    delete_task(task_id)
    await query.edit_message_text("Удалено 🗑")


def weekday_keyboard(prefix: str):
    buttons = [
        [InlineKeyboardButton(f"{WEEKDAY_EMOJI[i]} {WEEKDAY_NAMES_FULL_RU[i]}", callback_data=f"{prefix}:{i}")]
        for i in range(7)
    ]
    return InlineKeyboardMarkup(buttons)


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


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


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


ALL_USERS_SENTINEL = "ALL"


async def schedule_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    if is_admin(update.effective_user.id):
        await update.message.reply_text(
            "Кому добавляем пару в расписание?", reply_markup=target_user_keyboard("schtgt")
        )
        return CHOOSING_TARGET_USER

    context.user_data["sch_target_chat"] = update.effective_chat.id
    await update.message.reply_text(
        "В какой день недели этот урок?", reply_markup=weekday_keyboard("schwd")
    )
    return SCH_WEEKDAY


async def schedule_target_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    raw = query.data.split(":", 1)[1]
    if raw == "self":
        target = update.effective_chat.id
        who = "себя"
    elif raw == "all":
        target = ALL_USERS_SENTINEL
        who = "всех известных пользователей"
    else:
        target = int(raw)
        who = f"chat_id {target}"
    context.user_data["sch_target_chat"] = target
    await query.edit_message_text(
        f"Добавляем пару для: {who}\n\nВ какой день недели этот урок?",
        reply_markup=weekday_keyboard("schwd"),
    )
    return SCH_WEEKDAY


async def schedule_weekday_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    weekday = int(query.data.split(":")[1])
    context.user_data["sch_weekday"] = weekday
    await query.edit_message_text(
        f"День: {WEEKDAY_NAMES_FULL_RU[weekday]}\n\nВыбери предмет:",
    )
    await query.message.reply_text("Предмет:", reply_markup=subject_keyboard("schsub"))
    return SCH_SUBJECT


async def schedule_subject_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    subject = query.data.split(":")[1]
    context.user_data["sch_subject"] = subject
    await query.edit_message_text(
        f"Предмет: {SUBJECT_NAME[subject]}\n\nВо сколько начало? Напиши время в формате ЧЧ:ММ."
    )
    return SCH_TIME


async def schedule_time_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = parse_due_time(update.message.text)
    if not t:
        await update.message.reply_text(
            "Не понял время. Напиши в формате ЧЧ:ММ, например 09:00."
        )
        return SCH_TIME
    context.user_data["sch_time"] = t
    await update.message.reply_text("В каком кабинете? Напиши номер/название кабинета.")
    return SCH_ROOM


async def schedule_room_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    room = update.message.text.strip()
    target = context.user_data.pop("sch_target_chat")
    weekday = context.user_data.pop("sch_weekday")
    subject = context.user_data.pop("sch_subject")
    time_str = context.user_data.pop("sch_time")

    if target == ALL_USERS_SENTINEL:
        targets = all_chat_ids()
        for chat_id in targets:
            add_lesson(chat_id, weekday, time_str, subject, room)
        who_line = f"для всех известных пользователей ({len(targets)} чел.)"
    else:
        add_lesson(target, weekday, time_str, subject, room)
        who_line = ""

    await update.message.reply_text(
        f"Добавлено ✅ {who_line}\n{WEEKDAY_NAMES_FULL_RU[weekday]}, {time_str} — "
        f"[{SUBJECT_NAME[subject]}], каб. {room}\n\n"
        f"Если хочешь, чтобы бот присылал фото/карту этого кабинета по утрам — "
        f"пришли его через /addroomphoto."
    )
    return ConversationHandler.END


async def schedule_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END


async def schedule_today_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    weekday = now_kz().weekday()
    rows = get_lessons(update.effective_chat.id, weekday)
    if not rows:
        await update.message.reply_text(
            f"На {WEEKDAY_NAMES_FULL_RU[weekday].lower()} пар не добавлено."
        )
        return
    heading = f"📅 Расписание на сегодня ({WEEKDAY_NAMES_FULL_RU[weekday].lower()})"
    text = format_lessons_block(rows, heading)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def schedule_week_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    rows = get_lessons(update.effective_chat.id)
    if not rows:
        await update.message.reply_text("Расписание пока пустое. Добавь уроки через /schedule_add.")
        return
    by_day = {i: [] for i in range(7)}
    for r in rows:
        by_day[r["weekday"]].append(r)
    today_weekday = now_kz().weekday()
    blocks = ["🗓 <b>Расписание на неделю</b>"]
    for i in range(7):
        if not by_day[i]:
            continue
        marker = " 📌" if i == today_weekday else ""
        heading = f"{WEEKDAY_EMOJI[i]} {WEEKDAY_NAMES_FULL_RU[i]}{marker}"
        blocks.append(format_lessons_block(by_day[i], heading))
    text = "\n\n".join(blocks)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


async def schedule_delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    rows = get_lessons(update.effective_chat.id)
    if not rows:
        await update.message.reply_text("Расписание пустое.")
        return
    buttons = [
        [InlineKeyboardButton(
            f"{WEEKDAY_NAMES_RU[r['weekday']]} {r['time']} {SUBJECT_NAME[r['subject']]}",
            callback_data=f"schdel:{r['id']}",
        )]
        for r in rows
    ]
    await update.message.reply_text(
        "Что удалить из расписания?", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def schedule_delete_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lesson_id = int(query.data.split(":")[1])
    delete_lesson(lesson_id)
    await query.edit_message_text("Удалено 🗑")


async def schedule_day_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    await update.message.reply_text(
        "На какой день недели показать расписание?",
        reply_markup=weekday_keyboard("schday"),
    )


async def schedule_day_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    weekday = int(query.data.split(":")[1])
    chat_id = update.effective_chat.id

    rows = get_lessons(chat_id, weekday)
    if not rows:
        await query.edit_message_text(
            f"На {WEEKDAY_NAMES_FULL_RU[weekday].lower()} пар не добавлено."
        )
        return

    marker = " 📌 (сегодня)" if weekday == now_kz().weekday() else ""
    heading = f"{WEEKDAY_EMOJI[weekday]} Расписание: {WEEKDAY_NAMES_FULL_RU[weekday]}{marker}"
    text = format_lessons_block(rows, heading)
    await query.edit_message_text(text, parse_mode=ParseMode.HTML)

    seen_rooms = []
    for r in rows:
        if r["room"] not in seen_rooms:
            seen_rooms.append(r["room"])

    sent_any_photo = False
    for room in seen_rooms:
        photo = get_room_photo(chat_id, room)
        if not photo:
            continue
        file_id, kind = photo
        sent_any_photo = True
        caption = f"📍 Кабинет {room}"
        if kind == "document":
            await query.message.reply_document(document=file_id, caption=caption)
        else:
            await query.message.reply_photo(photo=file_id, caption=caption)

    if not sent_any_photo:
        await query.message.reply_text(
            "ℹ️ Для кабинетов этого дня фото пока не сохранены (/addroomphoto)."
        )


async def addroomphoto_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    if is_admin(update.effective_user.id):
        await update.message.reply_text(
            "Для кого добавляем фото кабинета?", reply_markup=target_user_keyboard("phtgt")
        )
        return PHOTO_TARGET_USER

    context.user_data["photo_target_chat"] = update.effective_chat.id
    await update.message.reply_text(
        "Название/номер кабинета, для которого добавляем фото "
        "(пиши так же, как указывал в расписании, например «305»):"
    )
    return PHOTO_ROOM_NAME


async def addroomphoto_target_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    raw = query.data.split(":", 1)[1]
    if raw == "self":
        target = update.effective_chat.id
    elif raw == "all":
        target = ALL_USERS_SENTINEL
    else:
        target = int(raw)
    context.user_data["photo_target_chat"] = target
    await query.edit_message_text(
        "Название/номер кабинета, для которого добавляем фото "
        "(пиши так же, как указывал в расписании, например «305»):"
    )
    return PHOTO_ROOM_NAME


async def addroomphoto_room_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["photo_room"] = update.message.text.strip()
    await update.message.reply_text(
        "Теперь пришли фото — карту этажа или сам кабинет, где будет видно, где это находится."
    )
    return PHOTO_WAITING


def _save_room_photo_for_target(target, room: str, file_id: str, kind: str) -> str:
    if target == ALL_USERS_SENTINEL:
        targets = all_chat_ids()
        for chat_id in targets:
            set_room_photo(chat_id, room, file_id, kind=kind)
        return f" для всех известных пользователей ({len(targets)} чел.)"
    set_room_photo(target, room, file_id, kind=kind)
    return ""


async def addroomphoto_photo_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    room = context.user_data.pop("photo_room")
    target = context.user_data.pop("photo_target_chat")
    file_id = update.message.photo[-1].file_id
    who_line = _save_room_photo_for_target(target, room, file_id, kind="photo")
    await update.message.reply_text(f"Сохранено ✅ Фото для кабинета «{room}» добавлено{who_line}.")
    return ConversationHandler.END


async def addroomphoto_document_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc.mime_type or not doc.mime_type.startswith("image/"):
        await update.message.reply_text(
            "Этот файл не похож на изображение. Пришли фото/картинку кабинета."
        )
        return PHOTO_WAITING
    room = context.user_data.pop("photo_room")
    target = context.user_data.pop("photo_target_chat")
    who_line = _save_room_photo_for_target(target, room, doc.file_id, kind="document")
    await update.message.reply_text(f"Сохранено ✅ Фото для кабинета «{room}» добавлено{who_line}.")
    return ConversationHandler.END


async def addroomphoto_not_a_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Это не похоже на фото. Пришли картинку кабинета — как обычное фото "
        "или файлом-изображением (jpg/png)."
    )
    return PHOTO_WAITING


async def roomphotos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    rooms = list_room_photos(update.effective_chat.id)
    if not rooms:
        await update.message.reply_text(
            "Пока нет ни одного сохранённого фото кабинета. Добавь через /addroomphoto."
        )
        return
    await update.message.reply_text("Сохранённые фото кабинетов:\n" + "\n".join(f"• {r}" for r in rooms))


async def users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "Эта команда доступна только администраторам бота."
        )
        return
    rows = list_known_users()
    if not rows:
        await update.message.reply_text("Пока никто не писал боту.")
        return
    lines = [f"• {display_name(r)} — chat_id {r['chat_id']}" for r in rows]
    await update.message.reply_text("Известные пользователи:\n" + "\n".join(lines))


def get_chat_info(chat_id: int):
    conn = db()
    row = conn.execute("SELECT * FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()
    conn.close()
    return row


async def viewschedule_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "Эта команда доступна только администраторам бота."
        )
        return
    rows = list_known_users()
    if not rows:
        await update.message.reply_text("Пока никто не писал боту.")
        return
    buttons = [
        [InlineKeyboardButton(display_name(r), callback_data=f"viewsch:{r['chat_id']}")]
        for r in rows
    ]
    await update.message.reply_text(
        "Чьё расписание посмотреть?", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def viewschedule_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        await query.edit_message_text("Эта команда доступна только администраторам бота.")
        return

    target_chat_id = int(query.data.split(":", 1)[1])
    info = get_chat_info(target_chat_id)
    name = display_name(info) if info else f"chat_id {target_chat_id}"

    rows = get_lessons(target_chat_id)
    if not rows:
        await query.edit_message_text(f"У «{name}» расписание пока пустое.")
        return

    by_day = {i: [] for i in range(7)}
    for r in rows:
        by_day[r["weekday"]].append(r)
    blocks = [f"🗓 <b>Расписание «{html.escape(name)}»</b>"]
    for i in range(7):
        if not by_day[i]:
            continue
        heading = f"{WEEKDAY_EMOJI[i]} {WEEKDAY_NAMES_FULL_RU[i]}"
        blocks.append(format_lessons_block(by_day[i], heading))

    await query.edit_message_text("\n\n".join(blocks), parse_mode=ParseMode.HTML)


async def testphoto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    rooms = list_room_photos(update.effective_chat.id)
    if not rooms:
        await update.message.reply_text(
            "Пока нет ни одного сохранённого фото. Сначала добавь через /addroomphoto."
        )
        return
    context.user_data["testphoto_rooms"] = rooms
    buttons = [
        [InlineKeyboardButton(room, callback_data=f"testph:{i}")]
        for i, room in enumerate(rooms)
    ]
    await update.message.reply_text(
        "Какой кабинет проверить?", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def testphoto_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split(":", 1)[1])
    rooms = context.user_data.get("testphoto_rooms") or []
    if idx >= len(rooms):
        await query.message.reply_text("Список устарел, вызови /testphoto ещё раз.")
        return
    room = rooms[idx]
    chat_id = update.effective_chat.id
    photo = get_room_photo(chat_id, room)
    if not photo:
        await query.message.reply_text(f"Для кабинета «{room}» фото не найдено.")
        return
    file_id, kind = photo
    if kind == "document":
        await query.message.reply_document(document=file_id, caption=f"Кабинет {room} (тест)")
    else:
        await query.message.reply_photo(photo=file_id, caption=f"Кабинет {room} (тест)")


async def testmorning_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    sent = await send_morning_schedule_for_chat(context.bot, update.effective_chat.id)
    if not sent:
        weekday = now_kz().weekday()
        await update.message.reply_text(
            f"На {WEEKDAY_NAMES_FULL_RU[weekday].lower()} в расписании пар нет — "
            "поэтому утренняя рассылка ничего бы не отправила. "
            "Добавь пару через /schedule_add и попробуй снова."
        )


MONTH_NAMES_RU = [
    "", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]


def calendar_keyboard(chat_id: int, year: int, month: int) -> InlineKeyboardMarkup:
    busy_days = get_task_dates_in_month(chat_id, year, month)
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
        if d == today:
            style = "primary"
        elif iso_day in busy_days:
            style = "danger"
        else:
            style = "success"
        week.append(InlineKeyboardButton(str(day_num), callback_data=f"day:{iso_day}", style=style))

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

    prev_month, prev_year = (12, year - 1) if month == 1 else (month - 1, year)
    next_month, next_year = (1, year + 1) if month == 12 else (month + 1, year)
    rows.append([
        InlineKeyboardButton("←", callback_data=f"cal:{prev_year}:{prev_month}"),
        InlineKeyboardButton("Сегодня", callback_data=f"cal:{today.year}:{today.month}"),
        InlineKeyboardButton("→", callback_data=f"cal:{next_year}:{next_month}"),
    ])

    return InlineKeyboardMarkup(rows)


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
        reply_markup=calendar_keyboard(SHARED_TASKS_ID, year, month),
    )


async def calendar_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, year, month = query.data.split(":")
    year, month = int(year), int(month)
    await query.edit_message_text(
        f"{MONTH_NAMES_RU[month]} {year}\nНажми на день, чтобы посмотреть задания.",
        reply_markup=calendar_keyboard(SHARED_TASKS_ID, year, month),
    )


async def calendar_noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()


async def calendar_day_tap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    iso_day = query.data.split(":")[1]
    d = datetime.strptime(iso_day, "%Y-%m-%d").date()
    rows = get_tasks(SHARED_TASKS_ID, only_undone=False, start=iso_day, end=iso_day)
    if not rows:
        text = f"{d.strftime('%d.%m.%Y')} — заданий нет 🎉"
    else:
        lines = [f"{d.strftime('%d.%m.%Y')}:"]
        for r in rows:
            mark = "✅" if r["done"] else "▫️"
            time_part = f" ({r['due_time']})" if r["due_time"] else ""
            lines.append(f"{mark} [{SUBJECT_NAME[r['subject']]}] {r['title']}{time_part}")
        text = "\n".join(lines)
    await query.answer(text=text, show_alert=True)


async def send_daily_reminders(context: ContextTypes.DEFAULT_TYPE):
    today_iso = today_kz().isoformat()
    tomorrow_iso = (today_kz() + timedelta(days=1)).isoformat()
    rows = get_tasks(SHARED_TASKS_ID, start=today_iso, end=tomorrow_iso)
    if not rows:
        return
    text = "🔔 Напоминание (общий список заданий):\n" + "\n".join(format_task_line(r) for r in rows)
    for chat_id in all_chat_ids():
        try:
            await context.bot.send_message(chat_id=chat_id, text=text)
        except Exception as e:
            logger.warning("Could not message chat %s: %s", chat_id, e)


async def send_morning_schedule_for_chat(bot, chat_id: int, weekday: int = None) -> bool:
    if weekday is None:
        weekday = now_kz().weekday()

    rows = get_lessons(chat_id, weekday)
    if not rows:
        return False

    heading = f"🌅 Расписание на {WEEKDAY_NAMES_FULL_RU[weekday].lower()}"
    text = format_lessons_block(rows, heading)
    await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)

    seen_rooms = []
    for r in rows:
        if r["room"] not in seen_rooms:
            seen_rooms.append(r["room"])

    for room in seen_rooms:
        photo = get_room_photo(chat_id, room)
        if not photo:
            continue
        file_id, kind = photo
        caption = f"📍 Кабинет {room}"
        if kind == "document":
            await bot.send_document(chat_id=chat_id, document=file_id, caption=caption)
        else:
            await bot.send_photo(chat_id=chat_id, photo=file_id, caption=caption)

    return True


async def send_morning_schedule(context: ContextTypes.DEFAULT_TYPE):
    weekday = now_kz().weekday()
    for chat_id in all_chat_ids():
        try:
            await send_morning_schedule_for_chat(context.bot, chat_id, weekday)
        except Exception as e:
            logger.warning("Could not send morning schedule to chat %s: %s", chat_id, e)


def main():
    init_db()
    start_health_check_server()
    app = Application.builder().token(BOT_TOKEN).build()

    add_conv = ConversationHandler(
        entry_points=[CommandHandler("add", add_start)],
        states={
            CHOOSING_SUBJECT: [CallbackQueryHandler(add_subject_chosen, pattern="^addsub:")],
            TYPING_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_title_typed)],
            TYPING_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_date_typed)],
            TYPING_TIME: [
                CallbackQueryHandler(add_time_skip, pattern="^notime$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_time_typed),
            ],
        },
        fallbacks=[CommandHandler("cancel", add_cancel)],
    )

    schedule_add_conv = ConversationHandler(
        entry_points=[CommandHandler("schedule_add", schedule_add_start)],
        states={
            CHOOSING_TARGET_USER: [CallbackQueryHandler(schedule_target_chosen, pattern="^schtgt:")],
            SCH_WEEKDAY: [CallbackQueryHandler(schedule_weekday_chosen, pattern="^schwd:")],
            SCH_SUBJECT: [CallbackQueryHandler(schedule_subject_chosen, pattern="^schsub:")],
            SCH_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule_time_typed)],
            SCH_ROOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule_room_typed)],
        },
        fallbacks=[CommandHandler("cancel", schedule_cancel)],
    )

    addroomphoto_conv = ConversationHandler(
        entry_points=[CommandHandler("addroomphoto", addroomphoto_start)],
        states={
            PHOTO_TARGET_USER: [CallbackQueryHandler(addroomphoto_target_chosen, pattern="^phtgt:")],
            PHOTO_ROOM_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, addroomphoto_room_typed)],
            PHOTO_WAITING: [
                MessageHandler(filters.PHOTO, addroomphoto_photo_received),
                MessageHandler(filters.Document.IMAGE, addroomphoto_document_received),
                MessageHandler(~filters.COMMAND, addroomphoto_not_a_photo),
            ],
        },
        fallbacks=[CommandHandler("cancel", schedule_cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(add_conv)
    app.add_handler(schedule_add_conv)
    app.add_handler(addroomphoto_conv)
    app.add_handler(CommandHandler("today", today_cmd))
    app.add_handler(CommandHandler("week", week_cmd))
    app.add_handler(CommandHandler("all", all_cmd))
    app.add_handler(CommandHandler("done", done_cmd))
    app.add_handler(CallbackQueryHandler(done_chosen, pattern="^done:"))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(CallbackQueryHandler(delete_chosen, pattern="^del:"))
    app.add_handler(CommandHandler("calendar", calendar_cmd))
    app.add_handler(CallbackQueryHandler(calendar_nav, pattern="^cal:"))
    app.add_handler(CallbackQueryHandler(calendar_day_tap, pattern="^day:"))
    app.add_handler(CallbackQueryHandler(calendar_noop, pattern="^noop$"))
    app.add_handler(CommandHandler("schedule", schedule_today_cmd))
    app.add_handler(CommandHandler("schedule_week", schedule_week_cmd))
    app.add_handler(CommandHandler("schedule_day", schedule_day_start))
    app.add_handler(CallbackQueryHandler(schedule_day_chosen, pattern="^schday:"))
    app.add_handler(CommandHandler("schedule_delete", schedule_delete_cmd))
    app.add_handler(CallbackQueryHandler(schedule_delete_chosen, pattern="^schdel:"))
    app.add_handler(CommandHandler("roomphotos", roomphotos_cmd))
    app.add_handler(CommandHandler("users", users_cmd))
    app.add_handler(CommandHandler("viewschedule", viewschedule_cmd))
    app.add_handler(CallbackQueryHandler(viewschedule_chosen, pattern="^viewsch:"))
    app.add_handler(CommandHandler("testphoto", testphoto_cmd))
    app.add_handler(CallbackQueryHandler(testphoto_chosen, pattern="^testph:"))
    app.add_handler(CommandHandler("testmorning", testmorning_cmd))

    app.job_queue.run_daily(
        send_daily_reminders,
        time=dtime(hour=REMINDER_HOUR, minute=REMINDER_MINUTE, tzinfo=TIMEZONE),
    )
    app.job_queue.run_daily(
        send_morning_schedule,
        time=dtime(hour=SCHEDULE_HOUR, minute=SCHEDULE_MINUTE, tzinfo=TIMEZONE),
    )

    logger.info("Bot starting (polling)...")
    app.run_polling()


if __name__ == "__main__":
    main()
