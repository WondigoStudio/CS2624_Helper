"""Admin-only diagnostic commands: /users, /viewschedule, /testmorning
(fires the adaptive morning schedule send immediately, for testing),
/dbstatus and /backupnow (multi-database failover status/control)."""

import asyncio
import html

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ..config import DATABASE_URL_BACKUP2, DATABASE_URL_BACKUP3
from ..constants import WEEKDAY_EMOJI, WEEKDAY_NAMES_FULL_RU
from ..activity import describe, flush_now
from ..db import (
    activity_stats,
    recent_activity,
    display_name,
    export_all_tables,
    get_chat_info,
    get_db_status,
    get_lessons,
    init_db,
    list_known_users,
    list_lms_feeds,
    mirror_active_db_to,
    register_chat,
)
from ..formatting import format_lessons_block
from ..jobs import send_morning_schedule_for_chat
from ..permissions import is_admin
from ..utils import _chunk_text, now_kz


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




async def lmsusers_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: who has connected an LMS calendar. Shows names and sync status
    only — the links (they hold personal tokens) are never displayed."""
    register_chat(update)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Эта команда доступна только администраторам бота.")
        return
    feeds = list_lms_feeds()
    if not feeds:
        await update.message.reply_text("Пока никто не подключил календарь LMS (/lms).")
        return
    names = {r["chat_id"]: display_name(r) for r in list_known_users()}
    lines = []
    for f in feeds:
        who = names.get(f["user_id"], f"id{f['user_id']}")
        last = (f["last_sync"] or "—")[:16].replace("T", " ")
        mark = "⚠️ ссылка не работает" if f["error_notified"] else "✅"
        lines.append(f"• {who} — {mark}, обновлено {last}")
    await update.message.reply_text(f"Подключили календарь LMS ({len(feeds)}):\n" + "\n".join(lines))


async def exportdb_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: download the whole database as a JSON file (private chat only)."""
    import io
    import json

    register_chat(update)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Эта команда доступна только администраторам бота.")
        return
    if update.effective_chat.type != "private":
        await update.message.reply_text("Бэкап содержит данные всех пользователей — запроси его в личке с ботом.")
        return
    data = await asyncio.to_thread(export_all_tables)
    payload = json.dumps(
        {"exported_at": now_kz().isoformat(), "tables": data}, ensure_ascii=False, indent=1, default=str
    ).encode("utf-8")
    buf = io.BytesIO(payload)
    buf.name = f"homework_bot_backup_{now_kz().strftime('%Y-%m-%d_%H-%M')}.json"
    counts = ", ".join(f"{t}: {len(r)}" for t, r in data.items() if r)
    await update.message.reply_document(buf, caption=f"💾 Бэкап базы. Строк по таблицам — {counts}"[:1000])


def _fmt_ts(ts: str) -> str:
    try:
        return f"{ts[8:10]}.{ts[5:7]} {ts[11:16]}"
    except Exception:
        return ts or "—"


async def activity_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin: who did what. /activity — latest actions; /activity 80 — more;
    /activity <имя, @username или id> — one person; /activity stats — who is
    most active. Only commands and button presses are recorded, never the
    text of messages."""
    register_chat(update)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Эта команда доступна только администраторам бота.")
        return
    await asyncio.to_thread(flush_now)
    users = list_known_users()
    names = {r["chat_id"]: display_name(r) for r in users}
    arg = " ".join(context.args).strip() if context.args else ""

    if arg.lower() in ("stats", "stat", "top", "топ", "статистика"):
        now = now_kz()
        parts = []
        for label, delta in (("за 24 часа", 1), ("за 7 дней", 7)):
            from datetime import timedelta
            rows = await asyncio.to_thread(activity_stats, (now - timedelta(days=delta)).isoformat(timespec="seconds"))
            lines = [f"📊 Активность {label}:"]
            for r in rows[:15]:
                lines.append(f"• {names.get(r['user_id'], 'id' + str(r['user_id']))} — {r['c']} (последнее {_fmt_ts(r['last_ts'])})")
            parts.append("\n".join(lines) if rows else f"📊 Активность {label}: пока пусто")
        await update.message.reply_text("\n\n".join(parts))
        return

    limit, user_id, title = 40, None, "Последние действия"
    if arg:
        if arg.isdigit() and int(arg) < 1000:
            limit = max(1, min(int(arg), 150))
        else:
            needle = arg.lstrip("@").lower()
            match = None
            for r in users:
                if str(r["chat_id"]) == needle or needle in display_name(r).lower():
                    match = r
                    break
            if match is None:
                await update.message.reply_text("Не нашёл такого человека. Напиши имя, @username или id.")
                return
            user_id = match["chat_id"]
            title = f"Действия: {display_name(match)}"
    rows = await asyncio.to_thread(recent_activity, limit, user_id)
    lines = []
    for r in rows:
        action = describe(r["kind"], r["name"])
        if not action:
            continue
        where = "личка" if r["chat_type"] == "private" else (r["chat_title"] or "группа")
        who = "" if user_id is not None else f"{names.get(r['user_id'], 'id' + str(r['user_id']))} · "
        lines.append(f"{_fmt_ts(r['ts'])} {who}{where} · {action}")
    if not lines:
        await update.message.reply_text("Пока ничего не записано.")
        return
    text = f"🧾 {title} ({len(lines)}):\n" + "\n".join(lines)
    for chunk in _chunk_text(text):
        await update.message.reply_text(chunk)
