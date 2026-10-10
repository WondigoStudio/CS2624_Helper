"""SQLite/Postgres storage layer: connection helper, schema creation and
migrations, and every read/write query used by the bot."""

import calendar
import sqlite3
import threading
import time
from datetime import date

from telegram import Update

from .config import (
    DATABASE_URL,
    DATABASE_URL_BACKUP2,
    DATABASE_URL_BACKUP3,
    DB_PATH,
    USE_POSTGRES,
    logger,
    psycopg2,
)
from .utils import is_task_overdue, now_kz

# --- Multi-database failover --------------------------------------------
# Postgres only. _DB_URLS is the priority order: the primary (DATABASE_URL)
# first, then whichever backups are configured. db() always tries the
# currently "active" one first; if it can't connect, it tries the next one
# down the list and — if that works — sticks with it as the new active DB
# until something explicitly switches back (see get_db_status below: there
# is no automatic switch-back, because data written during an outage lives
# only on the failover DB and would need a conscious merge before it's safe
# to call the original primary authoritative again).
_DB_URLS = [u for u in (DATABASE_URL, DATABASE_URL_BACKUP2, DATABASE_URL_BACKUP3) if u]
_active_db_index = 0


def _connect_pg(url: str):
    return psycopg2.connect(
        url,
        cursor_factory=psycopg2.extras.RealDictCursor,
        connect_timeout=5,
        # notice a connection the host silently dropped (Neon/Render idle
        # timeouts) instead of hanging on it
        keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3,
    )


def _connect_with_failover():
    """(connection, index of the database it belongs to)."""
    global _active_db_index
    last_err = None
    for offset in range(len(_DB_URLS)):
        idx = (_active_db_index + offset) % len(_DB_URLS)
        try:
            conn = _connect_pg(_DB_URLS[idx])
        except Exception as e:
            last_err = e
            logger.warning("Database #%d unreachable, trying next: %s", idx, e)
            continue
        if idx != _active_db_index:
            logger.error(
                "Database failover: switched from #%d to #%d — check what took #%d down "
                "and merge any data written here back before relying on it again.",
                _active_db_index, idx, _active_db_index,
            )
            _active_db_index = idx
        return conn, idx
    raise last_err


# --- Connection pool ------------------------------------------------------
# Opening a Postgres connection (TCP + TLS + auth) to a remote host costs
# 100-300 ms; the bot used to do it for every single query. Idle connections
# are now kept and reused. A connection that the server closed in the
# meantime is detected on first use and replaced transparently.
_POOL_MAX = 8
_pool = []  # [(connection, db_index)]
_pool_lock = threading.Lock()


def _pool_take():
    with _pool_lock:
        while _pool:
            conn, idx = _pool.pop()
            if idx == _active_db_index and not conn.closed:
                return conn, idx
            try:
                conn.close()
            except Exception:
                pass
    return None


def _pool_give(conn, idx):
    try:
        if conn.closed or idx != _active_db_index:
            raise RuntimeError("stale")
        conn.rollback()  # never park a connection inside an open transaction
        with _pool_lock:
            if len(_pool) < _POOL_MAX:
                _pool.append((conn, idx))
                return
    except Exception:
        pass
    try:
        conn.close()
    except Exception:
        pass


def get_db_status() -> dict:
    """For an admin /dbstatus command: which DB is currently active and how
    many are configured as failover targets."""
    return {
        "using_postgres": USE_POSTGRES,
        "configured_count": len(_DB_URLS),
        "active_index": _active_db_index,
    }


def db():
    if USE_POSTGRES:
        taken = _pool_take()
        if taken is None:
            taken = _connect_with_failover()
        return _PGConn(*taken)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


