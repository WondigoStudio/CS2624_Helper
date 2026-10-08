"""Background jobs run on the PTB JobQueue: the adaptive morning
schedule/task reminders, per-lesson heads-up pings, and the morning poll."""

import asyncio
import html
import sqlite3
from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from .config import DATABASE_URL_BACKUP2, DATABASE_URL_BACKUP3, LMS_ICAL_URL, SHARED_TASKS_ID, logger
from .constants import SUBJECT_NAME, WEEKDAY_NAMES_FULL_RU
from .lms_sync import sync_lms
from .db import (
    all_chat_ids,
    bump_reminder_nag,
    db,
    get_all_enabled_reminders,
    get_lessons,
    get_reminders_awaiting_confirmation,
    get_room_photo,
    get_tasks,
    init_db,
    list_known_users,
    mark_reminder_fired,
    mark_task_deadline_notified,
    mirror_active_db_to,
    users_who_finished,
    viewer_for_chat,
)
from .formatting import format_lessons_block, format_task_line
from .states import LESSON_REMINDER_MINUTES
from .utils import (
    DEFAULT_SCHEDULE_TIME,
    DEFAULT_TASKS_TIME,
    SCHEDULE_OFFSET_MINUTES,
    TASKS_OFFSET_MINUTES,
    _chunk_text,
    _time_minus_minutes,
    now_kz,
    task_due_datetime,
    today_kz,
)


async def send_morning_poll_job(context: ContextTypes.DEFAULT_TYPE):
    # 1. Сначала подготавливаем данные из базы
    conn = db()
    conn.row_factory = sqlite3.Row
    chat_ids = []
    group_members_map = {}

    try:
        # Выбираем только те чаты, где утренний опрос включен (enabled = 1)
        rows = conn.execute(
            "SELECT chat_id FROM report_chats WHERE enabled = 1"
        ).fetchall()
        chat_ids = [r["chat_id"] for r in rows]

        # Лог для проверки
        logger.info(
            f"[POLL_DEBUG] Найдено чатов в report_chats: {len(chat_ids)} -> {chat_ids}"
        )

        if not chat_ids:
            logger.warning(
                "[POLL_DEBUG] Список chat_ids пуст! Вызовите /setreport в группе."
            )
            return

        # Для каждого чата забираем сохраненных участников
        for cid in chat_ids:
            members = conn.execute(
                "SELECT user_id, first_name FROM group_members WHERE chat_id = ?",
                (cid,),
            ).fetchall()
            group_members_map[cid] = members

    except Exception as e:
        logger.error(
            f"Ошибка чтения из БД в send_morning_poll_job: {e}", exc_info=True
        )
        return
    finally:
        conn.close()

    # 2. Выполняем асинхронную отправку сообщений без открытых транзакций БД
    options = ["Да", "Заболел(а)", "Опаздываю", "Нет"]

    for chat_id in chat_ids:
        try:
            # Отправка опроса
            poll_msg = await context.bot.send_poll(
                chat_id=chat_id,
                question="Кто идет в университет?",
                options=options,
                is_anonymous=False,
            )

            # Закрепление опроса
            try:
                await context.bot.pin_chat_message(
                    chat_id=chat_id,
                    message_id=poll_msg.message_id,
                    disable_notification=True,
                )
            except Exception as pin_err:
                logger.warning(f"Не удалось закрепить сообщение в {chat_id}: {pin_err}")

            # Формирование созыва
            users_to_tag = {}

            # Получаем админов из Telegram API
            try:
                admins = await context.bot.get_chat_administrators(chat_id)
                for a in admins:
                    if not a.user.is_bot:
                        users_to_tag[a.user.id] = a.user.first_name or "Участник"
            except Exception:
                pass

            # Добавляем участников из словаря (который мы заранее прочитали из БД)
            for m in group_members_map.get(chat_id, []):
                users_to_tag[m["user_id"]] = m["first_name"] or "Участник"

            # Отправка тэгов
            if users_to_tag:
                mentions = [
                    f'<a href="tg://user?id={uid}">{html.escape(name)}</a>'
                    for uid, name in users_to_tag.items()
                ]
                call_text = "📢 <b>Пройдите утренний опрос:</b>\n" + " ".join(mentions)
                await context.bot.send_message(chat_id=chat_id, text=call_text, parse_mode=ParseMode.HTML)

        except Exception as e:
            logger.error(f"Ошибка отправки опроса в {chat_id}: {e}")


