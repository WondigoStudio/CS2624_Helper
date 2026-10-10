"""Telegram Mini App backend: a tiny stdlib HTTP server (no extra
dependencies) that serves the single-page UI in webapp/index.html and a
small JSON API on top of the same database the bot uses.

Runs inside the same process as the bot, on the $PORT Render already
requires for its health check (see config.start_health_check_server).

Auth: every API call carries Telegram's signed `initData` string. It is
verified here with an HMAC keyed by the bot token, per
https://core.telegram.org/bots/webapps#validating-data-received-via-a-web-app
— so nobody can call the API as someone else, and no separate login exists.

Permissions mirror the chat commands: managing tasks/schedule needs
is_schedule_allowed (same as /add, /done, /edittask, /schedule_add);
birthdays are open to everyone (same as /addbirthday), and deleting one
needs being whoever added it or an admin.
"""

import calendar
import gzip
import hashlib
import hmac
import html
import json
import re
import time
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl

from .ai_format import STRUCTURE_MIN_LENGTH, _sanitize_telegram_html, _structure_with_groq
from .config import BOT_TOKEN, GROQ_API_KEY, SHARED_TASKS_ID, logger, requests
from .constants import SUBJECT_EMOJI, SUBJECT_NAME, SUBJECTS
from .db import (
    add_birthday,
    add_lesson,
    add_task,
    delete_birthday,
    delete_lesson,
    delete_task,
    get_birthday,
    get_birthdays,
    get_db_status,
    get_lesson,
    get_lessons,
    get_task,
    get_task_attachments,
    get_tasks,
    mark_done,
    reset_task_deadline_notified,
    unmark_done,
    update_task_description,
    update_task_field,
)
from .permissions import can, is_admin
from .utils import is_task_overdue, next_birthday_date, now_kz, parse_due_time, today_kz

_INDEX_PATH = Path(__file__).parent / "webapp" / "index.html"
_MAX_BODY = 64 * 1024
_INIT_DATA_MAX_AGE = 24 * 3600  # seconds; Telegram refreshes initData on each open
_MAX_FILES_PER_REQUEST = 10

_TAG_RE = re.compile(r"<[^>]+>")


class ApiError(Exception):
    def __init__(self, code: int, error: str):
        super().__init__(error)
        self.code = code
        self.error = error


def validate_init_data(init_data: str):
    """Returns the Telegram user dict if init_data is genuine and fresh,
    otherwise None."""
    if not init_data or not BOT_TOKEN:
        return None
    try:
        params = dict(parse_qsl(init_data, keep_blank_values=True))
    except Exception:
        return None
    received_hash = params.pop("hash", None)
    if not received_hash:
        return None
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        return None
    try:
        if time.time() - int(params.get("auth_date", "0")) > _INIT_DATA_MAX_AGE:
            return None
        user = json.loads(params.get("user", ""))
    except (ValueError, TypeError):
        return None
    return user if isinstance(user, dict) and "id" in user else None


def _plain(text: str, is_html: bool) -> str:
    if not text:
        return ""
    return html.unescape(_TAG_RE.sub("", text)) if is_html else text


def _task_json(row) -> dict:
    return {
        "id": row["id"],
        "subject": row["subject"],
        "subject_name": SUBJECT_NAME.get(row["subject"], row["subject"]),
        "emoji": SUBJECT_EMOJI.get(row["subject"], "📘"),
        "title": row["title"],
        "description": _plain(row["description"], bool(row["description_html"])),
        "due_date": row["due_date"],
        "due_time": row["due_time"] or "",
        "overdue": is_task_overdue(row),
        "attachments": len(get_task_attachments(row["id"])),
        "created_by": row["created_by"] or "",
    }


