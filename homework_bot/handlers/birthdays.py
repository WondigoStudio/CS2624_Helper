"""Birthday tracker: /addbirthday (add yourself or someone else via a short
conversation), /importbirthdays (bulk-add by pasting a whole list at once),
/birthdays (full list, nearest first, with delete and a period filter —
week / 2 weeks / month / all) and /nextbirthday (just the single nearest
one)."""

import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from ..db import (
    add_birthday,
    delete_birthday,
    display_name,
    get_birthday,
    get_birthdays,
    list_known_users,
    register_chat,
)
from ..formatting import format_birthday_line
from ..keyboards import birthday_target_keyboard
from ..permissions import is_admin
from ..states import BDAY_DATE, BDAY_IMPORT, BDAY_TARGET
from ..utils import next_birthday_date, parse_due_date, reply_text_chunked, today_kz


async def addbirthday_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    await update.message.reply_text(
        "Чей день рождения добавляем?", reply_markup=birthday_target_keyboard("bdaytarget")
    )
    return BDAY_TARGET


async def addbirthday_target_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data.split(":", 1)[1]
    if choice == "self":
        user = update.effective_user
        context.user_data["bday_user_id"] = user.id
        context.user_data["bday_name"] = user.first_name or (
            f"@{user.username}" if user.username else f"id{user.id}"
        )
    else:
        target_id = int(choice)
        row = next((u for u in list_known_users() if u["chat_id"] == target_id), None)
        context.user_data["bday_user_id"] = target_id
        context.user_data["bday_name"] = display_name(row) if row else f"id{target_id}"
    await query.edit_message_text(
        f"Добавляю: {context.user_data['bday_name']}\n\n"
        "Напиши дату рождения в формате ДД.ММ или ДД.ММ.ГГГГ (год — по желанию, "
        "чтобы показывать возраст)."
    )
    return BDAY_DATE


async def addbirthday_date_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = parse_due_date(update.message.text)
    if d is None:
        await update.message.reply_text(
            "Не понял дату. Попробуй ещё раз, например: 05.10 или 05.10.2008"
        )
        return BDAY_DATE
    text = update.message.text.strip()
    # parse_due_date fills in the current year when none was typed — only
    # keep the year if the person actually typed one (3+ dot-separated parts).
    year = d.year if text.count(".") == 2 else None
    user_id = context.user_data.pop("bday_user_id")
    name = context.user_data.pop("bday_name")
    add_birthday(
        user_id, update.effective_chat.id, name, d.day, d.month,
        update.effective_user.id, year=year,
    )
    await update.message.reply_text(
        f"Готово ✅ Запомнил день рождения: {name} — {d.day:02d}.{d.month:02d}"
        + (f".{year}" if year else "")
    )
    return ConversationHandler.END


async def addbirthday_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END


# --- Bulk import (/importbirthdays) ----------------------------------
# Accepts a whole pasted list at once — one entry per line, in whatever
# loose shape it comes in (a copy-pasted table row, "Имя ДД.ММ.ГГГГ", a
# bare "Имя ДД.ММ", tab- or space-separated). For each line: the last
# date-shaped token is the birthday, the name is whatever text sits right
# before it (for a 4-column table row like
# "<timestamp>\t<имя>\t<username>\t<дата>" that's the 2nd column, not the
# username). Lines that are just a bare number (row indices from a pasted
# table) or that don't contain a recognizable date are skipped and listed
# back so nothing is silently lost. The year, if present, is NOT stored as
# a birth year — in a pasted "nearest occurrence" table it's this year's
# date, not the person's birth year, so keeping it would show a bogus age.
_BULK_DATE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?\s*$")


def _parse_bulk_line(line: str):
    line = line.strip()
    if not line or line.isdigit():
        return None
    m = _BULK_DATE_RE.search(line)
    if not m:
        return None
    day, month = int(m.group(1)), int(m.group(2))
    if not (1 <= day <= 31 and 1 <= month <= 12):
        return None
    before = line[:m.start()].strip()
    parts = [p.strip() for p in before.split("\t") if p.strip()]
    if not parts:
        parts = [p.strip() for p in re.split(r"\s{2,}", before) if p.strip()]
    if len(parts) >= 3:
        # timestamp \t имя \t username [\t ...]  — name is the 2nd column
        name = parts[1]
    elif parts:
        name = parts[-1] if len(parts) == 1 else parts[0]
    else:
        return None
    if not name:
        return None
    return name, day, month


