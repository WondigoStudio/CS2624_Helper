"""Admin-only diagnostic commands: /users, /viewschedule, and /testmorning
(fires the adaptive morning schedule send immediately, for testing)."""

import html

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ..config import DATABASE_URL_BACKUP2, DATABASE_URL_BACKUP3
from ..constants import WEEKDAY_EMOJI, WEEKDAY_NAMES_FULL_RU
from ..db import display_name, get_chat_info, get_db_status, get_lessons, list_known_users, register_chat
from ..formatting import format_lessons_block
from ..jobs import send_morning_schedule_for_chat
from ..permissions import is_admin
from ..utils import now_kz


async def dbstatus_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "Эта команда доступна только администраторам бота."
        )
        return
    status = get_db_status()
    if not status["using_postgres"]:
        await update.message.reply_text("База — локальный SQLite-файл, резервирование не настроено.")
        return
    labels = ["основная (DATABASE_URL)", "резервная №2", "резервная №3"]
    active_label = labels[status["active_index"]] if status["active_index"] < len(labels) else "?"
    backups = []
    if DATABASE_URL_BACKUP2:
        backups.append("№2 настроена")
    if DATABASE_URL_BACKUP3:
        backups.append("№3 настроена")
    backups_text = ", ".join(backups) if backups else "не настроены"
    warning = ""
    if status["active_index"] != 0:
        warning = (
            "\n⚠️ Сейчас работаем не на основной базе — это значит основная была "
            "недоступна. Данные, записанные с момента переключения, нужно будет "
            "вручную перенести обратно, когда основная снова заработает."
        )
    await update.message.reply_text(
        f"Сейчас активна: {active_label}\nВсего баз в цепочке: {status['configured_count']}\n"
        f"Резервные базы: {backups_text}{warning}"
    )


async def users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "Эта команда доступна только администраторам бота."
        )
        return
    rows = list_known_users()
    if not rows:
        await update.message.reply_text("Пока никто не писал боту.")
        return
    lines = [f"• {display_name(r)} — chat_id {r['chat_id']}" for r in rows]
    await update.message.reply_text("Известные пользователи:\n" + "\n".join(lines))


async def viewschedule_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "Эта команда доступна только администраторам бота."
        )
        return
    rows = list_known_users()
    if not rows:
        await update.message.reply_text("Пока никто не писал боту.")
        return
    buttons = [
        [InlineKeyboardButton(display_name(r), callback_data=f"viewsch:{r['chat_id']}")]
        for r in rows
    ]
    await update.message.reply_text(
        "Чьё расписание посмотреть?", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def viewschedule_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        await query.edit_message_text("Эта команда доступна только администраторам бота.")
        return

    target_chat_id = int(query.data.split(":", 1)[1])
    info = get_chat_info(target_chat_id)
    name = display_name(info) if info else f"chat_id {target_chat_id}"

    rows = get_lessons(target_chat_id)
    if not rows:
        await query.edit_message_text(f"У «{name}» расписание пока пустое.")
        return

    by_day = {i: [] for i in range(7)}
    for r in rows:
        by_day[r["weekday"]].append(r)
    blocks = [f"🗓 <b>Расписание «{html.escape(name)}»</b>"]
    for i in range(7):
        if not by_day[i]:
            continue
        heading = f"{WEEKDAY_EMOJI[i]} {WEEKDAY_NAMES_FULL_RU[i]}"
        blocks.append(format_lessons_block(by_day[i], heading))

    await query.edit_message_text("\n\n".join(blocks), parse_mode=ParseMode.HTML)


async def testmorning_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    sent = await send_morning_schedule_for_chat(context.bot, update.effective_chat.id)
    if not sent:
        weekday = now_kz().weekday()
        await update.message.reply_text(
            f"На {WEEKDAY_NAMES_FULL_RU[weekday].lower()} в расписании пар нет — "
            "поэтому утренняя рассылка ничего бы не отправила. "
            "Добавь пару через /schedule_add и попробуй снова."
        )