def build_state(user: dict) -> dict:
    uid = user["id"]
    today = today_kz()
    all_rows = get_tasks(SHARED_TASKS_ID, only_undone=False, viewer_id=uid)
    todo = [r for r in all_rows if not r["my_done"]]
    # only the marks this person made themselves can be undone (legacy
    # globally-finished tasks stay hidden — there's no per-user mark to remove)
    finished = [r for r in all_rows if r["my_done"] and not r["done"]]
    finished.sort(key=lambda r: r["due_date"], reverse=True)

    lessons = [
        {
            "id": r["id"],
            "weekday": r["weekday"],
            "time": r["time"],
            "subject": r["subject"],
            "subject_name": SUBJECT_NAME.get(r["subject"], r["subject"]),
            "emoji": SUBJECT_EMOJI.get(r["subject"], "📘"),
            "room": r["room"],
        }
        for r in get_lessons(uid)  # in a private chat chat_id == user id
    ]
    birthdays = []
    admin = is_admin(uid)
    for r in get_birthdays():
        nxt = next_birthday_date(r["day"], r["month"], today)
        birthdays.append(
            {
                "id": r["id"],
                "name": r["display_name"],
                "day": r["day"],
                "month": r["month"],
                "days_left": (nxt - today).days,
                "can_delete": admin or r["added_by"] == uid,
            }
        )
    birthdays.sort(key=lambda b: b["days_left"])

    state = {
        "user": {"id": uid, "first_name": user.get("first_name", "")},
        "can_edit": can(uid, "tasks"),
        "can_schedule": can(uid, "schedule"),
        "is_admin": admin,
        "today": today.isoformat(),
        "weekday": today.weekday(),
        "now": now_kz().strftime("%H:%M"),
        "subjects": [{"code": c, "name": n, "emoji": SUBJECT_EMOJI.get(c, "📘")} for c, n in SUBJECTS],
        "tasks": [_task_json(r) for r in todo],
        "done_tasks": [_task_json(r) for r in finished[:40]],
        "lessons": lessons,
        "birthdays": birthdays,
        "stats": {"done": len(finished), "todo": len(todo)},
    }
    if admin:
        status = get_db_status()
        labels = ["основная", "резервная №2", "резервная №3"]
        idx = status.get("active_index", 0)
        state["db"] = {
            "postgres": bool(status.get("using_postgres")),
            "active": labels[idx] if idx < len(labels) else "?",
            "on_primary": idx == 0,
            "count": status.get("configured_count", 1),
        }
    return state


# ---------------------------------------------------------------------------
# Input validation helpers
# ---------------------------------------------------------------------------
def _require_editor(user: dict, perm: str = "tasks"):
    if not can(user["id"], perm):
        raise ApiError(403, "forbidden")


def _get_shared_task(body: dict):
    try:
        task_id = int(body.get("task_id", 0))
    except (TypeError, ValueError):
        raise ApiError(400, "bad_request")
    task = get_task(task_id)
    if not task or task["chat_id"] != SHARED_TASKS_ID:
        raise ApiError(404, "not_found")
    return task


def _clean_text(value, limit: int, required: bool = False) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if required and not text:
        raise ApiError(400, "empty")
    if len(text) > limit:
        raise ApiError(400, "too_long")
    return text


def _clean_date(value) -> str:
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise ApiError(400, "bad_date")


def _clean_time(value) -> str:
    """'' means no exact time."""
    if value in (None, ""):
        return ""
    t = parse_due_time(str(value))
    if not t:
        raise ApiError(400, "bad_time")
    return t


def _clean_subject(value) -> str:
    if value not in SUBJECT_NAME:
        raise ApiError(400, "bad_subject")
    return value


def _description_for_storage(text: str):
    """(text, is_html): long descriptions get structured by the same Groq
    helper the chat flow uses, falling back silently to plain text."""
    if GROQ_API_KEY and len(text) >= STRUCTURE_MIN_LENGTH:
        try:
            structured = _structure_with_groq(text)
            if structured:
                return _sanitize_telegram_html(structured), True
        except Exception as e:
            logger.warning("Mini App description structuring failed: %s", e)
    return text, False


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
def act_state(user, body):
    return build_state(user)


def act_done(user, body):
    _require_editor(user)
    task = _get_shared_task(body)
    mark_done(task["id"], user["id"])
    return {"ok": True}


def act_undone(user, body):
    _require_editor(user)
    task = _get_shared_task(body)
    unmark_done(task["id"], user["id"])
    return {"ok": True}


def act_task_add(user, body):
    _require_editor(user)
    subject = _clean_subject(body.get("subject"))
    title = _clean_text(body.get("title"), 200, required=True)
    description = _clean_text(body.get("description"), 3500)
    due_date = _clean_date(body.get("due_date"))
    due_time = _clean_time(body.get("due_time"))
    text, is_html = _description_for_storage(description) if description else ("", False)
    task_id = add_task(
        SHARED_TASKS_ID, subject, title, due_date, due_time or None,
        created_by=user.get("first_name") or str(user["id"]),
        description=text or None, description_html=is_html,
    )
    return {"ok": True, "id": task_id, "structured": is_html}


