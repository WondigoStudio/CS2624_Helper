"""Vacation mode: a period during which the bot sends nothing on its own.

Stored as one JSON setting, read through the DB cache (a warm check costs no query), so
every scheduled job can ask `silent()` on each run. Replies to commands still work.
"""

import json
import re
from datetime import date, timedelta

from .db import get_setting, set_setting
from .utils import today_kz

KEY = "vacation"
_DATE = re.compile(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?")


def get() -> dict:
    """{"start": date|None, "end": date|None, "personal": bool} or {} when switched off."""
    raw = get_setting(KEY)
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        return {
            "start": date.fromisoformat(d["start"]) if d.get("start") else None,
            "end": date.fromisoformat(d["end"]) if d.get("end") else None,
            "personal": bool(d.get("personal")),
        }
    except Exception:
        return {}


def save(start, end, personal: bool = False):
    set_setting(KEY, json.dumps({
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "personal": bool(personal),
    }))


def clear():
    set_setting(KEY, None)


def is_active(cfg: dict = None, today: date = None) -> bool:
    cfg = get() if cfg is None else cfg
    if not cfg:
        return False
    today = today or today_kz()
    if cfg.get("start") and today < cfg["start"]:
        return False
    if cfg.get("end") and today > cfg["end"]:
        return False
    return True


def silent(personal: bool = False) -> bool:
    """True while scheduled messages must not be sent. `personal=True` asks about the
    people's own /remind reminders, which are silenced only if the admin chose so."""
    cfg = get()
    if not is_active(cfg):
        return False
    return cfg["personal"] if personal else True


def describe(cfg: dict = None, today: date = None) -> str:
    cfg = get() if cfg is None else cfg
    if not cfg:
        return "выключен"
    today = today or today_kz()
    start, end = cfg.get("start"), cfg.get("end")
    fmt = lambda d: d.strftime("%d.%m.%Y")
    span = ("с " + fmt(start) if start else "") + (" " if start and end else "") + ("по " + fmt(end) if end else "")
    if not start and not end:
        span = "без даты окончания"
    elif start and not end:
        span += ", без даты окончания"
    if is_active(cfg, today):
        return f"включён сейчас ({span.strip()})"
    if start and today < start:
        return f"запланирован ({span.strip()})"
    return f"закончился ({span.strip()})"


def parse_period(text: str, today: date = None):
    """"до 10.01" / "25.12 - 10.01" / "с 25.12 по 10.01.2027" / "бессрочно" → (start, end) or None.
    A date without a year means its next occurrence."""
    today = today or today_kz()
    t = (text or "").strip().lower()
    if t in ("бессрочно", "без даты", "навсегда", "пока не выключу"):
        return today, None
    found = _DATE.findall(t)
    if not found or len(found) > 2:
        return None

    def build(day, month, year, not_before):
        d, m = int(day), int(month)
        try:
            if year:
                y = int(year)
                return date(2000 + y if y < 100 else y, m, d)
            out = date(not_before.year, m, d)
            return out if out >= not_before else date(not_before.year + 1, m, d)
        except ValueError:
            return None

    if len(found) == 1:
        end = build(*found[0], today)
        return (today, end) if end and end >= today else None
    start = build(*found[0], today)
    if start is None:
        return None
    end = build(*found[1], start)
    return (start, end) if end and end >= start else None


def days_from_today(n: int):
    today = today_kz()
    return today, today + timedelta(days=n - 1)