def _adaptive_target_time(chat_id: int, chat_type, offset_minutes: int, default_time: str) -> str:
    """For a private chat with a filled-in schedule for today, the target
    send time is offset_minutes before their first lesson. Everyone else
    (groups, or a private chat with no lessons today / unknown chat_type
    from before this feature existed) falls back to the same fixed time
    used for everyone previously."""
    if chat_type == "private":
        lessons = get_lessons(chat_id, now_kz().weekday())
        if lessons:
            return _time_minus_minutes(lessons[0]["time"], offset_minutes)
    return default_time


async def check_adaptive_schedule(context: ContextTypes.DEFAULT_TYPE):
    """Runs every minute: sends each chat's morning timetable (+ room
    photos) once, at the moment that matches its target send time — fixed
    07:30 for groups, or SCHEDULE_OFFSET_MINUTES before that person's first
    lesson today for a private chat with a schedule."""
    now_str = now_kz().strftime("%H:%M")
    weekday = now_kz().weekday()
    for row in list_known_users():
        chat_id = row["chat_id"]
        target = _adaptive_target_time(chat_id, row["chat_type"], SCHEDULE_OFFSET_MINUTES, DEFAULT_SCHEDULE_TIME)
        if target != now_str:
            continue
        try:
            await send_morning_schedule_for_chat(context.bot, chat_id, weekday)
        except Exception as e:
            logger.warning("Could not send morning schedule to chat %s: %s", chat_id, e)


async def check_adaptive_tasks(context: ContextTypes.DEFAULT_TYPE):
    """Runs every minute: sends the shared homework reminder to each chat
    once, at the moment that matches its target send time — fixed 08:00 for
    groups, or TASKS_OFFSET_MINUTES before that person's first lesson today
    for a private chat with a schedule."""
    today_iso = today_kz().isoformat()
    tomorrow_iso = (today_kz() + timedelta(days=1)).isoformat()
    rows = get_tasks(SHARED_TASKS_ID, start=today_iso, end=tomorrow_iso)
    if not rows:
        return
    text = "🔔 Напоминание (общий список заданий):\n" + "\n".join(format_task_line(r) for r in rows)

    now_str = now_kz().strftime("%H:%M")
    for row in list_known_users():
        chat_id = row["chat_id"]
        target = _adaptive_target_time(chat_id, row["chat_type"], TASKS_OFFSET_MINUTES, DEFAULT_TASKS_TIME)
        if target != now_str:
            continue
        # Personal "done" marks: in a private chat leave out what this person
        # already finished (groups have no single person, so see everything
        # that isn't finished globally).
        chat_rows = get_tasks(
            SHARED_TASKS_ID, start=today_iso, end=tomorrow_iso, viewer_id=viewer_for_chat(chat_id)
        )
        if not chat_rows:
            continue
        chat_text = "🔔 Напоминание (общий список заданий):\n" + "\n".join(
            format_task_line(r) for r in chat_rows
        )
        try:
            for chunk in _chunk_text(chat_text):
                await context.bot.send_message(chat_id=chat_id, text=chunk)
        except Exception as e:
            logger.warning("Could not message chat %s: %s", chat_id, e)


async def send_morning_schedule_for_chat(bot, chat_id: int, weekday: int = None) -> bool:
    if weekday is None:
        weekday = now_kz().weekday()

    rows = get_lessons(chat_id, weekday)
    if not rows:
        return False

    heading = f"🌅 Расписание на {WEEKDAY_NAMES_FULL_RU[weekday].lower()}"
    text = format_lessons_block(rows, heading)
    await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.HTML)

    seen_rooms = []
    for r in rows:
        if r["room"] not in seen_rooms:
            seen_rooms.append(r["room"])

    for room in seen_rooms:
        photo = get_room_photo(chat_id, room)
        if not photo:
            continue
        file_id, kind = photo
        caption = f"📍 Кабинет {room}"
        if kind == "document":
            await bot.send_document(chat_id=chat_id, document=file_id, caption=caption)
        else:
            await bot.send_photo(chat_id=chat_id, photo=file_id, caption=caption)

    return True