class _PGConn:
    """Thin wrapper so the rest of the code (written for sqlite3) can use a
    Postgres connection unchanged: '?' placeholders are translated to '%s',
    and RealDictCursor rows already support row["col"] like sqlite3.Row.
    close() hands the connection back to the pool instead of closing it."""

    def __init__(self, conn, idx=0):
        self._conn = conn
        self._idx = idx
        self._statements = 0  # in the current transaction

    def execute(self, sql, params=()):
        sql = sql.replace("?", "%s")
        for attempt in (0, 1):
            try:
                cur = self._conn.cursor()
                cur.execute(sql, params)
                self._statements += 1
                return cur
            except (psycopg2.OperationalError, psycopg2.InterfaceError):
                # the server dropped an idle pooled connection: replace it and
                # retry — but only if nothing was done on it in this transaction
                if attempt or self._statements:
                    raise
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn, self._idx = _connect_with_failover()

    def commit(self):
        self._conn.commit()
        self._statements = 0

    def close(self):
        _pool_give(self._conn, self._idx)


def _existing_columns(conn, table: str) -> set:
    """PRAGMA table_info doesn't exist in Postgres; this works on both."""
    if USE_POSTGRES:
        cur = conn.execute(
            "SELECT column_name AS name FROM information_schema.columns WHERE table_name = ?",
            (table,),
        )
        return {r["name"] for r in cur.fetchall()}
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def init_db(target_url: str = None):
    """With target_url, creates/migrates the schema on that specific
    Postgres database directly (bypassing the active/failover DB) — used to
    make sure a backup database is ready to receive mirrored data before
    the first backup job runs against it."""
    if target_url is not None:
        conn = _PGConn(_connect_pg(target_url), -1)
    else:
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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_perms (
            user_id BIGINT NOT NULL,
            perm TEXT NOT NULL,
            PRIMARY KEY (user_id, perm)
        )
        """
    )
    # One-time: people who had the old single "allowed" flag get all three rights.
    if not conn.execute("SELECT 1 FROM user_perms WHERE user_id = 0 AND perm = '_migrated'").fetchone():
        for old in conn.execute("SELECT user_id FROM schedule_allowed_users").fetchall():
            for perm in ("tasks", "schedule", "lms"):
                conn.execute(
                    "INSERT INTO user_perms (user_id, perm) VALUES (?, ?) ON CONFLICT(user_id, perm) DO NOTHING",
                    (old["user_id"], perm),
                )
        conn.execute("INSERT INTO user_perms (user_id, perm) VALUES (0, '_migrated')")
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
        CREATE TABLE IF NOT EXISTS task_attachments (
            id {id_pk},
            task_id INTEGER NOT NULL,
            file_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    # Which LMS calendar events have already been turned into tasks (see
    # lms_sync.py). Kept even after the task is deleted, so a deleted task
    # isn't re-imported on the next sync. signature = what the task looked
    # like when last synced, to notice when the LMS changes the event.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS lms_synced (
            uid TEXT PRIMARY KEY,
            task_id INTEGER NOT NULL,
            signature TEXT NOT NULL
        )
        """
    )
    # Each person's own LMS calendar link (contains a personal token). One row
    # per person; see lms_sync.py / handlers/lms.py.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS lms_feeds (
            user_id BIGINT PRIMARY KEY,
            url TEXT NOT NULL,
            added_at TEXT NOT NULL,
            last_sync TEXT,
            error_notified INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS chat_threads (
            chat_id BIGINT PRIMARY KEY,
            thread_id BIGINT NOT NULL
        )
        """
    )
    # Per-person "done" marks: the task list is shared, but whether YOU have
    # finished a task is yours alone. (tasks.done = 1 is the legacy global
    # flag from before this table existed — still honored, so old finished
    # tasks stay finished for everyone.)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS task_done (
            task_id INTEGER NOT NULL,
            user_id BIGINT NOT NULL,
            done_at TEXT NOT NULL,
            PRIMARY KEY (task_id, user_id)
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
    if "description_html" not in existing_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN description_html INTEGER NOT NULL DEFAULT 0")
    if "deadline_notified" not in existing_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN deadline_notified INTEGER NOT NULL DEFAULT 0")
    # One-time migration: fold each task's old single attachment_file_id
    # (from before multiple attachments were supported) into the new
    # task_attachments table, so nothing already saved gets lost. Guarded by
    # an existence check so it's a no-op on every later startup.
    for old in conn.execute(
        "SELECT id, attachment_file_id, attachment_kind, created_at FROM tasks "
        "WHERE attachment_file_id IS NOT NULL"
    ).fetchall():
        already = conn.execute(
            "SELECT 1 FROM task_attachments WHERE task_id = ?", (old["id"],)
        ).fetchone()
        if already:
            continue
        conn.execute(
            "INSERT INTO task_attachments (task_id, file_id, kind, created_at) "
            "VALUES (?, ?, ?, ?)",
            (old["id"], old["attachment_file_id"], old["attachment_kind"] or "photo",
             old["created_at"]),
        )
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
    for ddl in (
        "CREATE INDEX IF NOT EXISTS idx_tasks_chat_due ON tasks (chat_id, due_date)",
        "CREATE INDEX IF NOT EXISTS idx_task_done_user ON task_done (user_id)",
        "CREATE INDEX IF NOT EXISTS idx_schedule_chat_wd ON schedule (chat_id, weekday)",
        "CREATE INDEX IF NOT EXISTS idx_schedule_wd_time ON schedule (weekday, time)",
    ):
        conn.execute(ddl)
    conn.commit()
    conn.close()

_chat_seen = {}  # chat_id -> (signature, monotonic time of the last write)
_CHAT_REFRESH_SECONDS = 300


def register_chat(update: Update):
    chat_id = update.effective_chat.id
    chat_type = update.effective_chat.type  # "private", "group", "supergroup", ...
    user = update.effective_user
    username = user.username if user else None
    first_name = user.first_name if user else None
    last_name = user.last_name if user else None
    # Almost every command calls this. Writing the same row to the database on
    # each message is pure overhead, so repeat writes within a few minutes are skipped
    # (a changed name or chat type is written immediately).
    signature = (username, first_name, last_name, chat_type)
    seen = _chat_seen.get(chat_id)
    if seen and seen[0] == signature and time.monotonic() - seen[1] < _CHAT_REFRESH_SECONDS:
        return
    _chat_seen[chat_id] = (signature, time.monotonic())
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
             created_by: str = None, description: str = None, description_html: bool = False) -> int:
    """Returns the new task's id, so the caller can attach files to it via
    add_task_attachment right after creation (see 'Task attachments'
    below — a task can now have any number of attachments, not just one)."""
    conn = db()
    cur = conn.execute(
        "INSERT INTO tasks (chat_id, subject, title, due_date, due_time, created_at, "
        "created_by, description, description_html) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
        (chat_id, subject, title, due_date, due_time, now_kz().isoformat(),
         created_by, description, 1 if description_html else 0),
    )
    row = cur.fetchone()
    task_id = row["id"] if row else None
    conn.commit()
    conn.close()
    return task_id


def update_task_description(task_id: int, description: str, description_html: bool = False):
    conn = db()
    conn.execute(
        "UPDATE tasks SET description = ?, description_html = ? WHERE id = ?",
        (description, 1 if description_html else 0, task_id),
    )
    conn.commit()
    conn.close()


def viewer_for_chat(chat_id: int):
    """Private chats have a positive chat_id equal to the person's user id,
    so "done" marks can be applied for them. Groups (negative ids) have no
    single person behind them — they only see the legacy global flag."""
    return chat_id if chat_id and chat_id > 0 else None


def get_tasks(chat_id: int, only_undone=True, start=None, end=None, viewer_id=None):
    """viewer_id: whose personal "done" marks to apply. Rows come back with
    my_done = 1 if the task is finished for that viewer (their own mark, or
    the legacy global flag); with only_undone=True those rows are left out."""
    conn = db()
    vid = viewer_id if viewer_id is not None else -1  # -1 never matches a real user
    q = (
        "SELECT tasks.*, CASE WHEN tasks.done = 1 OR EXISTS ("
        "SELECT 1 FROM task_done td WHERE td.task_id = tasks.id AND td.user_id = ?"
        ") THEN 1 ELSE 0 END AS my_done FROM tasks WHERE chat_id = ?"
    )
    params = [vid, chat_id]
    if only_undone:
        q += (
            " AND done = 0 AND NOT EXISTS ("
            "SELECT 1 FROM task_done td WHERE td.task_id = tasks.id AND td.user_id = ?)"
        )
        params.append(vid)
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


def mark_done(task_id: int, user_id: int):
    """Marks the task finished for this one person only."""
    conn = db()
    conn.execute(
        "INSERT INTO task_done (task_id, user_id, done_at) VALUES (?, ?, ?) "
        "ON CONFLICT(task_id, user_id) DO NOTHING",
        (task_id, user_id, now_kz().isoformat()),
    )
    conn.commit()
    conn.close()


def unmark_done(task_id: int, user_id: int):
    conn = db()
    conn.execute("DELETE FROM task_done WHERE task_id = ? AND user_id = ?", (task_id, user_id))
    conn.commit()
    conn.close()


def done_users_for(task_ids) -> dict:
    """{task_id: {user ids who finished it}} for many tasks in one query."""
    ids = [int(t) for t in task_ids]
    if not ids:
        return {}
    conn = db()
    rows = conn.execute(
        f"SELECT task_id, user_id FROM task_done WHERE task_id IN ({','.join('?' * len(ids))})", ids
    ).fetchall()
    conn.close()
    out = {}
    for r in rows:
        out.setdefault(r["task_id"], set()).add(r["user_id"])
    return out


def users_who_finished(task_id: int) -> set:
    conn = db()
    rows = conn.execute("SELECT user_id FROM task_done WHERE task_id = ?", (task_id,)).fetchall()
    conn.close()
    return {r["user_id"] for r in rows}


def delete_task(task_id: int):
    conn = db()
    conn.execute("DELETE FROM task_done WHERE task_id = ?", (task_id,))
    conn.execute("DELETE FROM task_attachments WHERE task_id = ?", (task_id,))
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


# --- Task attachments ---------------------------------------------------
# A task can have any number of attachments (photos/files), added one at a
# time via /add or /edittask. The old single attachment_file_id/kind columns
# on `tasks` are kept only so existing rows aren't broken — init_db() folds
# any of those into this table on startup (see the migration above) and
# nothing new is written there.

def add_task_attachment(task_id: int, file_id: str, kind: str):
    conn = db()
    conn.execute(
        "INSERT INTO task_attachments (task_id, file_id, kind, created_at) VALUES (?, ?, ?, ?)",
        (task_id, file_id, kind, now_kz().isoformat()),
    )
    conn.commit()
    conn.close()


def get_task_attachments(task_id: int):
    conn = db()
    rows = conn.execute(
        "SELECT * FROM task_attachments WHERE task_id = ? ORDER BY id ASC", (task_id,)
    ).fetchall()
    conn.close()
    return rows


def clear_task_attachments(task_id: int):
    conn = db()
    conn.execute("DELETE FROM task_attachments WHERE task_id = ?", (task_id,))
    conn.commit()
    conn.close()


def mark_task_deadline_notified(task_id: int):
    conn = db()
    conn.execute("UPDATE tasks SET deadline_notified = 1 WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()


def save_lms_feed(user_id: int, url: str):
    conn = db()
    conn.execute(
        "INSERT INTO lms_feeds (user_id, url, added_at, last_sync, error_notified) VALUES (?, ?, ?, ?, 0) "
        "ON CONFLICT(user_id) DO UPDATE SET url = excluded.url, error_notified = 0",
        (user_id, url, now_kz().isoformat(), now_kz().isoformat()),
    )
    conn.commit()
    conn.close()


def get_lms_feed(user_id: int):
    conn = db()
    row = conn.execute("SELECT * FROM lms_feeds WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row


def list_lms_feeds():
    conn = db()
    rows = conn.execute("SELECT * FROM lms_feeds").fetchall()
    conn.close()
    return rows


def delete_lms_feed(user_id: int) -> bool:
    conn = db()
    cur = conn.execute("DELETE FROM lms_feeds WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    return bool(cur.rowcount)


def set_lms_feed_result(user_id: int, ok: bool, error_notified: bool = None):
    """After a sync attempt: stamp last_sync on success; error_notified
    remembers that the person was already told the link stopped working, so
    they get that message once, not every 10 minutes."""
    conn = db()
    if ok:
        conn.execute(
            "UPDATE lms_feeds SET last_sync = ?, error_notified = 0 WHERE user_id = ?",
            (now_kz().isoformat(), user_id),
        )
    elif error_notified is not None:
        conn.execute(
            "UPDATE lms_feeds SET error_notified = ? WHERE user_id = ?",
            (1 if error_notified else 0, user_id),
        )
    conn.commit()
    conn.close()


def find_duplicate_task(subject: str, title: str, due_date: str, due_time):
    """An existing shared task that is the same assignment (same subject,
    title, date and time, ignoring case) — used so the same LMS deadline
    arriving from two people's feeds becomes ONE task."""
    conn = db()
    rows = conn.execute(
        "SELECT * FROM tasks WHERE chat_id = 0 AND subject = ? AND due_date = ?",
        (subject, due_date),
    ).fetchall()
    conn.close()
    want = (title.strip().lower(), due_time or "")
    for r in rows:
        if (r["title"].strip().lower(), r["due_time"] or "") == want:
            return r
    return None


