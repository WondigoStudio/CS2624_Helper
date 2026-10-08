"""Admin-only diagnostic commands: /users, /viewschedule, /testmorning
(fires the adaptive morning schedule send immediately, for testing),
/dbstatus and /backupnow (multi-database failover status/control)."""

import asyncio
import html

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ..config import DATABASE_URL_BACKUP2, DATABASE_URL_BACKUP3, LMS_ICAL_URL
from ..constants import WEEKDAY_EMOJI, WEEKDAY_NAMES_FULL_RU
from ..db import (
    display_name,
    get_chat_info,
    get_db_status,
    get_lessons,
    init_db,
    list_known_users,
    mirror_active_db_to,
    register_chat,
)
from ..formatting import format_lessons_block
from ..jobs import send_morning_schedule_for_chat
from ..lms_sync import sync_lms
from ..permissions import is_admin
from ..utils import now_kz


async def lmssync_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pulls LMS deadlines right now and reports what changed."""
    register_chat(update)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Эта команда доступна только администраторам бота.")
        return
    if not LMS_ICAL_URL:
        await update.message.reply_text(
            "Ссылка на календарь LMS не задана. Добавь переменную LMS_ICAL_URL "
            "(ссылка из LMS → Calendar → Export calendar → Get calendar URL) и перезапусти бота."
        )
        return
    status = await update.message.reply_text("⏳ Забираю дедлайны из LMS…")
    try:
        report = await asyncio.to_thread(sync_lms)
    except Exception as e:
        await status.edit_text(f"❌ Не получилось: {e}")
        return
    lines = [f"✅ Готово. Событий в календаре: {report['events']}"]
    if report["added"]:
        lines.append(f"\n➕ Добавлено ({len(report['added'])}):")
        lines += [f"• {t}" for t in report["added"][:25]]
        if len(report["added"]) > 25:
            lines.append(f"…и ещё {len(report['added']) - 25}")
    if report["updated"]:
        lines.append(f"\n🔄 Обновлено ({len(report['updated'])}):")
        lines += [f"• {t}" for t in report["updated"][:15]]
    if not report["added"] and not report["updated"]:
        lines.append("Новых и изменённых дедлайнов нет.")
    if report["unknown_subject"]:
        lines.append("\n⚠️ Не знаю предмет для: " + "; ".join(report["unknown_subject"][:5]))
    await status.edit_text("\n".join(lines))


async def backupnow_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Runs the mirror-to-backup-DB(s) immediately instead of waiting for
    the scheduled job — mainly useful right after setting up
    DATABASE_URL_BACKUP2/3 for the first time, to seed them with the
    existing data straight away rather than waiting up to 6h/overnight."""
    register_chat(update)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(
            "Эта команда доступна только администраторам бота."
        )
        return
    status = get_db_status()
    if not status["using_postgres"]:
        await update.message.reply_text("База — локальный SQLite-файл, переносить некуда.")
        return
    targets = [("резервную №2", DATABASE_URL_BACKUP2), ("резервную №3", DATABASE_URL_BACKUP3)]
    targets = [(label, url) for label, url in targets if url]
    if not targets:
        await update.message.reply_text(
            "DATABASE_URL_BACKUP2/3 не заданы — нечего заполнять."
        )
        return
    status_msg = await update.message.reply_text("⏳ Переношу данные…")
    results = []
    for label, url in targets:
        try:
            await asyncio.to_thread(init_db, url)
            ok = await asyncio.to_thread(mirror_active_db_to, url)
            results.append(f"✅ {label} — готово" if ok else f"⚠️ {label} — пропущено")
        except Exception as e:
            results.append(f"❌ {label} — ошибка: {e}")
    await status_msg.edit_text("\n".join(results))


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
