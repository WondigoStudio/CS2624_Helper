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

import asyncio
import calendar
import html
import logging
import os
import random
import sqlite3
import threading
from datetime import datetime, date, time as dtime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None

try:
    import requests
except ImportError:
    requests = None

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    InlineQueryHandler,
    filters,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Token comes from an environment variable (BOT_TOKEN) so it's never
# committed to git or hard-coded in the file. Set it in Render's dashboard
# under Environment. Locally you can export it before running, e.g.:
#   export BOT_TOKEN="123456:ABC..."   (Windows: set BOT_TOKEN=123456:ABC...)
# Only required to actually start the bot (checked in main()) — importing
# this module for other purposes (e.g. migrate_to_postgres.py) works without it.
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

# Where the SQLite file lives. On Render, point this (via the DB_PATH env
# var) at your persistent disk's mount path, e.g. /var/data/homework.db —
# otherwise the database is wiped on every deploy/restart (Render's default
# filesystem is ephemeral). Locally this just defaults to a file next to
# this script. IGNORED if DATABASE_URL is set (see below).
DB_PATH = Path(os.environ.get("DB_PATH", str(Path(__file__).parent / "homework.db")))

# If set, the bot uses this Postgres database instead of the local SQLite
# file — this is the "online DB" that survives redeploys/restarts on its
# own, with no disk to attach. Get a free, permanent Postgres database from
# https://neon.tech (or Supabase, or Render's own Postgres — Render's free
# Postgres auto-deletes after 30 days, so Neon/Supabase are the safer free
# choice). The connection string looks like:
#   postgresql://user:password@host/dbname?sslmode=require
# Put it in the DATABASE_URL environment variable, locally or on Render.
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_POSTGRES = bool(DATABASE_URL)
if USE_POSTGRES and psycopg2 is None:
    raise SystemExit(
        "DATABASE_URL is set but psycopg2 isn't installed. "
        "Run: pip install -r requirements.txt"
    )

TIMEZONE = ZoneInfo("Asia/Almaty")

REMINDER_HOUR = 8
REMINDER_MINUTE = 0

SCHEDULE_HOUR = 23
SCHEDULE_MINUTE = 00

ADMIN_IDS = {1762280778}

# Voice/audio/video transcription (via Groq's free Whisper API). Get a free
# key (no card needed) at https://console.groq.com -> API Keys, then set it
# as the GROQ_API_KEY environment variable. Any voice message, audio file,
# video note or video sent to the bot is auto-transcribed and replied to —
# the actual speech-to-text work happens on Groq's servers, so this needs
# almost no CPU/RAM locally, which matters on Render's free tier.
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_WHISPER_MODEL = "whisper-large-v3"
TRANSCRIBE_ENABLED = bool(GROQ_API_KEY)

# Translation: reply to any message and mention the bot (@botusername) in
# your reply — the bot translates the original message into Russian.
# Reuses the same GROQ_API_KEY as transcription, via Groq's free chat
# models (no separate setup needed).
GROQ_TRANSLATE_MODEL = "openai/gpt-oss-20b"
TRANSLATE_ENABLED = bool(GROQ_API_KEY)

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

# ---------------------------------------------------------------------------
# Fun reply actions ("обнять", "ударить", etc.) — Iris-bot style. Reply to
# someone's message with one of these words (no slash, just the plain word)
# and the bot posts a little scene with both names. {a} = the person who
# sent the action, {t} = the person being replied to.
# ---------------------------------------------------------------------------

ACTIONS = {
    "обнять":        ("🤗", ["{a} крепко обнял(а) {t}"]),
    "погладить":     ("🥰", ["{a} нежно погладил(а) {t} по голове"]),
    "поцеловать":    ("😘", ["{a} поцеловал(а) {t}"]),
    "ударить":       ("👊", ["{a} со всей силы ударил(а) {t}"]),
    "пнуть":         ("🦵", ["{a} от души пнул(а) {t}"]),
    "укусить":       ("😈", ["{a} укусил(а) {t}"]),
    "ущипнуть":      ("🤏", ["{a} ущипнул(а) {t}"]),
    "толкнуть":      ("🫸", ["{a} толкнул(а) {t}"]),
    "шлёпнуть":      ("👋", ["{a} шлёпнул(а) {t}"]),
    "дать пять":     ("✋", ["{a} дал(а) пять {t}"]),
    "потрепать":     ("🖐", ["{a} потрепал(а) {t} по щеке"]),
    "взъерошить":    ("💇", ["{a} взъерошил(а) волосы {t}"]),
    "подмигнуть":    ("😉", ["{a} подмигнул(а) {t}"]),
    "помахать":      ("👋", ["{a} помахал(а) {t}"]),
    "потискать":     ("🫂", ["{a} затискал(а) {t}"]),
    "защекотать":    ("🤣", ["{a} защекотал(а) {t} до слёз"]),
    "взять за руку": ("🤝", ["{a} взял(а) {t} за руку"]),
    "станцевать":    ("💃", ["{a} закружил(а) {t} в танце"]),
    "подзатыльник":  ("🖐", ["{a} дал(а) подзатыльник {t}"]),
    "оплеуха":       ("✋", ["{a} отвесил(а) оплеуху {t}"]),
    "погрозить":     ("✊", ["{a} погрозил(а) кулаком {t}"]),
    "показать язык": ("😛", ["{a} показал(а) язык {t}"]),
    "комплимент":    ("💬", ["{a} сделал(а) комплимент {t}"]),
    "признаться":    ("❤️", ["{a} признался(ась) в любви {t}"]),
    "торт в лицо":   ("🎂", ["{a} кинул(а) тортом в лицо {t}"]),
    "облить водой":  ("💦", ["{a} облил(а) водой {t}"]),
    "напугать":      ("👻", ["{a} напугал(а) {t}"]),
    "засмеять":      ("😂", ["{a} поднял(а) на смех {t}"]),
    "извиниться":    ("🙏", ["{a} извинился(лась) перед {t}"]),
    "поблагодарить": ("🙌", ["{a} поблагодарил(а) {t}"]),
}