async def importbirthdays_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    await update.message.reply_text(
        "Пришли список одним сообщением — по одному человеку на строку, как в "
        "твоей таблице. В каждой строке найду имя и дату (ДД.ММ или "
        "ДД.ММ.ГГГГ); год рождения не запоминаю, беру только день и месяц. "
        "/cancel — отменить."
    )
    return BDAY_IMPORT


async def importbirthdays_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    added_by = update.effective_user.id
    added, skipped = [], []
    for line in update.message.text.splitlines():
        parsed = _parse_bulk_line(line)
        if parsed is None:
            if line.strip():
                skipped.append(line.strip())
            continue
        name, day, month = parsed
        add_birthday(0, chat_id, name, day, month, added_by)
        added.append(f"{name} — {day:02d}.{month:02d}")
    report = f"Готово ✅ Добавлено: {len(added)}\n" + "\n".join(added) if added else \
        "Не добавил ни одной записи — не нашёл дат в строках."
    if skipped:
        report += f"\n\n⚠️ Не распознал {len(skipped)} строк(и):\n" + "\n".join(skipped)
    await reply_text_chunked(update.message, report)
    return ConversationHandler.END


async def importbirthdays_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END


# Period filters for /birthdays — key is either a day count or "all".
_BIRTHDAY_FILTERS = [("7", "Неделя"), ("14", "2 недели"), ("30", "Месяц"), ("all", "Все")]


def _sorted_birthdays(days: int = None, today=None):
    if today is None:
        today = today_kz()
    rows = get_birthdays()
    rows = sorted(rows, key=lambda r: next_birthday_date(r["day"], r["month"], today))
    if days is not None:
        rows = [
            r for r in rows
            if (next_birthday_date(r["day"], r["month"], today) - today).days <= days
        ]
    return rows


def _birthdays_keyboard(rows, active_key: str) -> InlineKeyboardMarkup:
    filter_row = [
        InlineKeyboardButton(
            ("• " if key == active_key else "") + label, callback_data=f"bdayfilter:{key}"
        )
        for key, label in _BIRTHDAY_FILTERS
    ]
    buttons = [filter_row] + [
        [InlineKeyboardButton(f"🗑 Удалить #{r['id']}", callback_data=f"bdaydel:{r['id']}")]
        for r in rows
    ]
    return InlineKeyboardMarkup(buttons)


def _birthdays_text(rows, active_key: str) -> str:
    if not rows:
        if active_key == "all":
            return "Дни рождения пока не добавлены. Добавь через /addbirthday."
        label = dict(_BIRTHDAY_FILTERS)[active_key]
        return f"За период «{label}» дней рождения нет."
    return "🎂 Дни рождения:\n\n" + "\n".join(format_birthday_line(r) for r in rows)


async def birthdays_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    rows = _sorted_birthdays()
    await reply_text_chunked(
        update.message, _birthdays_text(rows, "all"),
        reply_markup=_birthdays_keyboard(rows, "all"),
    )


async def birthday_filter_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    key = query.data.split(":", 1)[1]
    days = None if key == "all" else int(key)
    rows = _sorted_birthdays(days=days)
    try:
        await query.edit_message_text(
            _birthdays_text(rows, key), reply_markup=_birthdays_keyboard(rows, key)
        )
    except Exception:
        pass


async def nextbirthday_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    rows = _sorted_birthdays()
    if not rows:
        await update.message.reply_text(
            "Дни рождения пока не добавлены. Добавь через /addbirthday."
        )
        return
    await update.message.reply_text(f"🎂 Ближайший день рождения:\n{format_birthday_line(rows[0])}")


async def birthday_delete_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    birthday_id = int(query.data.split(":")[1])
    row = get_birthday(birthday_id)
    if not row:
        await query.edit_message_text("Уже удалено.")
        return
    if row["added_by"] != update.effective_user.id and not is_admin(update.effective_user.id):
        await query.answer("Это не ты добавлял.", show_alert=True)
        return
    delete_birthday(birthday_id)
    await query.edit_message_text("Удалено 🗑")
