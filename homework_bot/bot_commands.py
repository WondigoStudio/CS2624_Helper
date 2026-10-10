"""The command menu shown by Telegram (the "/" button), kept in sync with the
code: set automatically at startup, so it no longer has to be edited by hand
in BotFather."""

from telegram import BotCommand, BotCommandScopeChat

# (command, description <= 256 chars, shown to everyone)
_USER = [
    ("start", "Начать работу с ботом, список команд"),
    ("app", "Открыть мини-приложение"),
    ("today", "Задания на сегодня"),
    ("week", "Задания на эту неделю"),
    ("all", "Все предстоящие задания"),
    ("calendar", "Календарь месяца с цветными днями"),
    ("add", "Добавить домашнее задание (общее для всех)"),
    ("done", "Отметить задание выполненным (только у тебя)"),
    ("edittask", "Изменить задание (предмет/текст/дату/время/файлы)"),
    ("delete", "Удалить задание"),
    ("taskfile", "Показать вложения задания"),
    ("schedule", "Моё расписание на сегодня"),
    ("schedule_week", "Моё расписание на всю неделю"),
    ("schedule_day", "Расписание на выбранный день + фото кабинета"),
    ("schedule_add", "Добавить пару в расписание"),
    ("editschedule", "Изменить пару (день/предмет/время/кабинет)"),
    ("schedule_delete", "Удалить пару из расписания"),
    ("copyschedule", "Скопировать расписание (своё или чужое) другому человеку"),
    ("addroomphoto", "Прикрепить фото кабинета"),
    ("roomphotos", "Список кабинетов с сохранённым фото"),
    ("testphoto", "Проверить сохранённое фото кабинета"),
    ("testmorning", "Проверить утреннюю рассылку сейчас"),
    ("lms", "Подключить календарь LMS: дедлайны сами попадут в задания"),
    ("lmssync", "Обновить дедлайны из LMS сейчас"),
    ("lmsoff", "Отключить календарь LMS"),
    ("weather", "Погода сейчас (по умолчанию Астана, можно: /weather Алматы)"),
    ("remind", "Добавить личное напоминание"),
    ("reminders", "Список моих напоминаний"),
    ("addbirthday", "Добавить день рождения"),
    ("importbirthdays", "Добавить дни рождения списком"),
    ("birthdays", "Список дней рождения"),
    ("nextbirthday", "Ближайший день рождения"),
    ("call", "Созвать участников группы"),
    ("set_report", "Вкл/выкл утренний опрос в группе"),
    ("sethere", "В группе с топиками: слать рассылки в этот топик (админам группы)"),
    ("topactions", "Топ весёлых действий в чате"),
    ("cancel", "Отменить текущее действие"),
]

# only for bot admins (shown in their private chat with the bot)
_ADMIN = [
    ("permissions", "Права: кому можно задания, расписание, LMS"),
    ("users", "Список пользователей"),
    ("viewschedule", "Посмотреть чьё-то расписание"),
    ("lmsusers", "Кто подключил календарь LMS"),
    ("exportdb", "Скачать бэкап базы файлом"),
    ("dbstatus", "Какая база сейчас активна"),
    ("backupnow", "Сделать бэкап в резервные базы сейчас"),
]


def _build(rows):
    return [BotCommand(c, d[:256]) for c, d in rows]


async def sync_commands(bot, admin_ids):
    """Default menu for everybody + the extended menu for each admin."""
    await bot.set_my_commands(_build(_USER))
    for admin_id in admin_ids:
        try:
            await bot.set_my_commands(_build(_USER + _ADMIN), scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception:
            pass  # the admin has never opened the bot yet — the shorter menu still works