# Optional: map an action key -> a Telegram custom (premium) emoji ID, to
# use INSTEAD of the plain unicode emoji when sending the standalone
# animated-emoji message. Requires the account that created this bot (via
# @BotFather) to have Telegram Premium — otherwise sending silently falls
# back to plain emoji. Get IDs with /emojiid (admin-only, see below), then
# fill them in here, e.g.:
#   CUSTOM_EMOJI_IDS = {"обнять": "5368324170671202286", ...}
CUSTOM_EMOJI_IDS: dict = {
    "засмеять": "5370953476635368811",
}

CHOOSING_SUBJECT, TYPING_TITLE, TYPING_DATE, TYPING_TIME = range(4)
CHOOSING_TARGET_USER, SCH_WEEKDAY, SCH_SUBJECT, SCH_TIME, SCH_ROOM = range(4, 9)
PHOTO_TARGET_USER, PHOTO_ROOM_NAME, PHOTO_WAITING = range(9, 12)

# Conversation states for /edittask (edit an existing homework task)
EDIT_TASK_PICK, EDIT_TASK_FIELD, EDIT_TASK_SUBJECT, EDIT_TASK_TITLE, EDIT_TASK_DATE, EDIT_TASK_TIME = range(12, 18)

# Conversation states for /editschedule (edit an existing timetable lesson)
EDIT_LESSON_PICK, EDIT_LESSON_FIELD, EDIT_LESSON_WEEKDAY, EDIT_LESSON_SUBJECT, EDIT_LESSON_TIME, EDIT_LESSON_ROOM = range(18, 24)

# Extra states for /add: description (after title) and attachment (after time)
TYPING_DESCRIPTION, TYPING_ATTACHMENT = range(24, 26)

# Extra state for /edittask: editing description or attachment of an existing task
EDIT_TASK_DESCRIPTION, EDIT_TASK_ATTACHMENT = range(26, 28)

# How long before a lesson starts to send a heads-up reminder
LESSON_REMINDER_MINUTES = 10

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
    if USE_POSTGRES:
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor)
        return _PGConn(conn)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


