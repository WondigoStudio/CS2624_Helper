"""Who the bot may write to on its own, and what.

Five kinds of unprompted messages (KINDS). For every chat the admin can force each kind on
or off (/audience); where nothing was chosen, the default applies: only private chats of people
who were given a permission (or admins) — groups get nothing. The admin can also switch the
default to "every chat the bot knows" (the old behaviour).
Answers to commands, a group's poll (/setreport) and personal /remind reminders are not part of this.
"""

from .db import chat_send_overrides, get_setting, set_chat_send, set_setting
from .permissions import is_admin, user_perms

KEY = "audience"
PRIVATE = "private_allowed"   # default
EVERYONE = "all"              # how the bot behaved before

KINDS = [
    ("schedule", "🌅 Расписание по утрам"),
    ("weather", "🌤 Погода по утрам"),
    ("tasks", "🔔 Напоминание о заданиях"),
    ("lessons", "⏰ Пара через 10 минут"),
    ("deadlines", "📌 Дедлайн через час"),
]
KIND_KEYS = [k for k, _ in KINDS]


def mode() -> str:
    return EVERYONE if get_setting(KEY) == EVERYONE else PRIVATE


def set_mode(value: str):
    set_setting(KEY, EVERYONE if value == EVERYONE else None)  # default = no row


def default_allowed(chat_id: int) -> bool:
    """The rule that applies where the admin made no explicit choice."""
    if mode() == EVERYONE:
        return True
    if not chat_id or chat_id < 0:  # groups, supergroups, channels
        return False
    return is_admin(chat_id) or bool(user_perms(chat_id))


def override(chat_id: int, kind: str):
    """True / False if the admin forced it, None for "by default"."""
    v = chat_send_overrides().get((chat_id, kind))
    return None if v is None else bool(v)


def allowed(chat_id: int, kind: str = None) -> bool:
    if kind is not None:
        forced = override(chat_id, kind)
        if forced is not None:
            return forced
    return default_allowed(chat_id)


def set_override(chat_id: int, kind: str, value):
    if kind not in KIND_KEYS:
        raise ValueError(kind)
    set_chat_send(chat_id, kind, value)


def reset(chat_id: int):
    for k in KIND_KEYS:
        set_chat_send(chat_id, k, None)


def cycle(chat_id: int, kind: str):
    """default -> on -> off -> default"""
    nxt = {None: True, True: False, False: None}[override(chat_id, kind)]
    set_override(chat_id, kind, nxt)
    return nxt
