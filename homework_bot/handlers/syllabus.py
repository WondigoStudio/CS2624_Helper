"""/syllabus — short, practical facts for every course (grading scheme,
deadlines, attendance rules). The score calculator lives in the Mini App."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ..config import WEBAPP_URL
from ..constants import SUBJECT_EMOJI, SUBJECTS
from ..db import register_chat
from ..syllabi import SYLLABI, card_text


def _menu_keyboard() -> InlineKeyboardMarkup:
    rows, row = [], []
    for code, name in SUBJECTS:
        if code not in SYLLABI:
            continue
        row.append(InlineKeyboardButton(f"{SUBJECT_EMOJI.get(code, '📘')} {name}", callback_data=f"syl:{code}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def _card_keyboard() -> InlineKeyboardMarkup:
    rows = []
    if WEBAPP_URL:
        rows.append([InlineKeyboardButton("🎓 Калькулятор баллов", web_app=WebAppInfo(url=WEBAPP_URL))])
    rows.append([InlineKeyboardButton("← Все предметы", callback_data="syl:menu")])
    return InlineKeyboardMarkup(rows)


async def syllabus_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    await update.message.reply_text("Выбери предмет — покажу кратко главное из силлабуса 📋", reply_markup=_menu_keyboard())


async def syllabus_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    code = query.data.split(":", 1)[1]
    if code == "menu":
        await query.edit_message_text("Выбери предмет — покажу кратко главное из силлабуса 📋", reply_markup=_menu_keyboard())
        return
    if code not in SYLLABI:
        return
    # web_app buttons only work in private chats; groups get the plain back button
    kb = _card_keyboard() if query.message and query.message.chat.type == "private" else InlineKeyboardMarkup(
        [[InlineKeyboardButton("← Все предметы", callback_data="syl:menu")]]
    )
    await query.edit_message_text(card_text(code), parse_mode=ParseMode.HTML, reply_markup=kb)