class _PGConn:
    """Thin wrapper so the rest of the code (written for sqlite3) can use a
    Postgres connection unchanged: '?' placeholders are translated to '%s',
    and RealDictCursor rows already support row["col"] like sqlite3.Row."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=()):
        cur = self._conn.cursor()
        cur.execute(sql.replace("?", "%s"), params)
        return cur

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


def _existing_columns(conn, table: str) -> set:
    """PRAGMA table_info doesn't exist in Postgres; this works on both."""
    if USE_POSTGRES:
        cur = conn.execute(
            "SELECT column_name AS name FROM information_schema.columns WHERE table_name = ?",
            (table,),
        )
        return {r["name"] for r in cur.fetchall()}
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def init_db():
    conn = db()
    id_pk = "SERIAL PRIMARY KEY" if USE_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS tasks (
            id {id_pk},
            chat_id BIGINT NOT NULL,
            subject TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            due_date TEXT NOT NULL,
            due_time TEXT,
            done INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            created_by TEXT,
            attachment_file_id TEXT,
            attachment_kind TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chats (
            chat_id BIGINT PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS schedule (
            id {id_pk},
            chat_id BIGINT NOT NULL,
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
            chat_id BIGINT NOT NULL,
            room TEXT NOT NULL,
            file_id TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'photo',
            PRIMARY KEY (chat_id, room)
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS actions (
            id {id_pk},
            chat_id BIGINT NOT NULL,
            actor_id BIGINT NOT NULL,
            target_id BIGINT NOT NULL,
            action TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    existing_cols = _existing_columns(conn, "tasks")
    if "due_time" not in existing_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN due_time TEXT")
    if "created_by" not in existing_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN created_by TEXT")
    if "description" not in existing_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN description TEXT")
    if "attachment_file_id" not in existing_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN attachment_file_id TEXT")
    if "attachment_kind" not in existing_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN attachment_kind TEXT")
    room_cols = _existing_columns(conn, "room_photos")
    if "kind" not in room_cols:
        conn.execute("ALTER TABLE room_photos ADD COLUMN kind TEXT NOT NULL DEFAULT 'photo'")
    chat_cols = _existing_columns(conn, "chats")
    for col in ("username", "first_name", "last_name", "updated_at"):
        if col not in chat_cols:
            conn.execute(f"ALTER TABLE chats ADD COLUMN {col} TEXT")
    if USE_POSTGRES:
        # Widen chat_id to BIGINT for anyone whose database was created by
        # an earlier version of this schema that used plain INTEGER —
        # modern Telegram user/chat ids commonly exceed the 32-bit range.
        # Safe/no-op if the column is already BIGINT.
        for table in ("chats", "tasks", "schedule", "room_photos"):
            conn.execute(f"ALTER TABLE {table} ALTER COLUMN chat_id TYPE BIGINT")
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


def add_task(chat_id: int, subject: str, title: str, due_date: str, due_time: str = None,
             created_by: str = None, description: str = None,
             attachment_file_id: str = None, attachment_kind: str = None):
    conn = db()
    conn.execute(
        "INSERT INTO tasks (chat_id, subject, title, due_date, due_time, created_at, "
        "created_by, description, attachment_file_id, attachment_kind) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (chat_id, subject, title, due_date, due_time, now_kz().isoformat(),
         created_by, description, attachment_file_id, attachment_kind),
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


# whitelisted so it's always a fixed, known column name — never built from
# unsanitized user input, even though it's f-string-interpolated below
_TASK_EDITABLE_FIELDS = {"subject", "title", "due_date", "due_time", "description"}


def update_task_field(task_id: int, field: str, value):
    if field not in _TASK_EDITABLE_FIELDS:
        raise ValueError(f"Not an editable task field: {field}")
    conn = db()
    conn.execute(f"UPDATE tasks SET {field} = ? WHERE id = ?", (value, task_id))
    conn.commit()
    conn.close()


def update_task_attachment(task_id: int, file_id, kind):
    conn = db()
    conn.execute(
        "UPDATE tasks SET attachment_file_id = ?, attachment_kind = ? WHERE id = ?",
        (file_id, kind, task_id),
    )
    conn.commit()
    conn.close()


def get_task(task_id: int):
    conn = db()
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    return row


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


_LESSON_EDITABLE_FIELDS = {"weekday", "subject", "time", "room"}


def update_lesson_field(lesson_id: int, field: str, value):
    if field not in _LESSON_EDITABLE_FIELDS:
        raise ValueError(f"Not an editable lesson field: {field}")
    conn = db()
    conn.execute(f"UPDATE schedule SET {field} = ? WHERE id = ?", (value, lesson_id))
    conn.commit()
    conn.close()


def get_lesson(lesson_id: int):
    conn = db()
    row = conn.execute("SELECT * FROM schedule WHERE id = ?", (lesson_id,)).fetchone()
    conn.close()
    return row


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


def log_action_and_count(chat_id: int, actor_id: int, target_id: int, action: str) -> int:
    """Records one occurrence of a fun reply-action and returns how many
    times this exact actor -> target -> action combo has now happened in
    this chat (including this one)."""
    conn = db()
    conn.execute(
        "INSERT INTO actions (chat_id, actor_id, target_id, action, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (chat_id, actor_id, target_id, action, now_kz().isoformat()),
    )
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM actions WHERE chat_id = ? AND actor_id = ? "
        "AND target_id = ? AND action = ?",
        (chat_id, actor_id, target_id, action),
    ).fetchone()
    conn.commit()
    conn.close()
    return row["c"]


def top_actions(chat_id: int, limit: int = 5):
    """Most frequent actor->target->action combos in this chat."""
    conn = db()
    rows = conn.execute(
        "SELECT actor_id, target_id, action, COUNT(*) AS c FROM actions "
        "WHERE chat_id = ? GROUP BY actor_id, target_id, action "
        "ORDER BY c DESC LIMIT ?",
        (chat_id, limit),
    ).fetchall()
    conn.close()
    return rows


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
    attach_part = " 📎" if row["attachment_file_id"] else ""
    line = f"#{row['id']} [{SUBJECT_NAME[row['subject']]}] {row['title']} — {d.strftime('%d.%m.%Y')}{time_part}{tag}{attach_part}{by_part}"
    if row["description"]:
        line += f"\n    📝 {row['description']}"
    return line


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
    text = (
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
        "/edittask — изменить задание (предмет/текст/описание/дату/время/вложение)\n"
        "/taskfile — показать вложение (фото/файл) у задания\n"
        "/calendar — календарь месяца кнопками: зелёная — свободный день, "
        "красная — есть задание, синяя — сегодня. Нажми на день — покажу "
        "что на него задано\n\n"
        "Расписание пар и кабинеты:\n"
        "/schedule_add — добавить пару в расписание (день недели → предмет → время → кабинет)\n"
        "/schedule — расписание на сегодня\n"
        "/schedule_week — расписание на всю неделю\n"
        "/schedule_day — расписание на выбранный день недели + фото кабинетов\n"
        "/schedule_delete — удалить пару из расписания\n"
        "/editschedule — изменить пару (день/предмет/время/кабинет)\n"
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
        "кабинетов, если они сохранены.\n"
        f"Плюс за {LESSON_REMINDER_MINUTES} минут до каждой пары пришлю короткое напоминание."
    )
    if TRANSCRIBE_ENABLED:
        text += (
            "\n\n🎙 Ещё умею: пришли голосовое, аудио, кружок или видео — расшифрую "
            "речь в текст автоматически, без команд."
        )
    if TRANSLATE_ENABLED:
        text += (
            f"\n🌐 И перевожу: ответь на любое сообщение (реплаем) и упомяни меня "
            f"через @{context.bot.username} в тексте ответа — переведу его на русский.\n"
            f"А ещё можно вызвать меня где угодно, даже там, где меня нет в чате — "
            f"просто напиши @{context.bot.username} и текст в любом окне ввода Telegram."
        )
    text += (
        "\n\n🎭 Ещё есть весёлые команды: ответь на чьё-нибудь сообщение словом вроде "
        "«обнять», «погладить», «ударить», «поцеловать» и т.п. (всего 30 штук) — "
        "пришлю шуточную сценку с вашими именами. /topactions — топ по чату."
    )
    await update.message.reply_text(text)


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
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Без описания", callback_data="nodesc")]
    ])
    await update.message.reply_text(
        "Добавь подробное описание задания (что именно нужно сделать, номера "
        "заданий и т.п.), или нажми кнопку, если название всё уже объясняет.",
        reply_markup=keyboard,
    )
    return TYPING_DESCRIPTION


async def add_description_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_description"] = update.message.text.strip()
    await update.message.reply_text(
        "Когда сдавать? Напиши дату в формате ДД.ММ или ДД.ММ.ГГГГ, "
        "либо просто «сегодня» / «завтра»."
    )
    return TYPING_DATE


async def add_description_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["new_description"] = None
    await query.edit_message_text(
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


async def _prompt_for_attachment(target_message):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Без вложения", callback_data="noattach")]
    ])
    await target_message(
        "Прикрепи фото или файл к заданию (скан условия, фото с доски и т.п.), "
        "или нажми кнопку, если вложение не нужно.",
        reply_markup=keyboard,
    )


async def _finish_add_task(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int,
    attachment_file_id: str = None, attachment_kind: str = None,
):
    subject = context.user_data.pop("new_subject")
    title = context.user_data.pop("new_title")
    description = context.user_data.pop("new_description", None)
    due_date_iso = context.user_data.pop("new_date")
    due_time = context.user_data.pop("new_time", "")
    creator_name = context.user_data.pop("new_creator", None)
    add_task(
        chat_id, subject, title, due_date_iso, due_time or None,
        created_by=creator_name, description=description,
        attachment_file_id=attachment_file_id, attachment_kind=attachment_kind,
    )

    d = datetime.strptime(due_date_iso, "%Y-%m-%d").date()
    time_part = f", {due_time}" if due_time else ""
    desc_part = f"\n📝 {description}" if description else ""
    attach_part = "\n📎 вложение сохранено" if attachment_file_id else ""
    return f"Готово ✅\n[{SUBJECT_NAME[subject]}] {title} — {d.strftime('%d.%m.%Y')}{time_part}{desc_part}{attach_part}"


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


async def add_time_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = parse_due_time(update.message.text)
    if t is None:
        await update.message.reply_text(
            "Не понял время. Напиши в формате ЧЧ:ММ (например 09:00), "
            "или «нет», если время не нужно."
        )
        return TYPING_TIME
    context.user_data["new_time"] = t
    context.user_data["new_creator"] = user_short_name(update)
    await _prompt_for_attachment(update.message.reply_text)
    return TYPING_ATTACHMENT