REMINDER_NAG_MINUTES = 5


def _reminder_fire_keyboard(reminder_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Подтверждаю", callback_data=f"remconfirm:{reminder_id}"),
        InlineKeyboardButton("⏱ Отложить на 10 мин", callback_data=f"remsnooze:{reminder_id}"),
    ]])


async def check_reminders(context: ContextTypes.DEFAULT_TYPE):
    """Runs every minute, two passes:

    1. Fires any personal reminder (/remind) that's due — a one-time
       reminder on/after its date+time, a daily one from its time onward
       today, a weekly one from its time onward on its weekday — each at
       most once per calendar day, tracked via last_sent_date. The message
       asks for confirmation instead of just informing, and starts the nag
       cycle (see mark_reminder_fired).

       Uses "is it due yet" (<=) rather than "does it match this exact
       minute" (==) on purpose: a free-tier host can spin the bot down
       between requests, or a scheduler misfire can skip a single run (both
       observed in production), so the one exact minute a reminder was due
       can pass while nothing checks it. With <=, the first check that does
       run still catches it and sends it late, instead of silently skipping
       it forever.

    2. Re-sends (nags) any reminder still awaiting confirmation whose last
       nag was REMINDER_NAG_MINUTES or more ago — whether the person
       actively said "not yet" or just never answered, the bot keeps
       reminding every 5 minutes either way, until "✅ Подтверждаю" is
       tapped (or the reminder is snoozed/deleted)."""
    now = now_kz()
    now_str = now.strftime("%H:%M")
    today_iso = now.date().isoformat()
    weekday = now.weekday()
    now_iso = now.isoformat()

    for row in get_all_enabled_reminders():
        if row["repeat"] == "once":
            should_send = (
                bool(row["remind_date"]) and not row["last_sent_date"]
                and (
                    row["remind_date"] < today_iso
                    or (row["remind_date"] == today_iso and row["time"] <= now_str)
                )
            )
        elif row["repeat"] == "daily":
            should_send = row["time"] <= now_str and row["last_sent_date"] != today_iso
        elif row["repeat"] == "weekly":
            should_send = (
                row["weekday"] == weekday
                and row["time"] <= now_str
                and row["last_sent_date"] != today_iso
            )
        else:
            should_send = False
        if not should_send:
            continue

        try:
            await context.bot.send_message(
                chat_id=row["chat_id"],
                text=f"🔔 Напоминание: {row['text']}\n\nСделано?",
                reply_markup=_reminder_fire_keyboard(row["id"]),
            )
        except Exception as e:
            logger.warning("Could not send reminder %s to chat %s: %s", row["id"], row["chat_id"], e)
            continue

        mark_reminder_fired(row["id"], today_iso, now_iso)

    for row in get_reminders_awaiting_confirmation():
        last_nag = row["last_nag_at"]
        if not last_nag:
            continue
        try:
            last_nag_dt = datetime.fromisoformat(last_nag)
        except ValueError:
            continue
        if now - last_nag_dt < timedelta(minutes=REMINDER_NAG_MINUTES):
            continue
        try:
            await context.bot.send_message(
                chat_id=row["chat_id"],
                text=f"⚠️ Всё ещё не подтверждено: {row['text']}",
                reply_markup=_reminder_fire_keyboard(row["id"]),
            )
        except Exception as e:
            logger.warning("Could not nag reminder %s in chat %s: %s", row["id"], row["chat_id"], e)
            continue
        bump_reminder_nag(row["id"], now_iso)


async def backup_to_secondary(context: ContextTypes.DEFAULT_TYPE):
    """Runs every 6 hours: mirrors the active database into the first
    backup database (DATABASE_URL_BACKUP2). No-op if that env var isn't
    set — failover/backups stay fully opt-in."""
    if not DATABASE_URL_BACKUP2:
        return
    try:
        await asyncio.to_thread(init_db, DATABASE_URL_BACKUP2)
        ok = await asyncio.to_thread(mirror_active_db_to, DATABASE_URL_BACKUP2)
        logger.info("Backup to secondary DB: %s", "ok" if ok else "skipped (not Postgres)")
    except Exception as e:
        logger.error("Backup to secondary DB failed: %s", e)