def get_lms_link(uid: str):
    conn = db()
    row = conn.execute("SELECT * FROM lms_synced WHERE uid = ?", (uid,)).fetchone()
    conn.close()
    return row


def set_lms_link(uid: str, task_id: int, signature: str):
    conn = db()
    conn.execute(
        "INSERT INTO lms_synced (uid, task_id, signature) VALUES (?, ?, ?) "
        "ON CONFLICT(uid) DO UPDATE SET task_id = excluded.task_id, signature = excluded.signature",
        (uid, task_id, signature),
    )
    conn.commit()
    conn.close()


def reset_task_deadline_notified(task_id: int):
    """Called when a task's due date/time is changed, so the 1-hour heads-up
    can fire again for the new deadline."""
    conn = db()
    conn.execute("UPDATE tasks SET deadline_notified = 0 WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()


def get_task(task_id: int):
    conn = db()
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    conn.close()
    return row


def get_task_dates_in_month(chat_id: int, year: int, month: int, viewer_id=None):
    start = date(year, month, 1).isoformat()
    last_day = calendar.monthrange(year, month)[1]
    end = date(year, month, last_day).isoformat()
    rows = get_tasks(chat_id, only_undone=True, start=start, end=end, viewer_id=viewer_id)
    return {r["due_date"] for r in rows}


def get_overdue_task_dates_in_month(chat_id: int, year: int, month: int, viewer_id=None):
    """Due dates in this month that still have an undone task whose
    deadline has already passed (see is_task_overdue) — used to fade those
    calendar days out instead of tagging them as 'просрочено' in text."""
    start = date(year, month, 1).isoformat()
    last_day = calendar.monthrange(year, month)[1]
    end = date(year, month, last_day).isoformat()
    rows = get_tasks(chat_id, only_undone=True, start=start, end=end, viewer_id=viewer_id)
    return {r["due_date"] for r in rows if is_task_overdue(r)}


def all_chat_ids():
    conn = db()
    rows = conn.execute("SELECT chat_id FROM chats").fetchall()
    conn.close()
    return [r["chat_id"] for r in rows]


def first_lesson_times(weekday: int) -> dict:
    """{chat_id: earliest lesson time that weekday} — one query for everyone,
    instead of one per chat."""
    conn = db()
    rows = conn.execute(
        "SELECT chat_id, MIN(time) AS first_time FROM schedule WHERE weekday = ? GROUP BY chat_id",
        (weekday,),
    ).fetchall()
    conn.close()
    return {r["chat_id"]: r["first_time"] for r in rows}


def lessons_starting_at(weekday: int, time_str: str):
    """Every lesson (all chats) that starts exactly at time_str that weekday."""
    conn = db()
    rows = conn.execute(
        "SELECT * FROM schedule WHERE weekday = ? AND time = ?", (weekday, time_str)
    ).fetchall()
    conn.close()
    return rows


def get_chat_thread(chat_id: int):
    """Topic (message_thread_id) of a forum supergroup where the bot's daily
    messages go, or None for the default (General / ordinary chat)."""
    conn = db()
    row = conn.execute("SELECT thread_id FROM chat_threads WHERE chat_id = ?", (chat_id,)).fetchone()
    conn.close()
    return row["thread_id"] if row else None


def set_chat_thread(chat_id: int, thread_id):
    conn = db()
    if thread_id is None:
        conn.execute("DELETE FROM chat_threads WHERE chat_id = ?", (chat_id,))
    else:
        conn.execute(
            "INSERT INTO chat_threads (chat_id, thread_id) VALUES (?, ?) "
            "ON CONFLICT(chat_id) DO UPDATE SET thread_id = excluded.thread_id",
            (chat_id, thread_id),
        )
    conn.commit()
    conn.close()


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


def get_birthdays():
    """Birthdays are shared across everyone, like tasks — not scoped to the
    chat they were added from (chat_id is kept only as a record of where
    each one was added, e.g. for /addbirthday's "known users" picker)."""
    conn = db()
    rows = conn.execute("SELECT * FROM birthdays ORDER BY month ASC, day ASC").fetchall()
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


# --- Multi-database backup (mirroring) ----------------------------------
# Copies every row of every table from the currently active DB into one of
# the configured backup DBs, overwriting whatever that backup already has.
# Postgres-to-Postgres only. Called from jobs.py on a schedule (every 6h
# into the first backup, once a night into the second) — never run this
# against SQLite or against DATABASE_URL itself as the target.
_MIRROR_TABLES = [
    "tasks", "task_attachments", "task_done", "lms_synced", "lms_feeds", "chats", "schedule", "room_photos", "actions",
    "schedule_allowed_users", "user_perms", "report_settings", "group_members", "report_chats",
    "reminders", "birthdays", "chat_threads",
]


def export_all_tables() -> dict:
    """{table: [row dicts]} for every table, for a downloadable backup.
    LMS calendar links hold personal access tokens, so they are left out."""
    out = {}
    conn = db()
    try:
        for table in _MIRROR_TABLES:
            try:
                rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            except Exception:
                continue
            items = [dict(r) for r in rows]
            if table == "lms_feeds":
                for it in items:
                    it["url"] = "<скрыто>"
            out[table] = items
    finally:
        conn.close()
    return out


def mirror_active_db_to(target_url: str) -> bool:
    if not USE_POSTGRES or not target_url:
        return False
    src = _connect_pg(_DB_URLS[_active_db_index])
    dst = _connect_pg(target_url)
    try:
        dst_cur = dst.cursor()
        for table in _MIRROR_TABLES:
            src_cur = src.cursor()
            try:
                src_cur.execute(f"SELECT * FROM {table}")
            except Exception:
                # table doesn't exist on the source (older schema) — skip it
                continue
            rows = src_cur.fetchall()
            dst_cur.execute(f"DELETE FROM {table}")
            if rows:
                columns = list(rows[0].keys())
                col_list = ", ".join(columns)
                placeholders = ", ".join(["%s"] * len(columns))
                values = [[r[c] for c in columns] for r in rows]
                psycopg2.extras.execute_batch(
                    dst_cur, f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})", values,
                )
        dst.commit()
        return True
    finally:
        src.close()
        dst.close()