async def add_time_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["new_time"] = ""
    context.user_data["new_creator"] = user_short_name(update)
    await query.edit_message_text("Без точного времени.")
    await _prompt_for_attachment(query.message.reply_text)
    return TYPING_ATTACHMENT


async def add_attachment_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file_id = update.message.photo[-1].file_id
    text = await _finish_add_task(context, SHARED_TASKS_ID, file_id, "photo")
    await update.message.reply_text(text)
    return ConversationHandler.END


async def add_attachment_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    text = await _finish_add_task(context, SHARED_TASKS_ID, doc.file_id, "document")
    await update.message.reply_text(text)
    return ConversationHandler.END


async def add_attachment_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = await _finish_add_task(context, SHARED_TASKS_ID)
    await query.edit_message_text(text)
    return ConversationHandler.END


async def add_attachment_invalid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Это не похоже на фото или файл. Пришли картинку/документ, или нажми "
        "«Без вложения»."
    )
    return TYPING_ATTACHMENT


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


# --- /edittask: change subject, title, date or time of an existing task ---

async def taskfile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lets anyone re-send the attachment of a task that has one, without
    scrolling back to find the original message."""
    register_chat(update)
    rows = [r for r in get_tasks(SHARED_TASKS_ID, only_undone=False) if r["attachment_file_id"]]
    if not rows:
        await update.message.reply_text("Ни у одного задания пока нет вложения.")
        return
    buttons = [
        [InlineKeyboardButton(
            f"{SUBJECT_NAME[r['subject']]}: {r['title'][:35]}",
            callback_data=f"taskfile:{r['id']}",
        )]
        for r in rows
    ]
    await update.message.reply_text(
        "У какого задания показать вложение?", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def taskfile_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split(":")[1])
    task = get_task(task_id)
    if not task or not task["attachment_file_id"]:
        await query.message.reply_text("Вложение не найдено.")
        return
    caption = f"[{SUBJECT_NAME[task['subject']]}] {task['title']}"
    if task["attachment_kind"] == "document":
        await query.message.reply_document(document=task["attachment_file_id"], caption=caption)
    else:
        await query.message.reply_photo(photo=task["attachment_file_id"], caption=caption)


async def edittask_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    rows = get_tasks(SHARED_TASKS_ID, only_undone=False)
    if not rows:
        await update.message.reply_text("Список заданий пуст.")
        return ConversationHandler.END
    buttons = [
        [InlineKeyboardButton(
            f"{SUBJECT_NAME[r['subject']]}: {r['title'][:35]}",
            callback_data=f"edittask:{r['id']}",
        )]
        for r in rows
    ]
    await update.message.reply_text(
        "Какое задание изменить?", reply_markup=InlineKeyboardMarkup(buttons)
    )
    return EDIT_TASK_PICK


def _edit_task_field_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Предмет", callback_data="editfield:subject")],
        [InlineKeyboardButton("Текст задания", callback_data="editfield:title")],
        [InlineKeyboardButton("Описание", callback_data="editfield:description")],
        [InlineKeyboardButton("Дата", callback_data="editfield:due_date")],
        [InlineKeyboardButton("Время", callback_data="editfield:due_time")],
        [InlineKeyboardButton("Вложение", callback_data="editfield:attachment")],
    ])


async def edittask_picked(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split(":")[1])
    context.user_data["edit_task_id"] = task_id
    await query.edit_message_text(
        "Что изменить в этом задании?", reply_markup=_edit_task_field_keyboard()
    )
    return EDIT_TASK_FIELD


async def edittask_field_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    field = query.data.split(":", 1)[1]

    if field == "subject":
        await query.edit_message_text("Выбери новый предмет:")
        await query.message.reply_text("Предмет:", reply_markup=subject_keyboard("edittasksub"))
        return EDIT_TASK_SUBJECT
    if field == "title":
        await query.edit_message_text("Напиши новый текст задания:")
        return EDIT_TASK_TITLE
    if field == "due_date":
        await query.edit_message_text(
            "Напиши новую дату в формате ДД.ММ или ДД.ММ.ГГГГ, либо «сегодня»/«завтра»:"
        )
        return EDIT_TASK_DATE
    if field == "due_time":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Без точного времени", callback_data="edittasktime:none")]
        ])
        await query.edit_message_text(
            "Напиши новое время в формате ЧЧ:ММ, или нажми кнопку, чтобы убрать время:",
            reply_markup=keyboard,
        )
        return EDIT_TASK_TIME
    if field == "description":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Убрать описание", callback_data="edittaskdesc:none")]
        ])
        await query.edit_message_text(
            "Напиши новое описание, или нажми кнопку, чтобы убрать его:",
            reply_markup=keyboard,
        )
        return EDIT_TASK_DESCRIPTION
    if field == "attachment":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Убрать вложение", callback_data="edittaskattach:none")]
        ])
        await query.edit_message_text(
            "Пришли новое фото/файл вложения, или нажми кнопку, чтобы убрать его:",
            reply_markup=keyboard,
        )
        return EDIT_TASK_ATTACHMENT


async def edittask_subject_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    subject = query.data.split(":")[1]
    task_id = context.user_data.pop("edit_task_id")
    update_task_field(task_id, "subject", subject)
    await query.edit_message_text(f"Готово ✅ Предмет изменён на «{SUBJECT_NAME[subject]}».")
    return ConversationHandler.END


async def edittask_title_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task_id = context.user_data.pop("edit_task_id")
    new_title = update.message.text.strip()
    update_task_field(task_id, "title", new_title)
    await update.message.reply_text(f"Готово ✅ Текст задания изменён на «{new_title}».")
    return ConversationHandler.END


async def edittask_date_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = parse_due_date(update.message.text)
    if d is None:
        await update.message.reply_text(
            "Не понял дату. Попробуй ещё раз, например: 05.10 или 05.10.2026"
        )
        return EDIT_TASK_DATE
    task_id = context.user_data.pop("edit_task_id")
    update_task_field(task_id, "due_date", d.isoformat())
    await update.message.reply_text(f"Готово ✅ Дата изменена на {d.strftime('%d.%m.%Y')}.")
    return ConversationHandler.END


async def edittask_time_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = parse_due_time(update.message.text)
    if t is None:
        await update.message.reply_text(
            "Не понял время. Напиши в формате ЧЧ:ММ (например 09:00), "
            "или «нет», чтобы убрать время."
        )
        return EDIT_TASK_TIME
    task_id = context.user_data.pop("edit_task_id")
    update_task_field(task_id, "due_time", t or None)
    await update.message.reply_text(
        f"Готово ✅ Время изменено на {t}." if t else "Готово ✅ Время убрано."
    )
    return ConversationHandler.END


async def edittask_time_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = context.user_data.pop("edit_task_id")
    update_task_field(task_id, "due_time", None)
    await query.edit_message_text("Готово ✅ Время убрано.")
    return ConversationHandler.END


async def edittask_description_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task_id = context.user_data.pop("edit_task_id")
    new_description = update.message.text.strip()
    update_task_field(task_id, "description", new_description)
    await update.message.reply_text("Готово ✅ Описание обновлено.")
    return ConversationHandler.END


async def edittask_description_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = context.user_data.pop("edit_task_id")
    update_task_field(task_id, "description", None)
    await query.edit_message_text("Готово ✅ Описание убрано.")
    return ConversationHandler.END


async def edittask_attachment_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task_id = context.user_data.pop("edit_task_id")
    file_id = update.message.photo[-1].file_id
    update_task_attachment(task_id, file_id, "photo")
    await update.message.reply_text("Готово ✅ Вложение обновлено.")
    return ConversationHandler.END


async def edittask_attachment_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task_id = context.user_data.pop("edit_task_id")
    doc = update.message.document
    update_task_attachment(task_id, doc.file_id, "document")
    await update.message.reply_text("Готово ✅ Вложение обновлено.")
    return ConversationHandler.END


async def edittask_attachment_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = context.user_data.pop("edit_task_id")
    update_task_attachment(task_id, None, None)
    await query.edit_message_text("Готово ✅ Вложение убрано.")
    return ConversationHandler.END


async def edittask_attachment_invalid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Это не похоже на фото или файл. Пришли картинку/документ, или нажми "
        "«Убрать вложение»."
    )
    return EDIT_TASK_ATTACHMENT


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


# --- /editschedule: change weekday, subject, time or room of a lesson -----

async def editschedule_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    rows = get_lessons(update.effective_chat.id)
    if not rows:
        await update.message.reply_text("Расписание пустое. Добавь пару через /schedule_add.")
        return ConversationHandler.END
    buttons = [
        [InlineKeyboardButton(
            f"{WEEKDAY_NAMES_RU[r['weekday']]} {r['time']} {SUBJECT_NAME[r['subject']]}",
            callback_data=f"editlesson:{r['id']}",
        )]
        for r in rows
    ]
    await update.message.reply_text(
        "Какую пару изменить?", reply_markup=InlineKeyboardMarkup(buttons)
    )
    return EDIT_LESSON_PICK


def _edit_lesson_field_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("День недели", callback_data="editlfield:weekday")],
        [InlineKeyboardButton("Предмет", callback_data="editlfield:subject")],
        [InlineKeyboardButton("Время", callback_data="editlfield:time")],
        [InlineKeyboardButton("Кабинет", callback_data="editlfield:room")],
    ])


async def editschedule_picked(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lesson_id = int(query.data.split(":")[1])
    context.user_data["edit_lesson_id"] = lesson_id
    await query.edit_message_text(
        "Что изменить в этой паре?", reply_markup=_edit_lesson_field_keyboard()
    )
    return EDIT_LESSON_FIELD


async def editschedule_field_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    field = query.data.split(":", 1)[1]

    if field == "weekday":
        await query.edit_message_text("Выбери новый день недели:")
        await query.message.reply_text("День недели:", reply_markup=weekday_keyboard("editlwd"))
        return EDIT_LESSON_WEEKDAY
    if field == "subject":
        await query.edit_message_text("Выбери новый предмет:")
        await query.message.reply_text("Предмет:", reply_markup=subject_keyboard("editlsub"))
        return EDIT_LESSON_SUBJECT
    if field == "time":
        await query.edit_message_text("Напиши новое время начала в формате ЧЧ:ММ:")
        return EDIT_LESSON_TIME
    if field == "room":
        await query.edit_message_text("Напиши новый номер/название кабинета:")
        return EDIT_LESSON_ROOM


async def editschedule_weekday_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    weekday = int(query.data.split(":")[1])
    lesson_id = context.user_data.pop("edit_lesson_id")
    update_lesson_field(lesson_id, "weekday", weekday)
    await query.edit_message_text(f"Готово ✅ День изменён на {WEEKDAY_NAMES_FULL_RU[weekday]}.")
    return ConversationHandler.END


async def editschedule_subject_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    subject = query.data.split(":")[1]
    lesson_id = context.user_data.pop("edit_lesson_id")
    update_lesson_field(lesson_id, "subject", subject)
    await query.edit_message_text(f"Готово ✅ Предмет изменён на «{SUBJECT_NAME[subject]}».")
    return ConversationHandler.END


async def editschedule_time_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = parse_due_time(update.message.text)
    if not t:  # time is required for a lesson — "" (skip word) and None both invalid
        await update.message.reply_text(
            "Не понял время. Напиши в формате ЧЧ:ММ, например 09:00."
        )
        return EDIT_LESSON_TIME
    lesson_id = context.user_data.pop("edit_lesson_id")
    update_lesson_field(lesson_id, "time", t)
    await update.message.reply_text(f"Готово ✅ Время изменено на {t}.")
    return ConversationHandler.END


async def editschedule_room_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    room = update.message.text.strip()
    lesson_id = context.user_data.pop("edit_lesson_id")
    update_lesson_field(lesson_id, "room", room)
    await update.message.reply_text(f"Готово ✅ Кабинет изменён на «{room}».")
    return ConversationHandler.END


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
            attach_part = " 📎" if r["attachment_file_id"] else ""
            desc_part = " 📝" if r["description"] else ""
            lines.append(f"{mark} [{SUBJECT_NAME[r['subject']]}] {r['title']}{time_part}{attach_part}{desc_part}")
        text = "\n".join(lines)
    await query.answer(text=text, show_alert=True)

    # buttons underneath to see the full description or open an attachment —
    # the popup alert itself can't carry buttons, send files, or fit a long
    # description (it's capped around 200 characters by Telegram)
    with_extras = [r for r in rows if r["attachment_file_id"] or r["description"]]
    if with_extras:
        buttons = []
        for r in with_extras:
            label = f"{SUBJECT_NAME[r['subject']]}: {r['title'][:30]}"
            if r["description"]:
                buttons.append([InlineKeyboardButton(f"📝 {label}", callback_data=f"taskdesc:{r['id']}")])
            if r["attachment_file_id"]:
                buttons.append([InlineKeyboardButton(f"📎 {label}", callback_data=f"taskfile:{r['id']}")])
        await query.message.reply_text(
            f"Подробнее про задания на {d.strftime('%d.%m.%Y')}:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )


async def taskdesc_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split(":")[1])
    task = get_task(task_id)
    if not task or not task["description"]:
        await query.message.reply_text("Описания нет.")
        return
    await query.message.reply_text(
        f"📝 [{SUBJECT_NAME[task['subject']]}] {task['title']}:\n\n{task['description']}"
    )


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


# ---------------------------------------------------------------------------
# Voice/audio/video transcription (Groq's free Whisper API)
# ---------------------------------------------------------------------------

def _transcribe_with_groq(audio_bytes: bytes, filename: str) -> str:
    """Blocking HTTP call — always run this via asyncio.to_thread so it
    doesn't stall the bot's event loop while waiting on the network."""
    resp = requests.post(
        "https://api.groq.com/openai/v1/audio/transcriptions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        files={"file": (filename, bytes(audio_bytes))},
        data={"model": GROQ_WHISPER_MODEL, "response_format": "json"},
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json().get("text", "").strip()


async def handle_transcribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    media = msg.voice or msg.audio or msg.video_note or msg.video
    if media is None:
        return

    # Telegram's own size cap for bots downloading files is 20 MB; Groq's
    # free tier also caps around 25 MB — bail out early with a clear reason
    # instead of a confusing failure partway through.
    if media.file_size and media.file_size > 20 * 1024 * 1024:
        await msg.reply_text("Это сообщение слишком большое, чтобы распознать (лимит ~20 МБ).")
        return

    status = await msg.reply_text("🎙 Распознаю речь…")
    try:
        tg_file = await context.bot.get_file(media.file_id)
        audio_bytes = await tg_file.download_as_bytearray()

        if msg.voice:
            filename = "voice.ogg"
        elif msg.video_note:
            filename = "video_note.mp4"
        elif msg.video:
            filename = "video.mp4"
        else:
            filename = getattr(media, "file_name", None) or "audio.mp3"

        text = await asyncio.to_thread(_transcribe_with_groq, audio_bytes, filename)

        if not text:
            await status.edit_text("Не удалось разобрать речь — похоже, там тишина или шум.")
            return
        quoted = f"🗣 Транскрипция:\n<blockquote>{html.escape(text)}</blockquote>"
        await status.edit_text(quoted, parse_mode=ParseMode.HTML)
    except requests.exceptions.RequestException as e:
        logger.warning("Groq transcription request failed: %s", e)
        await status.edit_text("Не получилось распознать — сервис транскрипции сейчас недоступен.")
    except Exception as e:
        logger.warning("Transcription failed: %s", e)
        await status.edit_text("Не получилось распознать это сообщение.")


# ---------------------------------------------------------------------------
# Translation to Russian (reply + mention the bot) — Groq's free chat models
# ---------------------------------------------------------------------------

def _translate_with_groq(text: str) -> str:
    """Blocking HTTP call — run via asyncio.to_thread. Uses a plain chat
    completion (Groq's Whisper endpoint only transcribes, it doesn't
    translate arbitrary already-written text)."""
    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        json={
            "model": GROQ_TRANSLATE_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a translator. Translate the user's message into "
                        "Russian, however many languages it mixes or whatever language "
                        "it's already in. Output ONLY the translation itself — no "
                        "quotes, no explanations, no language names, nothing else. "
                        "If the text is already entirely in Russian, output it unchanged."
                    ),
                },
                {"role": "user", "content": text},
            ],
            "temperature": 0.2,
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def _message_mentions_bot(update: Update, bot_username: str) -> bool:
    msg = update.message
    if not msg.text or not bot_username:
        return False
    return f"@{bot_username.lower()}" in msg.text.lower()


async def handle_action_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str):
    msg = update.message
    original = msg.reply_to_message
    target_user = original.from_user if original else None
    if target_user is None:
        return

    emoji, phrases = ACTIONS[action]
    actor_link = mention_html(update.effective_user)
    target_link = mention_html(target_user)
    phrase = random.choice(phrases).format(a=actor_link, t=target_link)

    count = log_action_and_count(
        update.effective_chat.id, update.effective_user.id, target_user.id, action
    )
    tail = f" (уже {count}-й раз!)" if count > 1 else ""
    await msg.reply_text(f"{emoji} {phrase}{tail}", parse_mode=ParseMode.HTML)

    # sent as its OWN message with nothing else in it — Telegram clients
    # play a full-screen animation when you tap a message that's only a
    # single "animatable" emoji (❤️🔥🎉😂👊 etc.); this doesn't work when
    # the emoji is mixed into a longer sentence, only standalone.
    # If a premium custom emoji id is configured for this action, use that
    # instead — falls back to the plain emoji if sending it fails (e.g. the
    # bot's owner account doesn't have Telegram Premium).
    custom_id = CUSTOM_EMOJI_IDS.get(action)
    if custom_id:
        try:
            await msg.reply_text(
                f'<tg-emoji id="{custom_id}">{emoji}</tg-emoji>',
                parse_mode=ParseMode.HTML,
            )
            return
        except Exception as e:
            logger.warning("Could not send custom emoji for '%s': %s", action, e)
    try:
        await msg.reply_text(emoji)
    except Exception as e:
        logger.warning("Could not send standalone emoji: %s", e)


async def handle_translate_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    original = msg.reply_to_message
    if original is None:
        return

    action_key = (msg.text or "").strip().lower()
    if action_key in ACTIONS:
        await handle_action_reply(update, context, action_key)
        return

    if not TRANSLATE_ENABLED or not _message_mentions_bot(update, context.bot.username):
        return

    source_text = original.text or original.caption
    if not source_text:
        await msg.reply_text("В этом сообщении нет текста для перевода.")
        return

    status = await msg.reply_text("🌐 Перевожу…")
    try:
        translated = await asyncio.to_thread(_translate_with_groq, source_text)
        quoted = f"🌐 Перевод:\n<blockquote>{html.escape(translated)}</blockquote>"
        await status.edit_text(quoted, parse_mode=ParseMode.HTML)
    except requests.exceptions.RequestException as e:
        logger.warning("Groq translation request failed: %s", e)
        await status.edit_text("Не получилось перевести — сервис сейчас недоступен.")
    except Exception as e:
        logger.warning("Translation failed: %s", e)
        await status.edit_text("Не получилось перевести это сообщение.")


async def emojiid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only helper: send this command, then send a message containing
    a custom/premium emoji — the bot replies with its custom_emoji_id, so
    you can paste it into CUSTOM_EMOJI_IDS in the code."""
    register_chat(update)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Эта команда доступна только администраторам бота.")
        return
    await update.message.reply_text(
        "Пришли сообщение с premium-эмодзи (можно вместе с другим текстом) — "
        "отвечу его custom_emoji_id."
    )


