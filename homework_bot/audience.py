"""Who the bot may write to on its own (morning schedule, weather, task and lesson reminders,
deadline heads-ups).

Default: only private chats of people who were given a permission (or admins). Groups and
people without any permission get nothing unprompted. The admin can switch to the old
behaviour ("every chat the bot knows") with /audience. Answers to commands, the morning poll of
a group enabled with /setreport and personal /remind reminders are not affected.
"""

from .db import get_setting, set_setting
from .permissions import is_admin, user_perms

KEY = "audience"
PRIVATE = "private_allowed"   # default
EVERYONE = "all"              # how the bot behaved before


def mode() -> str:
    return EVERYONE if get_setting(KEY) == EVERYONE else PRIVATE


def set_mode(value: str):
    set_setting(KEY, EVERYONE if value == EVERYONE else None)  # default = no row


def allowed(chat_id: int) -> bool:
    if mode() == EVERYONE:
        return True
    if not chat_id or chat_id < 0:  # groups, supergroups, channels
        return False
    return is_admin(chat_id) or bool(user_perms(chat_id))