async def backup_to_tertiary(context: ContextTypes.DEFAULT_TYPE):
    """Runs once a night (see main.py's run_daily): mirrors the active
    database into the second backup database (DATABASE_URL_BACKUP3)."""
    if not DATABASE_URL_BACKUP3:
        return
    try:
        await asyncio.to_thread(init_db, DATABASE_URL_BACKUP3)
        ok = await asyncio.to_thread(mirror_active_db_to, DATABASE_URL_BACKUP3)
        logger.info("Backup to tertiary DB: %s", "ok" if ok else "skipped (not Postgres)")
    except Exception as e:
        logger.error("Backup to tertiary DB failed: %s", e)


async def sync_lms_job(context: ContextTypes.DEFAULT_TYPE):
    """Periodic pull of LMS deadlines into the shared task list (no-op
    unless LMS_ICAL_URL is set). A failure is logged and retried next time."""
    if not LMS_ICAL_URL:
        return
    try:
        await asyncio.to_thread(sync_lms)
    except Exception as e:
        logger.warning("LMS sync failed: %s", e)


TASK_DEADLINE_LEAD_MINUTES = 60


async def check_task_deadline_reminders(context: ContextTypes.DEFAULT_TYPE):
    """Runs every minute: for every undone task that has an exact due_time
    set, fires a one-off "1 hour left" heads-up to every known chat (groups
    and private chats alike) once its deadline comes within
    TASK_DEADLINE_LEAD_MINUTES — using "<=" rather than "==" so a missed
    exact minute (host spin-down, scheduler misfire) still catches it late
    instead of silently skipping it, same reasoning as check_reminders.
    deadline_notified guards against sending it more than once."""
    now = now_kz()
    rows = get_tasks(SHARED_TASKS_ID, only_undone=True)
    for row in rows:
        if row["deadline_notified"]:
            continue
        due_dt = task_due_datetime(row)
        if due_dt is None:
            continue
        minutes_left = (due_dt - now).total_seconds() / 60
        if not (0 <= minutes_left <= TASK_DEADLINE_LEAD_MINUTES):
            continue
        text = (
            f"⏰ Через час дедлайн: [{SUBJECT_NAME[row['subject']]}] {row['title']} — "
            f"{due_dt.strftime('%d.%m.%Y %H:%M')}"
        )
        finished = users_who_finished(row["id"])
        for chat_id in all_chat_ids():
            if chat_id in finished:
                continue  # this person already marked it done — no need to nag
            try:
                await context.bot.send_message(chat_id=chat_id, text=text)
            except Exception as e:
                logger.warning("Could not send deadline reminder to chat %s: %s", chat_id, e)
        mark_task_deadline_notified(row["id"])


async def check_lesson_reminders(context: ContextTypes.DEFAULT_TYPE):
    """Runs every minute: for each chat, finds any lesson today whose start
    time is exactly LESSON_REMINDER_MINUTES from now, and sends a heads-up.
    Minute-granularity matching means each lesson fires once, at the minute
    that lines up — no separate dedupe bookkeeping needed."""
    now = now_kz()
    weekday = now.weekday()
    target_time = (now + timedelta(minutes=LESSON_REMINDER_MINUTES)).strftime("%H:%M")

    for chat_id in all_chat_ids():
        rows = get_lessons(chat_id, weekday)
        for r in rows:
            if r["time"] != target_time:
                continue
            text = (
                f"⏰ Через {LESSON_REMINDER_MINUTES} минут: "
                f"[{SUBJECT_NAME[r['subject']]}] в {r['time']}, каб. {r['room']}"
            )
            try:
                await context.bot.send_message(chat_id=chat_id, text=text)
            except Exception as e:
                logger.warning("Could not send lesson reminder to chat %s: %s", chat_id, e)
                continue

            photo = get_room_photo(chat_id, r["room"])
            if not photo:
                continue
            file_id, kind = photo
            try:
                caption = f"📍 Кабинет {r['room']}"
                if kind == "document":
                    await context.bot.send_document(chat_id=chat_id, document=file_id, caption=caption)
                else:
                    await context.bot.send_photo(chat_id=chat_id, photo=file_id, caption=caption)
            except Exception as e:
                logger.warning("Could not send room photo reminder to chat %s: %s", chat_id, e)
