"""SQLite/Postgres storage layer: connection helper, schema creation and
migrations, and every read/write query used by the bot."""

import calendar
import sqlite3
from datetime import date

from telegram import Update

from .config import DATABASE_URL, DB_PATH, USE_POSTGRES, psycopg2
from .utils import is_task_overdue, now_kz


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
    # Таблица разрешенных пользователей для расписания
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schedule_allowed_users (
            user_id BIGINT PRIMARY KEY,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute("""
        CREATE TABLE IF NOT EXISTS report_settings (
            chat_id BIGINT PRIMARY KEY,
            enabled BOOLEAN DEFAULT TRUE,
            updated_at TEXT
        )
    """)
    
    # Создание таблицы участников группы
    conn.execute("""
        CREATE TABLE IF NOT EXISTS group_members (
            chat_id BIGINT,
            user_id BIGINT,
            first_name TEXT,
            updated_at TEXT,
            PRIMARY KEY (chat_id, user_id)
        )
    """)
    # Таблица чатов с включенным утренним опросом
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS report_chats (
            chat_id BIGINT PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS reminders (
            id {id_pk},
            chat_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            text TEXT NOT NULL,
            repeat TEXT NOT NULL DEFAULT 'once',
            weekday INTEGER,
            remind_date TEXT,
            time TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            last_sent_date TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS birthdays (
            id {id_pk},
            user_id BIGINT NOT NULL,
            chat_id BIGINT NOT NULL,
            display_name TEXT NOT NULL,
            day INTEGER NOT NULL,
            month INTEGER NOT NULL,
            year INTEGER,
            added_by BIGINT NOT NULL,
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
    reminder_cols = _existing_columns(conn, "reminders")
    if "awaiting_confirmation" not in reminder_cols:
        conn.execute("ALTER TABLE reminders ADD COLUMN awaiting_confirmation INTEGER NOT NULL DEFAULT 0")
    if "last_nag_at" not in reminder_cols:
        conn.execute("ALTER TABLE reminders ADD COLUMN last_nag_at TEXT")
    room_cols = _existing_columns(conn, "room_photos")
    if "kind" not in room_cols:
        conn.execute("ALTER TABLE room_photos ADD COLUMN kind TEXT NOT NULL DEFAULT 'photo'")
    chat_cols = _existing_columns(conn, "chats")
    for col in ("username", "first_name", "last_name", "updated_at", "chat_type"):
        if col not in chat_cols:
            conn.execute(f"ALTER TABLE chats ADD COLUMN {col} TEXT")
    if USE_POSTGRES:
        # Widen chat_id to BIGINT for anyone whose database was created by
        # an earlier version of this schema that used plain INTEGER —
        # modern Telegram user/chat ids commonly exceed the 32-bit range.
        # Safe/no-op if the column is already BIGINT.
        for table in ("chats", "tasks", "schedule", "room_photos", "reminders", "birthdays"):
            conn.execute(f"ALTER TABLE {table} ALTER COLUMN chat_id TYPE BIGINT")
    conn.commit()
    conn.close()

def register_chat(update: Update):
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type  # "private", "group", "supergroup", ...
    user = update.effective_user
    username = user.username if user else None
    first_name = user.first_name if user else None
    last_name = user.last_name if user else None
    conn = db()
    conn.execute(
        """
        INSERT INTO chats (chat_id, username, first_name, last_name, updated_at, chat_type)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(chat_id) DO UPDATE SET
            username = excluded.username,
            first_name = excluded.first_name,
            last_name = excluded.last_name,
            updated_at = excluded.updated_at,
            chat_type = excluded.chat_type
        """,
        (chat_id, username, first_name, last_name, now_kz().isoformat(), chat_type),
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


def get_overdue_task_dates_in_month(chat_id: int, year: int, month: int):
    """Due dates in this month that still have an undone task whose
    deadline has already passed (see is_task_overdue) — used to fade those
    calendar days out instead of tagging them as 'просрочено' in text."""
    start = date(year, month, 1).isoformat()
    last_day = calendar.monthrange(year, month)[1]
    end = date(year, month, last_day).isoformat()
    rows = get_tasks(chat_id, only_undone=True, start=start, end=end)
    return {r["due_date"] for r in rows if is_task_overdue(r)}


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


def get_chat_info(chat_id: int):
    conn = db()
    row = conn.execute("SELECT * FROM chats WHERE chat_id = ?", (chat_id,)).fetchone()
    conn.close()
    return row


# --- Personal reminders ----------------------------------------------------
# Unlike tasks/schedule (shared across everyone), each reminder belongs to
# whoever created it (user_id) and fires in the chat it was created in
# (chat_id) — a private chat for a reminder to yourself, or a group chat if
# someone wants the whole group pinged.

def add_reminder(chat_id: int, user_id: int, text: str, repeat: str, time_str: str,
                  remind_date: str = None, weekday: int = None):
    conn = db()
    conn.execute(
        "INSERT INTO reminders (chat_id, user_id, text, repeat, weekday, remind_date, "
        "time, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (chat_id, user_id, text, repeat, weekday, remind_date, time_str, now_kz().isoformat()),
    )
    conn.commit()
    conn.close()


def get_reminders_for_user(user_id: int):
    conn = db()
    rows = conn.execute(
        "SELECT * FROM reminders WHERE user_id = ? AND enabled = 1 "
        "ORDER BY COALESCE(remind_date, '9999-99-99') ASC, time ASC",
        (user_id,),
    ).fetchall()
    conn.close()
    return rows


def get_all_enabled_reminders():
    conn = db()
    rows = conn.execute("SELECT * FROM reminders WHERE enabled = 1").fetchall()
    conn.close()
    return rows


def get_reminder(reminder_id: int):
    conn = db()
    row = conn.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,)).fetchone()
    conn.close()
    return row


def delete_reminder(reminder_id: int):
    conn = db()
    conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
    conn.commit()
    conn.close()


def disable_reminder(reminder_id: int):
    """Used for a one-time reminder right after it fires — kept in the table
    (rather than deleted) just long enough that a "snooze" tap on its button
    can still look up its text, but it no longer shows in /reminders or
    matches in the once-a-minute job check."""
    conn = db()
    conn.execute("UPDATE reminders SET enabled = 0 WHERE id = ?", (reminder_id,))
    conn.commit()
    conn.close()


def mark_reminder_sent(reminder_id: int, date_iso: str):
    """For a recurring (daily/weekly) reminder: records the calendar date it
    last fired on, so the once-a-minute job doesn't send it again within the
    same matching minute or on a restart that re-scans the same minute."""
    conn = db()
    conn.execute("UPDATE reminders SET last_sent_date = ? WHERE id = ?", (date_iso, reminder_id))
    conn.commit()
    conn.close()


def mark_reminder_fired(reminder_id: int, date_iso: str, sent_at_iso: str):
    """Called right when a reminder's main message goes out: records the
    date (so once/daily/weekly all correctly skip re-firing until their next
    due occurrence — see check_reminders) and starts the confirmation-nag
    cycle by setting awaiting_confirmation and the nag clock."""
    conn = db()
    conn.execute(
        "UPDATE reminders SET last_sent_date = ?, awaiting_confirmation = 1, last_nag_at = ? WHERE id = ?",
        (date_iso, sent_at_iso, reminder_id),
    )
    conn.commit()
    conn.close()


def bump_reminder_nag(reminder_id: int, nag_at_iso: str):
    """Resets the 5-minute nag clock after a follow-up nag message is sent
    (or after a snooze, so the postponed reminder doesn't ALSO keep
    nagging about the original occurrence in the meantime)."""
    conn = db()
    conn.execute("UPDATE reminders SET last_nag_at = ? WHERE id = ?", (nag_at_iso, reminder_id))
    conn.commit()
    conn.close()


def confirm_reminder(reminder_id: int):
    """Stops the nag cycle — the person tapped "✅ Подтверждаю" (or
    snoozed, which counts as handling this occurrence)."""
    conn = db()
    conn.execute("UPDATE reminders SET awaiting_confirmation = 0 WHERE id = ?", (reminder_id,))
    conn.commit()
    conn.close()


def get_reminders_awaiting_confirmation():
    conn = db()
    rows = conn.execute(
        "SELECT * FROM reminders WHERE enabled = 1 AND awaiting_confirmation = 1"
    ).fetchall()
    conn.close()
    return rows


# --- Birthdays ---------------------------------------------------------
# One row per person whose birthday is tracked, scoped to the chat it was
# added in (a group's birthdays list is separate from a private chat's).
# display_name is a snapshot taken at add-time — the target may never have
# a row of their own in `chats` (e.g. someone only ever mentioned, not the
# one who pressed /start), so we can't rely on joining against chats.

def add_birthday(user_id: int, chat_id: int, display_name: str, day: int, month: int,
                  added_by: int, year: int = None):
    conn = db()
    conn.execute(
        "INSERT INTO birthdays (user_id, chat_id, display_name, day, month, year, "
        "added_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, chat_id, display_name, day, month, year, added_by, now_kz().isoformat()),
    )
    conn.commit()
    conn.close()


def get_birthdays(chat_id: int):
    conn = db()
    rows = conn.execute(
        "SELECT * FROM birthdays WHERE chat_id = ? ORDER BY month ASC, day ASC",
        (chat_id,),
    ).fetchall()
    conn.close()
    return rows


def get_birthday(birthday_id: int):
    conn = db()
    row = conn.execute("SELECT * FROM birthdays WHERE id = ?", (birthday_id,)).fetchone()
    conn.close()
    return row


def delete_birthday(birthday_id: int):
    conn = db()
    conn.execute("DELETE FROM birthdays WHERE id = ?", (birthday_id,))
    conn.commit()
    conn.close()
