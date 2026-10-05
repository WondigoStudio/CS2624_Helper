"""Telegram Mini App backend: a tiny stdlib HTTP server (no extra
dependencies) that serves the single-page UI in webapp/index.html and a
small JSON API on top of the same database the bot uses.

Runs inside the same process as the bot, on the $PORT Render already
requires for its health check (see config.start_health_check_server).

Auth: every API call carries Telegram's signed `initData` string. It is
verified here with an HMAC keyed by the bot token, per
https://core.telegram.org/bots/webapps#validating-data-received-via-a-web-app
— so nobody can call the API as someone else, and no separate login exists.
"""

import hashlib
import hmac
import html
import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl

from .config import BOT_TOKEN, SHARED_TASKS_ID, logger
from .constants import SUBJECT_EMOJI, SUBJECT_NAME
from .db import get_birthdays, get_lessons, get_task, get_task_attachments, get_tasks, mark_done
from .permissions import is_schedule_allowed
from .utils import is_task_overdue, next_birthday_date, now_kz, today_kz

_INDEX_PATH = Path(__file__).parent / "webapp" / "index.html"
_MAX_BODY = 64 * 1024
_INIT_DATA_MAX_AGE = 24 * 3600  # seconds; Telegram refreshes initData on each open

_TAG_RE = re.compile(r"<[^>]+>")


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
    }


def build_state(user: dict) -> dict:
    today = today_kz()
    tasks = [_task_json(r) for r in get_tasks(SHARED_TASKS_ID)]
    lessons = [
        {
            "weekday": r["weekday"],
            "time": r["time"],
            "subject_name": SUBJECT_NAME.get(r["subject"], r["subject"]),
            "emoji": SUBJECT_EMOJI.get(r["subject"], "📘"),
            "room": r["room"],
        }
        for r in get_lessons(user["id"])  # in a private chat chat_id == user id
    ]
    birthdays = []
    for r in get_birthdays():
        nxt = next_birthday_date(r["day"], r["month"], today)
        birthdays.append(
            {
                "name": r["display_name"],
                "day": r["day"],
                "month": r["month"],
                "days_left": (nxt - today).days,
            }
        )
    birthdays.sort(key=lambda b: b["days_left"])
    return {
        "user": {"id": user["id"], "first_name": user.get("first_name", "")},
        "can_edit": is_schedule_allowed(user["id"]),
        "today": today.isoformat(),
        "weekday": today.weekday(),
        "now": now_kz().strftime("%H:%M"),
        "tasks": tasks,
        "lessons": lessons,
        "birthdays": birthdays,
    }


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # keep the bot's own logs clean

    # -- helpers --------------------------------------------------------
    def _send(self, code: int, body: bytes, content_type: str):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, payload: dict):
        self._send(code, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8")

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
                self._send(200, _INDEX_PATH.read_bytes(), "text/html; charset=utf-8")
            except OSError:
                self._send(200, b"ok", "text/plain")
        else:
            self._send(200, b"ok", "text/plain")

    do_HEAD = do_GET

    def do_POST(self):
        body = self._read_json()
        if not isinstance(body, dict):
            self._json(400, {"error": "bad_request"})
            return
        user = validate_init_data(body.get("initData", ""))
        if user is None:
            self._json(401, {"error": "unauthorized"})
            return
        try:
            if self.path == "/api/state":
                self._json(200, build_state(user))
            elif self.path == "/api/done":
                if not is_schedule_allowed(user["id"]):
                    self._json(403, {"error": "forbidden"})
                    return
                task_id = int(body.get("task_id", 0))
                task = get_task(task_id)
                if not task or task["chat_id"] != SHARED_TASKS_ID:
                    self._json(404, {"error": "not_found"})
                    return
                mark_done(task_id)
                self._json(200, {"ok": True})
            else:
                self._json(404, {"error": "not_found"})
        except Exception as e:  # never let a bad request kill the thread silently
            logger.warning("Mini App API error on %s: %s", self.path, e)
            self._json(500, {"error": "server_error"})


def make_server(port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    server.daemon_threads = True
    return server
