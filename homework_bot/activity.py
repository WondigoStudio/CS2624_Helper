"""Who did what: every command and button press is written to the log (and to
the Render log line by line) and can be viewed by an admin with /activity.

Only the *action* is stored — the command name or the button's callback data —
never the text of messages. Events are buffered in memory and written to the
database in one batch about once a minute, so this adds no per-click DB load
(and lets a serverless DB sleep between bursts). Entries older than
RETENTION_DAYS are deleted."""

import asyncio
import threading
from datetime import timedelta

from telegram import Update
from telegram.ext import ContextTypes, TypeHandler

from .config import logger
from .db import insert_activity, prune_activity
from .utils import now_kz

RETENTION_DAYS = 30
_MAX_BUFFER = 2000

_buffer = []
_lock = threading.Lock()
_last_prune = None

# What a callback prefix means, for the /activity view (unknown ones are shown raw).
CALLBACK_LABELS = {
    "cal": "листает календарь", "day": "день в календаре", "noop": None,
    "done": "отметил задание", "del": "удаляет задание", "taskdesc": "описание задания",
    "etaskdesc": "описание задания (скрыто)", "taskfile": "файл задания",
    "etaskfile": "файл задания (скрыто)", "addsub": "добавляет задание",
    "notime": "добавляет задание", "nodesc": "добавляет задание", "attachdone": "добавляет задание",
    "edittask": "редактирует задание", "edittasksub": "редактирует задание",
    "edittasktime": "редактирует задание", "edittaskdesc": "редактирует задание",
    "edittaskattach": "редактирует задание", "editfield": "редактирует задание",
    "schday": "смотрит расписание", "schsub": "добавляет урок", "schwd": "добавляет урок",
    "schdel": "удаляет урок", "schtgt": "расписание: выбор человека",
    "editlesson": "редактирует урок", "editlfield": "редактирует урок", "editlsub": "редактирует урок",
    "editlwd": "редактирует урок", "edowner": "расписание: выбор владельца",
    "sdowner": "расписание: выбор владельца", "viewsch": "админ смотрит расписание",
    "cpsrc": "копирует расписание", "cptgt": "копирует расписание", "cpgo": "копирует расписание",
    "perm": "панель разрешений", "permu": "панель разрешений", "permt": "панель разрешений",
    "permall": "панель разрешений", "permback": "панель разрешений",
    "remconfirm": "подтвердил напоминание", "remsnooze": "отложил напоминание",
    "remdel": "удаляет напоминание", "remwd": "создаёт напоминание", "remwhen": "создаёт напоминание",
    "bdaydel": "удаляет день рождения", "bdayfilter": "дни рождения", "bdaytarget": "дни рождения",
    "phtgt": "фото кабинета", "syl": "смотрит силлабус", "testph": "тест фото", "ytdl": "скачивает видео",
}


def describe(kind: str, name: str) -> str:
    """Human-readable action for the /activity view."""
    if kind == "command":
        return f"/{name}"
    if kind == "button":
        prefix, _, rest = name.partition(":")
        label = CALLBACK_LABELS.get(prefix, prefix)
        if label is None:
            return ""
        return f"{label} ({name})" if rest and prefix in ("day", "cal") else label
    return {"voice": "голосовое/кружок/аудио", "video": "видео", "photo": "фото", "document": "файл"}.get(name, name)


def _classify(update: Update):
    """(kind, name) of what the update is, or None for ordinary chatter."""
    cq = update.callback_query
    if cq is not None and cq.data:
        return "button", cq.data[:64]
    msg = update.effective_message
    if msg is None:
        return None
    text = msg.text or msg.caption or ""
    if text.startswith("/"):
        cmd = text.split(None, 1)[0][1:].split("@", 1)[0].lower()
        return ("command", cmd[:40]) if cmd else None
    for attr, name in (("voice", "voice"), ("video_note", "voice"), ("audio", "voice"),
                       ("video", "video"), ("photo", "photo"), ("document", "document")):
        if getattr(msg, attr, None):
            return "media", name
    return None


async def log_activity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Runs before every handler (group=-2) and never raises."""
    try:
        user, chat = update.effective_user, update.effective_chat
        if user is None or user.is_bot:
            return
        what = _classify(update)
        if what is None:
            return
        kind, name = what
        if kind == "button" and describe(kind, name) == "":
            return  # pure UI noise (empty calendar cells)
        who = f"@{user.username}" if user.username else (user.first_name or str(user.id))
        where = (chat.title or "группа") if chat and chat.type != "private" else "личка"
        logger.info("ACTIVITY %s (%s) · %s · %s: %s", who, user.id, where, kind, name)
        row = (now_kz().isoformat(timespec="seconds"), user.id, chat.id if chat else None,
               chat.type if chat else None, (chat.title or None) if chat else None, kind, name)
        with _lock:
            if len(_buffer) < _MAX_BUFFER:
                _buffer.append(row)
    except Exception as e:  # logging must never break a command
        logger.debug("activity log skipped: %s", e)


def _drain():
    with _lock:
        rows = _buffer[:]
        del _buffer[:]
    return rows


def _flush_blocking():
    global _last_prune
    rows = _drain()
    if rows:
        try:
            insert_activity(rows)
        except Exception as e:
            logger.warning("Could not save %d activity entries: %s", len(rows), e)
            with _lock:
                _buffer[:0] = rows[: _MAX_BUFFER]  # try again next minute
    now = now_kz()
    if _last_prune is None or now - _last_prune > timedelta(hours=24):
        _last_prune = now
        try:
            prune_activity((now - timedelta(days=RETENTION_DAYS)).isoformat(timespec="seconds"))
        except Exception as e:
            logger.warning("Could not prune the activity log: %s", e)


async def flush_activity_job(context: ContextTypes.DEFAULT_TYPE = None):
    """Every minute; touches the database only if something is waiting
    (and once a day for the cleanup)."""
    with _lock:
        waiting = bool(_buffer)
    if waiting or _last_prune is None or now_kz() - _last_prune > timedelta(hours=24):
        await asyncio.to_thread(_flush_blocking)


def flush_now():
    """Synchronous flush, used before /activity reads the table."""
    _flush_blocking()


def handler():
    return TypeHandler(Update, log_activity)
