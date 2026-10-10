"""Access control: bot admins (fixed set) and the per-user allow-list for
managing the shared schedule."""

from .config import ADMIN_IDS
from .db import db
from .utils import now_kz


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


# What can be granted to a person, one by one. Admins always have everything.
PERMS = {"tasks": "Задания", "schedule": "Расписание", "lms": "LMS"}


def can(user_id: int, perm: str) -> bool:
    if is_admin(user_id):
        return True
    conn = db()
    row = conn.execute(
        "SELECT 1 FROM user_perms WHERE user_id = ? AND perm = ?", (user_id, perm)
    ).fetchone()
    conn.close()
    return bool(row)


def user_perms(user_id: int) -> set:
    conn = db()
    rows = conn.execute("SELECT perm FROM user_perms WHERE user_id = ?", (user_id,)).fetchall()
    conn.close()
    return {r["perm"] for r in rows}


def set_perm(user_id: int, perm: str, on: bool):
    if perm not in PERMS:
        raise ValueError(perm)
    conn = db()
    if on:
        conn.execute(
            "INSERT INTO user_perms (user_id, perm) VALUES (?, ?) ON CONFLICT(user_id, perm) DO NOTHING",
            (user_id, perm),
        )
    else:
        conn.execute("DELETE FROM user_perms WHERE user_id = ? AND perm = ?", (user_id, perm))
    conn.commit()
    conn.close()


def is_schedule_allowed(user_id: int) -> bool:
    """Kept for old call sites: the timetable permission."""
    return can(user_id, "schedule")
