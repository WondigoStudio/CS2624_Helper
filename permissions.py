"""Access control: bot admins (fixed set) and the per-user allow-list for
managing the shared schedule."""

from .config import ADMIN_IDS
from .db import db
from .utils import now_kz


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def is_schedule_allowed(user_id: int) -> bool:
    if is_admin(user_id):
        return True
    conn = db()
    row = conn.execute("SELECT user_id FROM schedule_allowed_users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return bool(row)


def allow_user_schedule(user_id: int):
    conn = db()
    conn.execute(
        "INSERT INTO schedule_allowed_users (user_id, created_at) VALUES (?, ?) ON CONFLICT(user_id) DO NOTHING",
        (user_id, now_kz().isoformat())
    )
    conn.commit()
    conn.close()


def disallow_user_schedule(user_id: int):
    conn = db()
    conn.execute("DELETE FROM schedule_allowed_users WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
