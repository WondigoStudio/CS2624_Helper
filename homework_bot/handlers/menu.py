"""Router for the persistent button menu (see menu_kb.py).

Buttons that open a section swap the keyboard; the rest call the same function as the
matching /command. The buttons that start a multi-step dialog (add a task, add a lesson…)
are wired as extra entry points of those ConversationHandlers in main.py instead.
"""

from telegram import ReplyKeyboardRemove, Update
from telegram.ext import ContextTypes, MessageHandler, filters

from .. import menu_kb as K
from ..db import register_chat
from . import gpa as gpa_h
from .birthdays import birthdays_cmd, nextbirthday_cmd
from .calendar import calendar_cmd
from .reminders import reminders_cmd
from .roomphotos import roomphotos_cmd
from .schedule import (
    copyschedule_cmd, schedule_day_start, schedule_delete_cmd, schedule_today_cmd, schedule_week_cmd,
)
from .start import app_cmd, start
from .syllabus import syllabus_cmd
from .tasks import all_cmd, delete_cmd, done_cmd, taskfile_cmd, today_cmd, week_cmd
from .weather import weather_cmd

_SECTION_TEXT = {
    "main": "Главное меню 👇",
    "tasks": "📝 Задания — выбирай действие 👇",
    "schedule": "🗓 Расписание — выбирай действие 👇",
    "reminders": "⏰ Напоминания — выбирай действие 👇",
    "birthdays": "🎂 Дни рождения — выбирай действие 👇",
    "grades": "🎓 Оценки и GPA — выбирай инструмент 👇",
}


def _section(name: str):
    async def show(update: Update, context: ContextTypes.DEFAULT_TYPE):
        register_chat(update)
        await update.message.reply_text(_SECTION_TEXT[name], reply_markup=K.keyboard(name))
    return show


async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/menu — show the main button menu (private chats only; groups would show it to everyone)."""
    register_chat(update)
    if update.effective_chat.type != "private":
        await update.message.reply_text("Кнопочное меню работает в личке с ботом.")
        return
    context.user_data.pop("gpa", None)
    await update.message.reply_text(_SECTION_TEXT["main"], reply_markup=K.keyboard("main"))


async def gpa_section_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/gpa — open the grades & GPA section (also works in groups: the tools answer in the chat)."""
    register_chat(update)
    context.user_data.pop("gpa", None)
    private = update.effective_chat.type == "private"
    await update.message.reply_text(
        _SECTION_TEXT["grades"] if private else
        "🎓 Калькулятор GPA, конвертер и посещаемость работают в личке с ботом — напиши мне и нажми /gpa.",
        reply_markup=K.keyboard("grades") if private else None,
    )


async def hide_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("gpa", None)
    await update.message.reply_text("Меню скрыто. Вернуть — команда /menu.", reply_markup=ReplyKeyboardRemove())


async def _main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await menu_cmd(update, context)


async def _help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


async def _busy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # only reached if a dialog button is pressed where its ConversationHandler did not take it
    await update.message.reply_text("Эта кнопка работает в личке с ботом.")


ACTIONS = {
    K.BACK: _main,
    K.HIDE: hide_menu,
    K.TASKS: _section("tasks"),
    K.SCHEDULE: _section("schedule"),
    K.GRADES: _section("grades"),
    K.REMINDERS: _section("reminders"),
    K.BIRTHDAYS: _section("birthdays"),
    K.CALENDAR: calendar_cmd,
    K.APP: app_cmd,
    K.WEATHER: weather_cmd,
    K.HELP: _help,
    K.T_TODAY: today_cmd,
    K.T_WEEK: week_cmd,
    K.T_ALL: all_cmd,
    K.T_DONE: done_cmd,
    K.T_DELETE: delete_cmd,
    K.T_FILES: taskfile_cmd,
    K.S_TODAY: schedule_today_cmd,
    K.S_WEEK: schedule_week_cmd,
    K.S_DAY: schedule_day_start,
    K.S_DELETE: schedule_delete_cmd,
    K.S_PHOTOS: roomphotos_cmd,
    K.S_COPY: copyschedule_cmd,
    K.R_LIST: reminders_cmd,
    K.B_NEXT: nextbirthday_cmd,
    K.B_ALL: birthdays_cmd,
    K.G_FINAL: gpa_h.final_start,
    K.G_CALC: gpa_h.calc_start,
    K.G_CUM: gpa_h.cum_start,
    K.G_CONV: gpa_h.conv_start,
    K.G_ATT: gpa_h.att_start,
    K.G_RISK: gpa_h.risk_cmd,
    K.G_SCALE: gpa_h.scale_cmd,
    K.G_SYL: syllabus_cmd,
    K.G_HELP: gpa_h.help_cmd,
}
for _label in K.CONVERSATION_LABELS:
    ACTIONS.setdefault(_label, _busy)

# every label must have an action, otherwise tapping the button would silently do nothing
_MISSING = [x for x in K.all_labels() if x not in ACTIONS]
assert not _MISSING, f"menu buttons without an action: {_MISSING}"


async def menu_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    action = ACTIONS.get(text)
    if action is None:
        return
    # any menu tap ends an open GPA "send me numbers" mode (the GPA buttons start theirs again)
    context.user_data.pop("gpa", None)
    await action(update, context)


def menu_handler() -> MessageHandler:
    return MessageHandler(K.MENU_TEXT & filters.ChatType.PRIVATE, menu_router)


async def _reset_gpa_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("gpa", None)
    context.user_data.pop("vac_wait", None)


def gpa_reset_handler() -> MessageHandler:
    """Runs before everything else (group -1): any command or menu tap closes an open GPA input mode,
    including the taps that start a dialog (those never reach the router)."""
    return MessageHandler((K.MENU_TEXT | filters.COMMAND) & filters.ChatType.PRIVATE, _reset_gpa_mode)

