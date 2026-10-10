"""Weekly-timetable commands: /schedule_add, /schedule, /schedule_week,
/schedule_day, /schedule_delete, the /editschedule conversation, and the
admin allow-list toggle for who may manage the schedule."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, ConversationHandler

from ..constants import ALL_USERS_SENTINEL, SUBJECT_NAME, WEEKDAY_EMOJI, WEEKDAY_NAMES_FULL_RU, WEEKDAY_NAMES_RU
from ..db import (
    add_lesson,
    all_chat_ids,
    delete_lesson,
    display_name,
    get_lessons,
    get_room_photo,
    list_known_users,
    register_chat,
    update_lesson_field,
)
from ..formatting import format_lessons_block
from ..keyboards import _edit_lesson_field_keyboard, subject_keyboard, target_user_keyboard, weekday_keyboard
from ..permissions import allow_user_schedule, disallow_user_schedule, is_admin, is_schedule_allowed
from ..states import (
    CHOOSING_TARGET_USER,
    EDIT_LESSON_FIELD,
    EDIT_LESSON_PICK,
    EDIT_LESSON_ROOM,
    EDIT_LESSON_SUBJECT,
    EDIT_LESSON_TIME,
    EDIT_LESSON_WEEKDAY,
    SCH_ROOM,
    SCH_SUBJECT,
    SCH_TIME,
    SCH_WEEKDAY,
)
from ..utils import parse_due_time, reply_text_chunked, now_kz


async def schedule_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_schedule_allowed(update.effective_user.id):
      await update.message.reply_text("У вас нет доступа к управлению расписанием.")
      return ConversationHandler.END
    register_chat(update)
    if is_admin(update.effective_user.id):
        await update.message.reply_text(
            "Кому добавляем пару в расписание?", reply_markup=target_user_keyboard("schtgt")
        )
        return CHOOSING_TARGET_USER

    context.user_data["sch_target_chat"] = update.effective_chat.id
    await update.message.reply_text(
        "В какой день недели этот урок?", reply_markup=weekday_keyboard("schwd")
    )
    return SCH_WEEKDAY

async def allow_schedule_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Эта команда доступна только администраторам.")
        return
    rows = list_known_users()
    if not rows:
        await update.message.reply_text("Пользователей не найдено.")
        return
    buttons = [
        [InlineKeyboardButton(
            f"{'✅ ' if is_schedule_allowed(r['chat_id']) else ''}{display_name(r)}",
            callback_data=f"toggle_sch_perm:{r['chat_id']}"
        )]
        for r in rows
    ]
    await update.message.reply_text(
        "Выберите пользователя, чтобы переключить доступ к расписанию:",
        reply_markup=InlineKeyboardMarkup(buttons)
    )

async def toggle_schedule_permission_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(update.effective_user.id):
        return
    target_id = int(query.data.split(":")[1])
    if is_schedule_allowed(target_id):
        disallow_user_schedule(target_id)
        msg = f"Доступ к расписанию для пользователя {target_id} отключен ❌"
    else:
        allow_user_schedule(target_id)
        msg = f"Доступ к расписанию для пользователя {target_id} включен ✅"
    await query.edit_message_text(msg)
async def schedule_target_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    raw = query.data.split(":", 1)[1]
    if raw == "self":
        target = update.effective_chat.id
        who = "себя"
    elif raw == "all":
        target = ALL_USERS_SENTINEL
        who = "всех известных пользователей"
    else:
        target = int(raw)
        who = f"chat_id {target}"
    context.user_data["sch_target_chat"] = target
    await query.edit_message_text(
        f"Добавляем пару для: {who}\n\nВ какой день недели этот урок?",
        reply_markup=weekday_keyboard("schwd"),
    )
    return SCH_WEEKDAY
async def schedule_weekday_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    weekday = int(query.data.split(":")[1])
    context.user_data["sch_weekday"] = weekday
    await query.edit_message_text(
        f"День: {WEEKDAY_NAMES_FULL_RU[weekday]}\n\nВыбери предмет:",
    )
    await query.message.reply_text("Предмет:", reply_markup=subject_keyboard("schsub"))
    return SCH_SUBJECT


async def schedule_subject_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    subject = query.data.split(":")[1]
    context.user_data["sch_subject"] = subject
    await query.edit_message_text(
        f"Предмет: {SUBJECT_NAME[subject]}\n\nВо сколько начало? Напиши время в формате ЧЧ:ММ."
    )
    return SCH_TIME


async def schedule_time_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = parse_due_time(update.message.text)
    if not t:
        await update.message.reply_text(
            "Не понял время. Напиши в формате ЧЧ:ММ, например 09:00."
        )
        return SCH_TIME
    context.user_data["sch_time"] = t
    await update.message.reply_text("В каком кабинете? Напиши номер/название кабинета.")
    return SCH_ROOM


async def schedule_room_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    room = update.message.text.strip()
    target = context.user_data.pop("sch_target_chat")
    weekday = context.user_data.pop("sch_weekday")
    subject = context.user_data.pop("sch_subject")
    time_str = context.user_data.pop("sch_time")

    if target == ALL_USERS_SENTINEL:
        targets = all_chat_ids()
        for chat_id in targets:
            add_lesson(chat_id, weekday, time_str, subject, room)
        who_line = f"для всех известных пользователей ({len(targets)} чел.)"
    else:
        add_lesson(target, weekday, time_str, subject, room)
        who_line = ""

    await update.message.reply_text(
        f"Добавлено ✅ {who_line}\n{WEEKDAY_NAMES_FULL_RU[weekday]}, {time_str} — "
        f"[{SUBJECT_NAME[subject]}], каб. {room}\n\n"
        f"Если хочешь, чтобы бот присылал фото/карту этого кабинета по утрам — "
        f"пришли его через /addroomphoto."
    )
    return ConversationHandler.END


async def schedule_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END


async def schedule_today_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_schedule_allowed(update.effective_user.id):
      await update.message.reply_text("У вас нет доступа к просмотру расписания.")
      return
    register_chat(update)
    weekday = now_kz().weekday()
    rows = get_lessons(update.effective_chat.id, weekday)
    if not rows:
        await update.message.reply_text(
            f"На {WEEKDAY_NAMES_FULL_RU[weekday].lower()} пар не добавлено."
        )
        return
    heading = f"📅 Расписание на сегодня ({WEEKDAY_NAMES_FULL_RU[weekday].lower()})"
    text = format_lessons_block(rows, heading)
    await reply_text_chunked(update.message, text, parse_mode=ParseMode.HTML)


async def schedule_week_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_schedule_allowed(update.effective_user.id):
      await update.message.reply_text("У вас нет доступа к просмотру расписания.")
      return
    register_chat(update)
    rows = get_lessons(update.effective_chat.id)
    if not rows:
        await update.message.reply_text("Расписание пока пустое. Добавь уроки через /schedule_add.")
        return
    by_day = {i: [] for i in range(7)}
    for r in rows:
        by_day[r["weekday"]].append(r)
    today_weekday = now_kz().weekday()
    blocks = ["🗓 <b>Расписание на неделю</b>"]
    for i in range(7):
        if not by_day[i]:
            continue
        marker = " 📌" if i == today_weekday else ""
        heading = f"{WEEKDAY_EMOJI[i]} {WEEKDAY_NAMES_FULL_RU[i]}{marker}"
        blocks.append(format_lessons_block(by_day[i], heading))
    text = "\n\n".join(blocks)
    await reply_text_chunked(update.message, text, parse_mode=ParseMode.HTML)


async def schedule_delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_schedule_allowed(update.effective_user.id):
        await update.message.reply_text("У вас нет доступа к управлению расписанием.")
        return
    register_chat(update)
    rows = get_lessons(update.effective_chat.id)
    if not rows:
        await update.message.reply_text("Расписание пустое.")
        return
    buttons = [
        [InlineKeyboardButton(
            f"{WEEKDAY_NAMES_RU[r['weekday']]} {r['time']} {SUBJECT_NAME[r['subject']]}",
            callback_data=f"schdel:{r['id']}",
        )]
        for r in rows
    ]
    await update.message.reply_text(
        "Что удалить из расписания?", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def schedule_delete_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lesson_id = int(query.data.split(":")[1])
    delete_lesson(lesson_id)
    await query.edit_message_text("Удалено 🗑")


# --- /editschedule: change weekday, subject, time or room of a lesson -----
async def editschedule_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_schedule_allowed(update.effective_user.id):
        await update.message.reply_text("У вас нет доступа к управлению расписанием.")
        return ConversationHandler.END
    register_chat(update)
    rows = get_lessons(update.effective_chat.id)
    if not rows:
        await update.message.reply_text("Расписание пустое. Добавь пару через /schedule_add.")
        return ConversationHandler.END
    buttons = [
        [InlineKeyboardButton(
            f"{WEEKDAY_NAMES_RU[r['weekday']]} {r['time']} {SUBJECT_NAME[r['subject']]}",
            callback_data=f"editlesson:{r['id']}",
        )]
        for r in rows
    ]
    await update.message.reply_text(
        "Какую пару изменить?", reply_markup=InlineKeyboardMarkup(buttons)
    )
    return EDIT_LESSON_PICK


async def editschedule_picked(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    lesson_id = int(query.data.split(":")[1])
    context.user_data["edit_lesson_id"] = lesson_id
    await query.edit_message_text(
        "Что изменить в этой паре?", reply_markup=_edit_lesson_field_keyboard()
    )
    return EDIT_LESSON_FIELD


async def editschedule_field_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    field = query.data.split(":", 1)[1]

    if field == "weekday":
        await query.edit_message_text("Выбери новый день недели:")
        await query.message.reply_text("День недели:", reply_markup=weekday_keyboard("editlwd"))
        return EDIT_LESSON_WEEKDAY
    if field == "subject":
        await query.edit_message_text("Выбери новый предмет:")
        await query.message.reply_text("Предмет:", reply_markup=subject_keyboard("editlsub"))
        return EDIT_LESSON_SUBJECT
    if field == "time":
        await query.edit_message_text("Напиши новое время начала в формате ЧЧ:ММ:")
        return EDIT_LESSON_TIME
    if field == "room":
        await query.edit_message_text("Напиши новый номер/название кабинета:")
        return EDIT_LESSON_ROOM


async def editschedule_weekday_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    weekday = int(query.data.split(":")[1])
    lesson_id = context.user_data.pop("edit_lesson_id")
    update_lesson_field(lesson_id, "weekday", weekday)
    await query.edit_message_text(f"Готово ✅ День изменён на {WEEKDAY_NAMES_FULL_RU[weekday]}.")
    return ConversationHandler.END


async def editschedule_subject_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    subject = query.data.split(":")[1]
    lesson_id = context.user_data.pop("edit_lesson_id")
    update_lesson_field(lesson_id, "subject", subject)
    await query.edit_message_text(f"Готово ✅ Предмет изменён на «{SUBJECT_NAME[subject]}».")
    return ConversationHandler.END


async def editschedule_time_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = parse_due_time(update.message.text)
    if not t:  # time is required for a lesson — "" (skip word) and None both invalid
        await update.message.reply_text(
            "Не понял время. Напиши в формате ЧЧ:ММ, например 09:00."
        )
        return EDIT_LESSON_TIME
    lesson_id = context.user_data.pop("edit_lesson_id")
    update_lesson_field(lesson_id, "time", t)
    await update.message.reply_text(f"Готово ✅ Время изменено на {t}.")
    return ConversationHandler.END


async def editschedule_room_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    room = update.message.text.strip()
    lesson_id = context.user_data.pop("edit_lesson_id")
    update_lesson_field(lesson_id, "room", room)
    await update.message.reply_text(f"Готово ✅ Кабинет изменён на «{room}».")
    return ConversationHandler.END


async def schedule_day_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_schedule_allowed(update.effective_user.id):
        await update.message.reply_text("У вас нет доступа к просмотру расписания.")
        return
    register_chat(update)
    await update.message.reply_text(
        "На какой день недели показать расписание?",
        reply_markup=weekday_keyboard("schday"),
    )


async def schedule_day_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    weekday = int(query.data.split(":")[1])
    chat_id = update.effective_chat.id

    rows = get_lessons(chat_id, weekday)
    if not rows:
        await query.edit_message_text(
            f"На {WEEKDAY_NAMES_FULL_RU[weekday].lower()} пар не добавлено."
        )
        return

    marker = " 📌 (сегодня)" if weekday == now_kz().weekday() else ""
    heading = f"{WEEKDAY_EMOJI[weekday]} Расписание: {WEEKDAY_NAMES_FULL_RU[weekday]}{marker}"
    text = format_lessons_block(rows, heading)
    await query.edit_message_text(text, parse_mode=ParseMode.HTML)

    seen_rooms = []
    for r in rows:
        if r["room"] not in seen_rooms:
            seen_rooms.append(r["room"])

    sent_any_photo = False
    for room in seen_rooms:
        photo = get_room_photo(chat_id, room)
        if not photo:
            continue
        file_id, kind = photo
        sent_any_photo = True
        caption = f"📍 Кабинет {room}"
        if kind == "document":
            await query.message.reply_document(document=file_id, caption=caption)
        else:
            await query.message.reply_photo(photo=file_id, caption=caption)

    if not sent_any_photo:
        await query.message.reply_text(
            "ℹ️ Для кабинетов этого дня фото пока не сохранены (/addroomphoto)."
        )




# ---------------------------------------------------------------------------
# /copyschedule — take someone else's timetable as a starting point
# ---------------------------------------------------------------------------
async def copyschedule_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    if not is_schedule_allowed(update.effective_user.id):
        await update.message.reply_text("У вас нет доступа к управлению расписанием.")
        return
    here = update.effective_chat.id
    buttons = []
    for u in list_known_users():
        if u["chat_id"] == here or u["chat_type"] != "private":
            continue
        n = len(get_lessons(u["chat_id"]))
        if n:
            buttons.append([InlineKeyboardButton(f"{display_name(u)} — {n} пар", callback_data=f"cpsrc:{u['chat_id']}")])
    if not buttons:
        await update.message.reply_text("Пока ни у кого нет расписания, которое можно скопировать.")
        return
    where = "в твоё расписание" if update.effective_chat.type == "private" else "в расписание этой группы"
    await update.message.reply_text(
        f"Чьё расписание скопировать {where}? Потом его можно поправить через /editschedule.",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def copyschedule_source_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_schedule_allowed(q.from_user.id):
        return
    src = int(q.data.split(":")[1])
    lessons = get_lessons(src)
    if not lessons:
        await q.edit_message_text("У этого человека расписание пустое.")
        return
    mine = len(get_lessons(q.message.chat.id))
    rows = [[InlineKeyboardButton("➕ Добавить к моему", callback_data=f"cpgo:{src}:add")]]
    if mine:
        rows.append([InlineKeyboardButton(f"♻️ Заменить моё ({mine} пар)", callback_data=f"cpgo:{src}:replace")])
    rows.append([InlineKeyboardButton("✖️ Отмена", callback_data="cpgo:0:cancel")])
    await q.edit_message_text(
        f"Скопировать {len(lessons)} пар (и фото кабинетов)?",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def copyschedule_go(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from ..db import list_room_photos, set_room_photo

    q = update.callback_query
    await q.answer()
    _, src, mode = q.data.split(":")
    if mode == "cancel":
        await q.edit_message_text("Отменено.")
        return
    if not is_schedule_allowed(q.from_user.id):
        return
    src, dst = int(src), q.message.chat.id
    lessons = get_lessons(src)
    if mode == "replace":
        for old in get_lessons(dst):
            delete_lesson(old["id"])
    have = {(r["weekday"], r["time"], r["subject"], r["room"]) for r in get_lessons(dst)}
    added = 0
    for r in lessons:
        key = (r["weekday"], r["time"], r["subject"], r["room"])
        if key in have:
            continue
        add_lesson(dst, r["weekday"], r["time"], r["subject"], r["room"])
        have.add(key)
        added += 1
    photos = 0
    mine = set(list_room_photos(dst))
    for room in list_room_photos(src):
        if room in mine:
            continue
        p = get_room_photo(src, room)
        if p:
            set_room_photo(dst, room, p[0], p[1])
            photos += 1
    await q.edit_message_text(
        f"✅ Скопировано пар: {added}, фото кабинетов: {photos}.\n"
        "Посмотреть — /schedule_week, поправить — /editschedule."
    )
