"""The persistent button menu (reply keyboard) shown in private chats.

Only labels and keyboards live here (no handler imports), so any module can use them
without import cycles. The router that maps a label to an action is handlers/menu.py.
"""

import re

from telegram import KeyboardButton, ReplyKeyboardMarkup
from telegram.ext import filters

# --- main menu -------------------------------------------------------------------------
TASKS = "📝 Задания"
SCHEDULE = "🗓 Расписание"
CALENDAR = "📆 Календарь"
GRADES = "🎓 Оценки и GPA"
REMINDERS = "⏰ Напоминания"
BIRTHDAYS = "🎂 Дни рождения"
APP = "📱 Приложение"
WEATHER = "🌤 Погода"
HELP = "ℹ️ Помощь"
HIDE = "⌨️ Скрыть меню"
BACK = "⬅️ Главное меню"

# --- tasks ------------------------------------------------------------------------------
T_ADD = "➕ Добавить задание"
T_TODAY = "📋 Задания на сегодня"
T_WEEK = "🗓 Задания на неделю"
T_ALL = "📚 Все задания"
T_DONE = "✅ Отметить выполненным"
T_EDIT = "✏️ Изменить задание"
T_DELETE = "🗑 Удалить задание"
T_FILES = "📎 Вложения заданий"
T_LMS = "🔗 Подключить LMS"

# --- schedule ---------------------------------------------------------------------------
S_TODAY = "🕘 Пары сегодня"
S_WEEK = "📅 Пары на неделю"
S_DAY = "🔎 Пары на день"
S_ADD = "➕ Добавить пару"
S_EDIT = "✏️ Изменить пару"
S_DELETE = "🗑 Удалить пару"
S_PHOTOS = "🖼 Фото кабинетов"
S_COPY = "📤 Копировать расписание"

# --- reminders / birthdays ----------------------------------------------------------------
R_ADD = "➕ Новое напоминание"
R_LIST = "📋 Мои напоминания"
B_ADD = "🎂 Добавить день рождения"
B_NEXT = "🎉 Ближайший день рождения"
B_ALL = "📜 Все дни рождения"
B_IMPORT = "📥 Импорт списком"

# --- grades / GPA -------------------------------------------------------------------------
G_FINAL = "🎯 Итоговая оценка"
G_CALC = "🧮 Калькулятор GPA"
G_CUM = "📈 Кумулятивный GPA"
G_CONV = "🔄 Конвертер GPA"
G_ATT = "🏫 Посещаемость"
G_SCALE = "📊 Шкала оценок"
G_SYL = "📋 Силлабусы"
G_HELP = "📖 Инструкция"
G_RISK = "⚠️ Риски: FX и ретейк"

# labels that open a ConversationHandler (they are its entry points, see main.py)
CONVERSATION_LABELS = (T_ADD, T_EDIT, T_LMS, S_ADD, S_EDIT, R_ADD, B_ADD, B_IMPORT)

# the reply keyboards of every section
_ROWS = {
    "main": [[TASKS, SCHEDULE, CALENDAR], [GRADES, REMINDERS, BIRTHDAYS], [APP, WEATHER, HELP], [HIDE]],
    "tasks": [[T_ADD, T_TODAY], [T_WEEK, T_ALL], [T_DONE, T_EDIT], [T_DELETE, T_FILES], [T_LMS], [BACK]],
    "schedule": [[S_TODAY, S_WEEK], [S_DAY, S_ADD], [S_EDIT, S_DELETE], [S_PHOTOS, S_COPY], [BACK]],
    "reminders": [[R_ADD, R_LIST], [BACK]],
    "birthdays": [[B_ADD, B_NEXT], [B_ALL, B_IMPORT], [BACK]],
    "grades": [[G_FINAL, G_CALC], [G_CUM, G_CONV], [G_ATT, G_RISK], [G_SCALE, G_SYL], [G_HELP, BACK]],
}


def keyboard(section: str = "main") -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(label) for label in row] for row in _ROWS[section]]
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, is_persistent=True)


def all_labels() -> list:
    seen = []
    for rows in _ROWS.values():
        for row in rows:
            for label in row:
                if label not in seen:
                    seen.append(label)
    return seen


def labels_pattern(labels) -> str:
    return "^(?:" + "|".join(re.escape(x) for x in labels) + ")$"


# matches any menu label (used to keep menu taps out of "type your answer" steps)
MENU_TEXT = filters.Regex(labels_pattern(all_labels()))


def entry(label: str):
    """Filter for one label — the menu twin of CommandHandler("add") as a conversation entry point."""
    return filters.Regex(labels_pattern([label])) & filters.ChatType.PRIVATE

