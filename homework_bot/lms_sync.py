"""Pulls deadlines from the university LMS (Moodle) calendar export (an
.ics feed) into the shared task list.

What gets imported: events whose title ends in "is due" or "closes"
(assignments, quizzes). Everything else — "opens", class "Attendance"
sessions — is ignored here. Each LMS event is remembered by its UID, so:

  * running the sync again never creates duplicates;
  * if the LMS moves a deadline or renames the event, the task follows;
  * if someone deletes a synced task in the bot, it is NOT re-added.

Each person connects their own calendar link with /lms. The same course
event has the same UID in everyone's feed (and a same-looking assignment is
matched by subject/title/date), so people sharing courses never create
duplicates in the shared list. A link contains a personal access token, so
it is stored only in the database and never logged or printed.
"""

import re
from urllib.parse import urlparse
from datetime import datetime, timedelta, timezone

from .config import LMS_HOST, SHARED_TASKS_ID, TIMEZONE, logger, requests
from .db import (
    add_task,
    find_duplicate_task,
    get_lms_link,
    list_lms_feeds,
    set_lms_feed_result,
    get_task,
    reset_task_deadline_notified,
    set_lms_link,
    update_task_description,
    update_task_field,
)
from .utils import now_kz

# LMS category (before the " | teacher" part) -> the bot's subject code.
# Matched by substring, lowercase, first hit wins.
_SUBJECT_KEYWORDS = [
    ("foreign language", "flb2"),
    ("english", "flb2"),
    ("psychology", "psy"),
    ("sociology", "soc"),
    ("discrete", "dm"),
    ("information and communication", "ict"),
    ("programming", "itp"),
    ("chinese", "chn"),
    ("physical education", "pe"),
]

_DEADLINE_RE = re.compile(r"\b(is due|closes)\s*$", re.IGNORECASE)
_DUE_SUFFIX_RE = re.compile(r"\s+is due\s*$", re.IGNORECASE)
# deadlines that passed more than this long ago aren't worth importing
_STALE_AFTER = timedelta(days=1)
_MAX_DESCRIPTION = 3500


# ---------------------------------------------------------------------------
# .ics parsing (just the subset Moodle emits — no extra dependency)
# ---------------------------------------------------------------------------
def _unfold(text: str):
    """RFC 5545: a line starting with a space/tab continues the previous one."""
    lines = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _unescape(value: str) -> str:
    out, i = [], 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            out.append("\n" if nxt in "nN" else nxt)
            i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _parse_dt(value: str):
    """Aware datetime in the bot's timezone, or None. Floating and
    date-only values are taken as local time."""
    value = value.strip()
    try:
        if value.endswith("Z"):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).astimezone(TIMEZONE)
        if "T" in value:
            return datetime.strptime(value, "%Y%m%dT%H%M%S").replace(tzinfo=TIMEZONE)
        return datetime.strptime(value, "%Y%m%d").replace(tzinfo=TIMEZONE)
    except ValueError:
        return None


def parse_ics(text: str):
    events, current = [], None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT":
            if current is not None:
                events.append(current)
            current = None
        elif current is not None and ":" in line:
            head, value = line.split(":", 1)
            name = head.split(";", 1)[0].upper()
            if name in ("UID", "SUMMARY", "DESCRIPTION", "CATEGORIES"):
                current[name.lower()] = _unescape(value)
            elif name == "DTSTART":
                current["start"] = _parse_dt(value)
                current["all_day"] = "T" not in value
    return events


def subject_for(category: str):
    label = (category or "").split("|", 1)[0].lower()
    for keyword, code in _SUBJECT_KEYWORDS:
        if keyword in label:
            return code
    return None


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------
def _clean_title(summary: str) -> str:
    return _DUE_SUFFIX_RE.sub("", summary).strip()[:200]


