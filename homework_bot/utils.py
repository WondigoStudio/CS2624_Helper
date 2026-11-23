"""Small stateless helpers: current time/date, parsing user-typed dates and
times, and splitting long messages so they fit under Telegram's cap."""

import re
from datetime import datetime, date, timedelta

from .config import TIMEZONE, SCHEDULE_HOUR, SCHEDULE_MINUTE, REMINDER_HOUR, REMINDER_MINUTE


def now_kz() -> datetime:
    return datetime.now(TIMEZONE)


def today_kz() -> date:
    return now_kz().date()


def task_due_datetime(row):
    """Full due date+time (timezone-aware, Kazakhstan) for a task, if its
    due_time is known. Returns None when the task has no due_time, in which
    case only the date (not a precise moment) is meaningful."""
    if not row["due_time"]:
        return None
    try:
        return datetime.strptime(
            f"{row['due_date']} {row['due_time']}", "%Y-%m-%d %H:%M"
        ).replace(tzinfo=TIMEZONE)
    except ValueError:
        return None


def is_task_overdue(row) -> bool:
    """True once the task's deadline is actually in the past — comparing
    the exact due date+time when a time was set, and just the date
    (has the day already ended) when it wasn't."""
    if row["done"]:
        return False
    due_dt = task_due_datetime(row)
    if due_dt is not None:
        return due_dt < now_kz()
    d = datetime.strptime(row["due_date"], "%Y-%m-%d").date()
    return d < today_kz()


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


TELEGRAM_MESSAGE_LIMIT = 4096


def _chunk_text(text: str, limit: int = TELEGRAM_MESSAGE_LIMIT):
    """Split text into <=limit-char pieces, breaking on line boundaries
    where possible so a single task's line is never cut in the middle."""
    if len(text) <= limit:
        return [text]
    chunks = []
    current = ""
    for line in text.split("\n"):
        # +1 accounts for the "\n" that will join it back to current
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            if current:
                chunks.append(current)
            if len(line) > limit:
                # a single line longer than the whole limit (very long
                # description) — hard-split it, nothing better to do
                for i in range(0, len(line), limit):
                    chunks.append(line[i:i + limit])
                current = ""
            else:
                current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


async def reply_text_chunked(message, text: str, **kwargs):
    """Like message.reply_text, but splits text that would exceed
    Telegram's 4096-character message cap (otherwise send_message raises
    BadRequest: Message is too long and the handler crashes)."""
    for chunk in _chunk_text(text):
        await message.reply_text(chunk, **kwargs)


SCHEDULE_OFFSET_MINUTES = 60  # личка: расписание — за столько минут до первой пары
TASKS_OFFSET_MINUTES = 30     # личка: напоминание о заданиях — за столько минут до первой пары
DEFAULT_SCHEDULE_TIME = f"{SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d}"   # "07:30"
DEFAULT_TASKS_TIME = f"{REMINDER_HOUR:02d}:{REMINDER_MINUTE:02d}"       # "08:00"


def next_birthday_date(day: int, month: int, today: date = None) -> date:
    """The next calendar occurrence of a day/month, today counting as
    'next' if it matches. Handles Feb 29 by falling back to Feb 28 in
    non-leap years (so it never raises)."""
    if today is None:
        today = today_kz()
    for year in (today.year, today.year + 1):
        try:
            d = date(year, month, day)
        except ValueError:
            # Feb 29 in a non-leap year
            d = date(year, 2, 28)
        if d >= today:
            return d
    # unreachable in practice, but keep a sane fallback
    return date(today.year + 1, month, min(day, 28))


def _time_minus_minutes(time_str: str, minutes: int) -> str:
    t = datetime.strptime(time_str, "%H:%M")
    return (t - timedelta(minutes=minutes)).strftime("%H:%M")


# --- Room codes like "C1.3.234L" -----------------------------------------------------
# <pavilion>.<building>.<floor digit + two-digit room>[type]:  C1.3.234 = pavilion C1,
# building 3, floor 2, room 34. The optional last letter is the kind of class:
# L - лекция, P - практика, K - компьютерный класс. Anything that doesn't look like
# that (a gym, a free-text name) is shown as typed, with no explanation.
_ROOM_RE = re.compile(
    r"^\s*([A-Za-zА-Яа-я]\d*)\s*[.\-]\s*(\d+)\s*[.\-]\s*([1-9])(\d{2})\s*([A-Za-zА-Яа-я])?\s*$"
)
# look-alikes typed on a Russian keyboard (and Л for «лекция»)
_CYR_TO_LAT = {"С": "C", "с": "C", "А": "A", "а": "A", "В": "B", "в": "B", "Е": "E", "е": "E",
               "Р": "P", "р": "P", "К": "K", "к": "K", "Л": "L", "л": "L"}
ROOM_KINDS = {"L": "лекция", "P": "практика", "K": "компьютерный класс"}


def _latin(text: str) -> str:
    return "".join(_CYR_TO_LAT.get(ch, ch) for ch in text).upper()


def parse_room(room):
    """(pavilion, building, floor, room number, kind letter or None), or None if it isn't such a code."""
    m = _ROOM_RE.match(room or "")
    if not m:
        return None
    number = int(m.group(4))
    kind = _latin(m.group(5)) if m.group(5) else None
    if number == 0 or (kind is not None and kind not in ROOM_KINDS):
        return None
    return _latin(m.group(1)), int(m.group(2)), int(m.group(3)), number, kind


def room_base(room) -> str:
    """The room itself without the class-kind letter, in one canonical spelling — so
    C1.3.234L and C1.3.234P are the same room (one photo serves both)."""
    parsed = parse_room(room)
    if parsed is None:
        return (room or "").strip()
    pavilion, building, floor, number, _kind = parsed
    return f"{pavilion}.{building}.{floor}{number:02d}"


def describe_room(room, short: bool = False):
    """'Павильон C1, 3 корпус, 2 этаж, каб. 34 (лекция)'; short: '3 корп., 2 этаж · лекция'. None if not a code."""
    parsed = parse_room(room)
    if parsed is None:
        return None
    pavilion, building, floor, number, kind = parsed
    if short:
        return f"{building} корп., {floor} этаж" + (f" · {ROOM_KINDS[kind]}" if kind else "")
    return f"Павильон {pavilion}, {building} корпус, {floor} этаж, каб. {number}" + (f" ({ROOM_KINDS[kind]})" if kind else "")


def room_caption(room, suffix: str = "") -> str:
    """Photo caption: '📍 Кабинет C1.3.234L · Павильон C1, 3 корпус, 2 этаж, каб. 34 (лекция)'."""
    info = describe_room(room)
    return f"📍 Кабинет {room}" + (f" · {info}" if info else "") + suffix

