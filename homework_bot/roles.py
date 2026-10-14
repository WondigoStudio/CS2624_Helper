"""Group roles are plain titles ("Староста", "Шут", ...) with no permissions
attached. They are shown by /roles and work only in groups where a group admin
has switched the feature on with /roles_on."""

from .db import get_group_role, roles_enabled
from .permissions import is_admin

MAX_TITLE = 30
# popular titles get their own emoji; any other text is accepted with a generic one
EMOJI = {
    "староста": "👑", "зам старосты": "🛡", "заместитель": "🛡", "зам": "🛡", "физорг": "🏃",
    "дежурный": "🧹", "ведущий": "🎤", "редактор": "✏️", "шут": "🃏", "мем-мастер": "😂",
    "казначей": "💰", "фотограф": "📸", "модератор": "🔧", "душа компании": "🎉",
    "ботаник": "🤓", "сова": "🦉", "жаворонок": "🐓",
}
SUGGESTIONS = ["староста", "зам старосты", "физорг", "дежурный", "ведущий", "шут", "мем-мастер", "казначей"]


def clean_title(text: str) -> str:
    return " ".join((text or "").split())[:MAX_TITLE]


def role_label(title: str) -> str:
    return f"{EMOJI.get(title.lower(), '🏷')} {title}"


def user_role(chat_id: int, user_id: int):
    """The user's title in this group, or None (also None when roles are off)."""
    if not roles_enabled(chat_id):
        return None
    return get_group_role(chat_id, user_id)


async def is_group_admin(bot, chat_id: int, user_id: int) -> bool:
    """Telegram admin/creator of the group, or a bot admin."""
    if is_admin(user_id):
        return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except Exception:
        return False
    return member.status in ("administrator", "creator")