async def emojiid_capture(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Passive listener: whenever an admin's message contains a custom
    emoji entity, report its id. Doesn't interfere with anything else since
    it only replies when that entity type is actually present."""
    if not update.effective_user or not is_admin(update.effective_user.id):
        return
    entities = update.message.entities or []
    custom = [e for e in entities if e.type == "custom_emoji"]
    if not custom:
        return
    lines = ["Custom emoji ID:"]
    for e in custom:
        piece = update.message.text[e.offset: e.offset + e.length]
        lines.append(f"{piece} → <code>{e.custom_emoji_id}</code>")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def topactions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    rows = top_actions(update.effective_chat.id)
    if not rows:
        await update.message.reply_text(
            "Пока никто никого не обнял, не ударил и вообще ничего не делал 🙂"
        )
        return
    lines = ["🏆 Топ действий в этом чате:"]
    for i, r in enumerate(rows, start=1):
        info_a = get_chat_info(r["actor_id"])
        info_t = get_chat_info(r["target_id"])
        name_a = display_name(info_a) if info_a else f"id{r['actor_id']}"
        name_t = display_name(info_t) if info_t else f"id{r['target_id']}"
        emoji = ACTIONS.get(r["action"], ("🎲", None))[0]
        lines.append(f"{i}. {emoji} {name_a} → {r['action']} → {name_t}: {r['c']} раз(а)")
    await update.message.reply_text("\n".join(lines))


# --- Inline mode: @botusername <text> works in ANY chat, even ones the bot
# isn't a member of (private DMs between two other people, other groups) ---

# Tracks the most recent inline query id per user, so that if they keep
# typing, only the latest keystroke actually triggers a Groq call — avoids
# hammering the API (and its rate limit) once per character.
_latest_inline_query_id: dict = {}


async def inline_translate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query
    text = query.query.strip()
    user_id = update.effective_user.id if update.effective_user else 0

    if not text:
        await query.answer(
            [
                InlineQueryResultArticle(
                    id="hint",
                    title="Напиши текст для перевода на русский",
                    description="Например: @имя_бота Hello, how are you?",
                    input_message_content=InputTextMessageContent(
                        "Напиши что-нибудь после имени бота, чтобы перевести на русский."
                    ),
                )
            ],
            cache_time=1,
            is_personal=True,
        )
        return

    # debounce: wait a beat, then bail out if a newer keystroke already
    # superseded this query (Telegram fires a new inline_query on every
    # pause in typing, not just when the person is "done")
    _latest_inline_query_id[user_id] = query.id
    await asyncio.sleep(0.6)
    if _latest_inline_query_id.get(user_id) != query.id:
        return

    try:
        translated = await asyncio.to_thread(_translate_with_groq, text)
    except Exception as e:
        logger.warning("Inline translation failed: %s", e)
        translated = None

    if not translated:
        results = [
            InlineQueryResultArticle(
                id="error",
                title="Не получилось перевести — попробуй ещё раз",
                description=text[:80],
                input_message_content=InputTextMessageContent(text),
            )
        ]
    else:
        results = [
            InlineQueryResultArticle(
                id="translation",
                title="🌐 Отправить перевод",
                description=translated[:100],
                input_message_content=InputTextMessageContent(translated),
            )
        ]
    await query.answer(results, cache_time=1, is_personal=True)


async def check_lesson_reminders(context: ContextTypes.DEFAULT_TYPE):
    """Runs every minute: for each chat, finds any lesson today whose start
    time is exactly LESSON_REMINDER_MINUTES from now, and sends a heads-up.
    Minute-granularity matching means each lesson fires once, at the minute
    that lines up — no separate dedupe bookkeeping needed."""
    now = now_kz()
    weekday = now.weekday()
    target_time = (now + timedelta(minutes=LESSON_REMINDER_MINUTES)).strftime("%H:%M")

    for chat_id in all_chat_ids():
        rows = get_lessons(chat_id, weekday)
        for r in rows:
            if r["time"] != target_time:
                continue
            text = (
                f"⏰ Через {LESSON_REMINDER_MINUTES} минут: "
                f"[{SUBJECT_NAME[r['subject']]}] в {r['time']}, каб. {r['room']}"
            )
            try:
                await context.bot.send_message(chat_id=chat_id, text=text)
            except Exception as e:
                logger.warning("Could not send lesson reminder to chat %s: %s", chat_id, e)
                continue

            photo = get_room_photo(chat_id, r["room"])
            if not photo:
                continue
            file_id, kind = photo
            try:
                caption = f"📍 Кабинет {r['room']}"
                if kind == "document":
                    await context.bot.send_document(chat_id=chat_id, document=file_id, caption=caption)
                else:
                    await context.bot.send_photo(chat_id=chat_id, photo=file_id, caption=caption)
            except Exception as e:
                logger.warning("Could not send room photo reminder to chat %s: %s", chat_id, e)


def main():
    if not BOT_TOKEN:
        raise SystemExit(
            "BOT_TOKEN is not set. Set it as an environment variable "
            "(locally: export BOT_TOKEN=...; on Render: Environment tab)."
        )
    init_db()
    start_health_check_server()
    app = Application.builder().token(BOT_TOKEN).build()

    add_conv = ConversationHandler(
        entry_points=[CommandHandler("add", add_start)],
        states={
            CHOOSING_SUBJECT: [CallbackQueryHandler(add_subject_chosen, pattern="^addsub:")],
            TYPING_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_title_typed)],
            TYPING_DESCRIPTION: [
                CallbackQueryHandler(add_description_skip, pattern="^nodesc$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_description_typed),
            ],
            TYPING_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_date_typed)],
            TYPING_TIME: [
                CallbackQueryHandler(add_time_skip, pattern="^notime$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_time_typed),
            ],
            TYPING_ATTACHMENT: [
                CallbackQueryHandler(add_attachment_skip, pattern="^noattach$"),
                MessageHandler(filters.PHOTO, add_attachment_photo),
                MessageHandler(filters.Document.IMAGE | filters.Document.PDF, add_attachment_document),
                MessageHandler(~filters.COMMAND, add_attachment_invalid),
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

    edittask_conv = ConversationHandler(
        entry_points=[CommandHandler("edittask", edittask_start)],
        states={
            EDIT_TASK_PICK: [CallbackQueryHandler(edittask_picked, pattern="^edittask:")],
            EDIT_TASK_FIELD: [CallbackQueryHandler(edittask_field_chosen, pattern="^editfield:")],
            EDIT_TASK_SUBJECT: [CallbackQueryHandler(edittask_subject_chosen, pattern="^edittasksub:")],
            EDIT_TASK_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edittask_title_typed)],
            EDIT_TASK_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edittask_date_typed)],
            EDIT_TASK_TIME: [
                CallbackQueryHandler(edittask_time_skip, pattern="^edittasktime:none$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, edittask_time_typed),
            ],
            EDIT_TASK_DESCRIPTION: [
                CallbackQueryHandler(edittask_description_clear, pattern="^edittaskdesc:none$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, edittask_description_typed),
            ],
            EDIT_TASK_ATTACHMENT: [
                CallbackQueryHandler(edittask_attachment_clear, pattern="^edittaskattach:none$"),
                MessageHandler(filters.PHOTO, edittask_attachment_photo),
                MessageHandler(filters.Document.IMAGE | filters.Document.PDF, edittask_attachment_document),
                MessageHandler(~filters.COMMAND, edittask_attachment_invalid),
            ],
        },
        fallbacks=[CommandHandler("cancel", add_cancel)],
    )

    editschedule_conv = ConversationHandler(
        entry_points=[CommandHandler("editschedule", editschedule_start)],
        states={
            EDIT_LESSON_PICK: [CallbackQueryHandler(editschedule_picked, pattern="^editlesson:")],
            EDIT_LESSON_FIELD: [CallbackQueryHandler(editschedule_field_chosen, pattern="^editlfield:")],
            EDIT_LESSON_WEEKDAY: [CallbackQueryHandler(editschedule_weekday_chosen, pattern="^editlwd:")],
            EDIT_LESSON_SUBJECT: [CallbackQueryHandler(editschedule_subject_chosen, pattern="^editlsub:")],
            EDIT_LESSON_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, editschedule_time_typed)],
            EDIT_LESSON_ROOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, editschedule_room_typed)],
        },
        fallbacks=[CommandHandler("cancel", schedule_cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(add_conv)
    app.add_handler(schedule_add_conv)
    app.add_handler(addroomphoto_conv)
    app.add_handler(edittask_conv)
    app.add_handler(editschedule_conv)
    app.add_handler(CommandHandler("today", today_cmd))
    app.add_handler(CommandHandler("week", week_cmd))
    app.add_handler(CommandHandler("all", all_cmd))
    app.add_handler(CommandHandler("done", done_cmd))
    app.add_handler(CallbackQueryHandler(done_chosen, pattern="^done:"))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(CallbackQueryHandler(delete_chosen, pattern="^del:"))
    app.add_handler(CommandHandler("taskfile", taskfile_cmd))
    app.add_handler(CallbackQueryHandler(taskfile_chosen, pattern="^taskfile:"))
    app.add_handler(CallbackQueryHandler(taskdesc_chosen, pattern="^taskdesc:"))
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

    if TRANSCRIBE_ENABLED and requests is not None:
        app.add_handler(
            MessageHandler(
                filters.VOICE | filters.AUDIO | filters.VIDEO_NOTE | filters.VIDEO,
                handle_transcribe,
            )
        )
        logger.info("Voice/audio/video transcription enabled (Groq).")
    elif GROQ_API_KEY and requests is None:
        logger.warning(
            "GROQ_API_KEY is set but the 'requests' package isn't installed — "
            "transcription is disabled. Run: pip install -r requirements.txt"
        )

    # always registered: fun reply-actions work with no external API; the
    # same handler also does reply-to-translate when GROQ_API_KEY is set
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.REPLY & ~filters.COMMAND,
            handle_translate_reply,
        )
    )
    app.add_handler(CommandHandler("topactions", topactions_cmd))
    app.add_handler(CommandHandler("emojiid", emojiid_cmd))
    app.add_handler(MessageHandler(filters.Entity("custom_emoji"), emojiid_capture))
    logger.info("Fun reply-actions enabled (%d actions).", len(ACTIONS))

    if TRANSLATE_ENABLED and requests is not None:
        app.add_handler(InlineQueryHandler(inline_translate))
        logger.info("Reply-to-translate and inline translation enabled (Groq).")

    app.job_queue.run_daily(
        send_daily_reminders,
        time=dtime(hour=REMINDER_HOUR, minute=REMINDER_MINUTE, tzinfo=TIMEZONE),
    )
    app.job_queue.run_daily(
        send_morning_schedule,
        time=dtime(hour=SCHEDULE_HOUR, minute=SCHEDULE_MINUTE, tzinfo=TIMEZONE),
    )
    # checked once a minute so a "10 minutes before" reminder can fire at
    # the right minute for any lesson, any day
    app.job_queue.run_repeating(check_lesson_reminders, interval=60, first=5)

    logger.info("Bot starting (polling)...")
    app.run_polling()


if __name__ == "__main__":
    main()
