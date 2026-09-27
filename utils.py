"""Small stateless helpers: current time/date, parsing user-typed dates and
times, and splitting long messages so they fit under Telegram's cap."""

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


def _time_minus_minutes(time_str: str, minutes: int) -> str:
    t = datetime.strptime(time_str, "%H:%M")
    return (t - timedelta(minutes=minutes)).strftime("%H:%M")