def apply_events(events, now=None) -> dict:
    """Writes the deadline events into the task list. Returns a report."""
    now = now or now_kz()
    report = {"events": len(events), "added": [], "updated": [], "unknown_subject": [], "already": 0}
    for ev in events:
        uid, summary, start = ev.get("uid"), (ev.get("summary") or "").strip(), ev.get("start")
        if not uid or not summary or start is None or not _DEADLINE_RE.search(summary):
            continue
        link = get_lms_link(uid)
        if link is not None:
            task = get_task(link["task_id"])
            if task is None:
                continue  # someone deleted it in the bot — keep it deleted
        elif now - start > _STALE_AFTER:
            continue  # old deadline we never imported — don't bring it back

        subject = subject_for(ev.get("categories", ""))
        if subject is None:
            if summary not in report["unknown_subject"]:
                report["unknown_subject"].append(summary)
            continue

        title = _clean_title(summary)
        due_date = start.date().isoformat()
        due_time = None if ev.get("all_day") else start.strftime("%H:%M")
        description = (ev.get("description") or "").strip()[:_MAX_DESCRIPTION] or None
        signature = f"{title}|{due_date}|{due_time}|{subject}|{description}"

        if link is None:
            same = find_duplicate_task(subject, title, due_date, due_time)
            if same is not None:
                # already in the list (typed by hand, or via someone else's feed)
                set_lms_link(uid, same["id"], signature)
                report["already"] += 1
                continue
            task_id = add_task(
                SHARED_TASKS_ID, subject, title, due_date, due_time,
                created_by="LMS", description=description,
            )
            set_lms_link(uid, task_id, signature)
            report["added"].append(f"{title} — {start.strftime('%d.%m %H:%M')}")
        elif link["signature"] != signature:
            tid = task["id"]
            update_task_field(tid, "title", title)
            update_task_field(tid, "subject", subject)
            if (task["due_date"], task["due_time"] or None) != (due_date, due_time):
                update_task_field(tid, "due_date", due_date)
                update_task_field(tid, "due_time", due_time)
                reset_task_deadline_notified(tid)
            update_task_description(tid, description, False)
            set_lms_link(uid, tid, signature)
            report["updated"].append(title)
        else:
            report["already"] += 1
    return report


def validate_feed_url(raw: str):
    """The cleaned-up link if it looks like a Moodle calendar export on the
    allowed LMS host, otherwise None. (Only that host is ever fetched, so
    pasting some other address can't make the bot call arbitrary servers.)"""
    url = (raw or "").strip().strip("<>").strip()
    try:
        parts = urlparse(url)
    except ValueError:
        return None
    if parts.scheme != "https" or (parts.hostname or "").lower() != LMS_HOST:
        return None
    if "export_execute.php" not in parts.path or "authtoken=" not in parts.query:
        return None
    return url


def fetch_events(url: str):
    """Blocking. Raises RuntimeError with a safe message (requests errors can
    embed the full URL, token included, so they are never passed on)."""
    try:
        resp = requests.get(url, timeout=30, allow_redirects=False)
    except Exception as e:
        raise RuntimeError(f"LMS недоступен ({type(e).__name__})") from None
    if resp.status_code != 200:
        raise RuntimeError(f"LMS ответил кодом {resp.status_code} — ссылка могла устареть")
    if "BEGIN:VCALENDAR" not in resp.text:
        raise RuntimeError("LMS ответил не календарём — ссылка могла устареть")
    return parse_ics(resp.text)


def sync_feed(url: str) -> dict:
    """Blocking (HTTP + DB) — run via asyncio.to_thread."""
    report = apply_events(fetch_events(url))
    logger.info(
        "LMS sync: %s events, %s added, %s updated",
        report["events"], len(report["added"]), len(report["updated"]),
    )
    return report


def sync_all_feeds() -> list:
    """Syncs every connected person's feed. Returns
    [(user_id, report_or_None, error_or_None), ...]."""
    results = []
    for feed in list_lms_feeds():
        try:
            report = sync_feed(feed["url"])
            set_lms_feed_result(feed["user_id"], ok=True)
            results.append((feed["user_id"], report, None))
        except Exception as e:
            logger.warning("LMS sync failed for user %s: %s", feed["user_id"], e)
            results.append((feed["user_id"], None, str(e)))
    return results
