"""Group roles. A role only means something inside a group where a group
admin (or a bot admin) has switched the feature on with /roles_on; everywhere
else every check below answers "no". Future group features (duty rosters,
polls, games...) should ask `has_perm(chat_id, user_id, perm)` and nothing else."""

from .db import get_group_role, roles_enabled
from .permissions import is_admin

# code: (emoji, title, permissions)
ROLES = {
    "headman": ("👑", "Староста", {"manage_roles", "announce", "edit_tasks", "run_games"}),
    "deputy": ("🛡", "Зам. старосты", {"manage_roles", "announce", "edit_tasks", "run_games"}),
    "editor": ("✏️", "Редактор", {"edit_tasks"}),
    "duty": ("🧹", "Дежурный", {"announce"}),
    "host": ("🎤", "Ведущий", {"run_games"}),
}
ALIASES = {
    "староста": "headman", "зам": "deputy", "заместитель": "deputy", "редактор": "editor",
    "дежурный": "duty", "ведущий": "host",
}


def parse_role(text: str):
    t = (text or "").strip().lower()
    if t in ROLES:
        return t
    return ALIASES.get(t)


def role_label(code: str) -> str:
    emoji, title, _ = ROLES[code]
    return f"{emoji} {title}"


def user_role(chat_id: int, user_id: int):
    """The user's role in this group, or None (also None when roles are off)."""
    if not roles_enabled(chat_id):
        return None
    code = get_group_role(chat_id, user_id)
    return code if code in ROLES else None


def has_perm(chat_id: int, user_id: int, perm: str) -> bool:
    code = user_role(chat_id, user_id)
    return bool(code and perm in ROLES[code][2])


async def is_group_admin(bot, chat_id: int, user_id: int) -> bool:
    """Telegram admin/creator of the group, or a bot admin."""
    if is_admin(user_id):
        return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except Exception:
        return False
    return member.status in ("administrator", "creator")