def act_task_edit(user, body):
    _require_editor(user)
    task = _get_shared_task(body)
    task_id = task["id"]
    fields = body.get("fields")
    if not isinstance(fields, dict):
        raise ApiError(400, "bad_request")
    deadline_changed = False
    if "subject" in fields:
        update_task_field(task_id, "subject", _clean_subject(fields["subject"]))
    if "title" in fields:
        update_task_field(task_id, "title", _clean_text(fields["title"], 200, required=True))
    if "due_date" in fields:
        update_task_field(task_id, "due_date", _clean_date(fields["due_date"]))
        deadline_changed = True
    if "due_time" in fields:
        update_task_field(task_id, "due_time", _clean_time(fields["due_time"]) or None)
        deadline_changed = True
    if "description" in fields:
        new_desc = _clean_text(fields["description"], 3500)
        old_plain = _plain(task["description"], bool(task["description_html"]))
        if new_desc != old_plain:  # untouched text keeps its bold/italic structure
            text, is_html = _description_for_storage(new_desc) if new_desc else ("", False)
            update_task_description(task_id, text or None, is_html)
    if deadline_changed:
        reset_task_deadline_notified(task_id)
    return {"ok": True}


def act_task_delete(user, body):
    _require_editor(user)
    task = _get_shared_task(body)
    delete_task(task["id"])
    return {"ok": True}


def _telegram_send_file(chat_id: int, att, caption: str = None):
    method, field = ("sendPhoto", "photo") if att["kind"] == "photo" else ("sendDocument", "document")
    payload = {"chat_id": chat_id, field: att["file_id"]}
    if caption:
        payload["caption"] = caption[:1000]
    resp = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", json=payload, timeout=30)
    resp.raise_for_status()


def act_task_files(user, body):
    """Sends the task's attachments to the person's private chat with the
    bot — Mini Apps can't hand Telegram file_ids to the browser, so the
    server forwards them the same way /taskfile does."""
    task = _get_shared_task(body)
    atts = get_task_attachments(task["id"])[:_MAX_FILES_PER_REQUEST]
    if not atts:
        raise ApiError(404, "no_files")
    caption = f"📎 {SUBJECT_NAME.get(task['subject'], task['subject'])}: {task['title']}"
    sent = 0
    for i, att in enumerate(atts):
        try:
            _telegram_send_file(user["id"], att, caption if i == 0 else None)
            sent += 1
        except Exception as e:
            logger.warning("Mini App could not send attachment of task %s: %s", task["id"], e)
    if not sent:
        raise ApiError(502, "send_failed")
    return {"ok": True, "sent": sent}


def act_lesson_add(user, body):
    _require_editor(user, "schedule")
    try:
        weekday = int(body.get("weekday"))
    except (TypeError, ValueError):
        raise ApiError(400, "bad_weekday")
    if not 0 <= weekday <= 6:
        raise ApiError(400, "bad_weekday")
    subject = _clean_subject(body.get("subject"))
    time_str = _clean_time(body.get("time"))
    if not time_str:
        raise ApiError(400, "bad_time")
    room = _clean_text(body.get("room"), 40, required=True)
    add_lesson(user["id"], weekday, time_str, subject, room)
    return {"ok": True}


def act_lesson_delete(user, body):
    _require_editor(user, "schedule")
    try:
        lesson = get_lesson(int(body.get("lesson_id", 0)))
    except (TypeError, ValueError):
        raise ApiError(400, "bad_request")
    if not lesson or lesson["chat_id"] != user["id"]:
        raise ApiError(404, "not_found")
    delete_lesson(lesson["id"])
    return {"ok": True}


def act_bday_add(user, body):
    name = _clean_text(body.get("name"), 60, required=True)
    try:
        day, month = int(body.get("day")), int(body.get("month"))
        year = int(body["year"]) if body.get("year") else None
        # 2000 is a leap year, so Feb 29 is accepted when no year is given
        calendar.monthrange(year or 2000, month)
        if not 1 <= day <= calendar.monthrange(year or 2000, month)[1]:
            raise ValueError
        if year is not None and not 1900 <= year <= date.today().year:
            raise ValueError
    except (TypeError, ValueError, calendar.IllegalMonthError):
        raise ApiError(400, "bad_date")
    add_birthday(0, user["id"], name, day, month, user["id"], year=year)
    return {"ok": True}


def act_bday_delete(user, body):
    try:
        row = get_birthday(int(body.get("id", 0)))
    except (TypeError, ValueError):
        raise ApiError(400, "bad_request")
    if not row:
        raise ApiError(404, "not_found")
    if not (is_admin(user["id"]) or row["added_by"] == user["id"]):
        raise ApiError(403, "forbidden")
    delete_birthday(row["id"])
    return {"ok": True}


_ROUTES = {
    "/api/state": act_state,
    "/api/done": act_done,
    "/api/undone": act_undone,
    "/api/task/add": act_task_add,
    "/api/task/edit": act_task_edit,
    "/api/task/delete": act_task_delete,
    "/api/task/files": act_task_files,
    "/api/lesson/add": act_lesson_add,
    "/api/lesson/delete": act_lesson_delete,
    "/api/bday/add": act_bday_add,
    "/api/bday/delete": act_bday_delete,
}


_index_cache = {"mtime": None, "raw": b"", "gz": b"", "etag": ""}


def _index_page():
    """The page, its gzip form and an ETag — rebuilt only when the file changes."""
    mtime = _INDEX_PATH.stat().st_mtime
    if _index_cache["mtime"] != mtime:
        raw = _INDEX_PATH.read_bytes()
        _index_cache.update(
            mtime=mtime, raw=raw, gz=gzip.compress(raw, 6),
            etag='"' + hashlib.md5(raw).hexdigest() + '"',
        )
    return _index_cache


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive: no new connection per request
    timeout = 30  # don't let a dead client hold a thread forever

    def log_message(self, *args):
        pass  # keep the bot's own logs clean

    # -- helpers --------------------------------------------------------
    def _send(self, code: int, body: bytes, content_type: str, extra=None, is_gzip=False, compress=True):
        try:
            if (
                compress and not is_gzip and len(body) > 1024
                and "gzip" in self.headers.get("Accept-Encoding", "")
            ):
                body = gzip.compress(body, 5)
                is_gzip = True
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if is_gzip:
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Vary", "Accept-Encoding")
            for k, v in (extra or {"Cache-Control": "no-store"}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True  # the phone closed the app / lost signal mid-response

    def _json(self, code: int, payload: dict):
        self._send(code, json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
                   "application/json; charset=utf-8")

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None
        if length <= 0 or length > _MAX_BODY:
            return None
        try:
            return json.loads(self.rfile.read(length))
        except ValueError:
            return None

    # -- routes ---------------------------------------------------------
    def do_GET(self):
        # "/" doubles as Render's health check, so it must always answer 200.
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            try:
                page = _index_page()
            except OSError:
                self._send(200, b"ok", "text/plain")
                return
            headers = {"Cache-Control": "no-cache", "ETag": page["etag"]}  # revalidate: a 304 is instant
            if self.headers.get("If-None-Match") == page["etag"]:
                self._send(304, b"", "text/html; charset=utf-8", extra=headers)
            elif "gzip" in self.headers.get("Accept-Encoding", ""):
                self._send(200, page["gz"], "text/html; charset=utf-8", extra=headers, is_gzip=True)
            else:
                self._send(200, page["raw"], "text/html; charset=utf-8", extra=headers, compress=False)
        else:
            self._send(200, b"ok", "text/plain")

    do_HEAD = do_GET

    def do_POST(self):
        action = _ROUTES.get(self.path.split("?", 1)[0])
        if action is None:
            self._json(404, {"error": "not_found"})
            return
        body = self._read_json()
        if not isinstance(body, dict):
            self._json(400, {"error": "bad_request"})
            return
        user = validate_init_data(body.get("initData", ""))
        if user is None:
            self._json(401, {"error": "unauthorized"})
            return
        try:
            result = action(user, body)
            if body.get("want_state") and isinstance(result, dict):
                result["state"] = build_state(user)  # saves the app a second round trip
            self._json(200, result)
        except ApiError as e:
            self._json(e.code, {"error": e.error})
        except Exception as e:  # never let a bad request kill the thread silently
            logger.warning("Mini App API error on %s: %s", self.path, e)
            self._json(500, {"error": "server_error"})


def make_server(port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    server.daemon_threads = True
    return server
