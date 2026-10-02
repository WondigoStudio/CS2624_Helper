"""Homework-task commands: add/today/week/all/done/delete, the /edittask
conversation, and re-sending a task's attachment (/taskfile)."""

import html
from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, ConversationHandler

from ..ai_format import maybe_structure_description, strip_html_preview
from ..config import SHARED_TASKS_ID
from ..constants import SUBJECT_NAME
from ..db import (
    add_task,
    add_task_attachment,
    clear_task_attachments,
    delete_task,
    get_task,
    get_task_attachments,
    get_tasks,
    mark_done,
    register_chat,
    update_task_description,
    update_task_field,
)
from ..formatting import _overdue_block, user_short_name
from ..keyboards import _edit_task_field_keyboard, subject_keyboard
from ..permissions import is_schedule_allowed
from ..states import (
    CHOOSING_SUBJECT,
    EDIT_TASK_ATTACHMENT,
    EDIT_TASK_DATE,
    EDIT_TASK_DESCRIPTION,
    EDIT_TASK_FIELD,
    EDIT_TASK_PICK,
    EDIT_TASK_SUBJECT,
    EDIT_TASK_TIME,
    EDIT_TASK_TITLE,
    TYPING_ATTACHMENT,
    TYPING_DATE,
    TYPING_DESCRIPTION,
    TYPING_TIME,
    TYPING_TITLE,
)
from ..formatting import format_task_line
from ..utils import is_task_overdue, parse_due_date, parse_due_time, reply_text_chunked, today_kz


async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Reuses the same allow-list as the schedule (/allow_schedule) rather
    # than a separate one — one "trusted to manage shared stuff" list for
    # both, admins included automatically.
    if not is_schedule_allowed(update.effective_user.id):
        await update.message.reply_text("У вас нет доступа к добавлению заданий.")
        return ConversationHandler.END
    register_chat(update)
    await update.message.reply_text(
        "Выбери предмет:", reply_markup=subject_keyboard("addsub")
    )
    return CHOOSING_SUBJECT


async def add_subject_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    subject = query.data.split(":")[1]
    context.user_data["new_subject"] = subject
    await query.edit_message_text(
        f"Предмет: {SUBJECT_NAME[subject]}\n\nНапиши, что нужно сделать:"
    )
    return TYPING_TITLE


async def add_title_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_title"] = update.message.text.strip()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Без описания", callback_data="nodesc")]
    ])
    await update.message.reply_text(
        "Добавь подробное описание задания (что именно нужно сделать, номера "
        "заданий и т.п.), или нажми кнопку, если название всё уже объясняет.",
        reply_markup=keyboard,
    )
    return TYPING_DESCRIPTION


async def add_description_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = update.message.text.strip()
    status = None
    if len(raw) >= 220:
        status = await update.message.reply_text("✨ Структурирую описание…")
    text, is_html = await maybe_structure_description(raw)
    context.user_data["new_description"] = text
    context.user_data["new_description_html"] = is_html
    if status:
        try:
            await status.delete()
        except Exception:
            pass
    await update.message.reply_text(
        "Когда сдавать? Напиши дату в формате ДД.ММ или ДД.ММ.ГГГГ, "
        "либо просто «сегодня» / «завтра»."
    )
    return TYPING_DATE


async def add_description_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["new_description"] = None
    context.user_data["new_description_html"] = False
    await query.edit_message_text(
        "Когда сдавать? Напиши дату в формате ДД.ММ или ДД.ММ.ГГГГ, "
        "либо просто «сегодня» / «завтра»."
    )
    return TYPING_DATE


async def add_date_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = parse_due_date(update.message.text)
    if d is None:
        await update.message.reply_text(
            "Не понял дату. Попробуй ещё раз, например: 05.10 или 05.10.2026"
        )
        return TYPING_DATE

    context.user_data["new_date"] = d.isoformat()
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Без точного времени", callback_data="notime")]
    ])
    await update.message.reply_text(
        "Во сколько (время сдачи/пары)? Напиши в формате ЧЧ:ММ, например 14:30.\n"
        "Или нажми кнопку, если точное время не нужно.",
        reply_markup=keyboard,
    )
    return TYPING_TIME


def _attachment_done_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Готово", callback_data="attachdone")]])


async def _prompt_for_attachment(target_message):
    await target_message(
        "Прикрепи фото или файлы к заданию (скан условия, фото с доски и т.п.) — "
        "можно несколько, присылай по одному. Когда закончишь (или если вложения "
        "не нужны), нажми «Готово».",
        reply_markup=_attachment_done_keyboard(),
    )


async def _finish_add_task(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, attachments: list = None,
):
    attachments = attachments or []
    subject = context.user_data.pop("new_subject")
    title = context.user_data.pop("new_title")
    description = context.user_data.pop("new_description", None)
    description_html = context.user_data.pop("new_description_html", False)
    due_date_iso = context.user_data.pop("new_date")
    due_time = context.user_data.pop("new_time", "")
    creator_name = context.user_data.pop("new_creator", None)
    task_id = add_task(
        chat_id, subject, title, due_date_iso, due_time or None,
        created_by=creator_name, description=description, description_html=description_html,
    )
    for file_id, kind in attachments:
        add_task_attachment(task_id, file_id, kind)
    d = datetime.strptime(due_date_iso, "%Y-%m-%d").date()
    time_part = f", {due_time}" if due_time else ""
    desc_part = f"\n📝 {strip_html_preview(description)}" if description else ""
    attach_part = f"\n📎 вложений: {len(attachments)}" if attachments else ""
    return f"Готово ✅\n[{SUBJECT_NAME[subject]}] {title} — {d.strftime('%d.%m.%Y')}{time_part}{desc_part}{attach_part}"
async def add_time_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = parse_due_time(update.message.text)
    if t is None:
        await update.message.reply_text(
            "Не понял время. Напиши в формате ЧЧ:ММ (например 09:00), "
            "или «нет», если время не нужно."
        )
        return TYPING_TIME
    context.user_data["new_time"] = t
    context.user_data["new_creator"] = user_short_name(update)
    await _prompt_for_attachment(update.message.reply_text)
    return TYPING_ATTACHMENT


async def add_time_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["new_time"] = ""
    context.user_data["new_creator"] = user_short_name(update)
    await query.edit_message_text("Без точного времени.")
    await _prompt_for_attachment(query.message.reply_text)
    return TYPING_ATTACHMENT


async def add_attachment_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file_id = update.message.photo[-1].file_id
    attachments = context.user_data.setdefault("new_attachments", [])
    attachments.append((file_id, "photo"))
    await update.message.reply_text(
        f"📎 Добавлено ({len(attachments)}). Пришли ещё или нажми «Готово».",
        reply_markup=_attachment_done_keyboard(),
    )
    return TYPING_ATTACHMENT


async def add_attachment_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    attachments = context.user_data.setdefault("new_attachments", [])
    attachments.append((doc.file_id, "document"))
    await update.message.reply_text(
        f"📎 Добавлено ({len(attachments)}). Пришли ещё или нажми «Готово».",
        reply_markup=_attachment_done_keyboard(),
    )
    return TYPING_ATTACHMENT


async def add_attachment_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    attachments = context.user_data.pop("new_attachments", [])
    text = await _finish_add_task(context, SHARED_TASKS_ID, attachments)
    await query.edit_message_text(text)
    return ConversationHandler.END


async def add_attachment_invalid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Это не похоже на фото или файл. Пришли картинку/документ, или нажми "
        "«Без вложения»."
    )
    return TYPING_ATTACHMENT


async def add_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END



async def today_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    d = today_kz().isoformat()
    rows = [r for r in get_tasks(SHARED_TASKS_ID, start=d, end=d) if not is_task_overdue(r)]
    overdue_block = _overdue_block(SHARED_TASKS_ID)
    if not rows and not overdue_block:
        await update.message.reply_text("На сегодня заданий нет 🎉")
        return
    body = "Сегодня:\n" + "\n".join(format_task_line(r) for r in rows) if rows else "На сегодня заданий нет."
    text = overdue_block + body
    await reply_text_chunked(update.message, text)


async def week_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    start = today_kz().isoformat()
    end = (today_kz() + timedelta(days=7)).isoformat()
    rows = [r for r in get_tasks(SHARED_TASKS_ID, start=start, end=end) if not is_task_overdue(r)]
    overdue_block = _overdue_block(SHARED_TASKS_ID)
    if not rows and not overdue_block:
        await update.message.reply_text("На эту неделю заданий нет 🎉")
        return
    body = "На неделю:\n" + "\n".join(format_task_line(r) for r in rows) if rows else "На эту неделю заданий нет."
    text = overdue_block + body
    await reply_text_chunked(update.message, text)


async def all_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    rows = get_tasks(SHARED_TASKS_ID)
    if not rows:
        await update.message.reply_text("Список пуст 🎉")
        return
    text = "Все предстоящие задания:\n" + "\n".join(format_task_line(r) for r in rows)
    await reply_text_chunked(update.message, text)


async def done_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_schedule_allowed(update.effective_user.id):
        await update.message.reply_text("У вас нет доступа к управлению заданиями.")
        return
    register_chat(update)
    rows = get_tasks(SHARED_TASKS_ID)
    if not rows:
        await update.message.reply_text("Нет незавершённых заданий.")
        return
    buttons = [
        [InlineKeyboardButton(
            f"{SUBJECT_NAME[r['subject']]}: {r['title'][:35]}",
            callback_data=f"done:{r['id']}",
        )]
        for r in rows
    ]
    await update.message.reply_text(
        "Какое задание выполнено?", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def done_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split(":")[1])
    mark_done(task_id)
    await query.edit_message_text("Отмечено как выполненное ✅")


async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_schedule_allowed(update.effective_user.id):
        await update.message.reply_text("У вас нет доступа к управлению заданиями.")
        return
    register_chat(update)
    rows = get_tasks(SHARED_TASKS_ID, only_undone=False)
    if not rows:
        await update.message.reply_text("Список пуст.")
        return
    buttons = [
        [InlineKeyboardButton(
            f"{SUBJECT_NAME[r['subject']]}: {r['title'][:35]}",
            callback_data=f"del:{r['id']}",
        )]
        for r in rows
    ]
    await update.message.reply_text(
        "Что удалить?", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def delete_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split(":")[1])
    delete_task(task_id)
    await query.edit_message_text("Удалено 🗑")


# --- /edittask: change subject, title, date or time of an existing task ---
async def taskfile_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lets anyone re-send the attachment of a task that has one, without
    scrolling back to find the original message."""
    register_chat(update)
    rows = [r for r in get_tasks(SHARED_TASKS_ID, only_undone=False) if get_task_attachments(r["id"])]
    if not rows:
        await update.message.reply_text("Ни у одного задания пока нет вложения.")
        return
    buttons = [
        [InlineKeyboardButton(
            f"{SUBJECT_NAME[r['subject']]}: {r['title'][:35]}",
            callback_data=f"taskfile:{r['id']}",
        )]
        for r in rows
    ]
    await update.message.reply_text(
        "У какого задания показать вложение?", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def taskfile_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split(":")[1])
    task = get_task(task_id)
    attachments = get_task_attachments(task_id) if task else []
    if not task or not attachments:
        await query.message.reply_text("Вложение не найдено.")
        return
    caption = f"[{SUBJECT_NAME[task['subject']]}] {task['title']}"
    for i, att in enumerate(attachments):
        att_caption = caption if i == 0 else None
        if att["kind"] == "document":
            await query.message.reply_document(document=att["file_id"], caption=att_caption)
        else:
            await query.message.reply_photo(photo=att["file_id"], caption=att_caption)


async def edittask_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_schedule_allowed(update.effective_user.id):
        await update.message.reply_text("У вас нет доступа к управлению заданиями.")
        return ConversationHandler.END
    register_chat(update)
    rows = get_tasks(SHARED_TASKS_ID, only_undone=False)
    if not rows:
        await update.message.reply_text("Список заданий пуст.")
        return ConversationHandler.END
    buttons = [
        [InlineKeyboardButton(
            f"{SUBJECT_NAME[r['subject']]}: {r['title'][:35]}",
            callback_data=f"edittask:{r['id']}",
        )]
        for r in rows
    ]
    await update.message.reply_text(
        "Какое задание изменить?", reply_markup=InlineKeyboardMarkup(buttons)
    )
    return EDIT_TASK_PICK


async def edittask_picked(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split(":")[1])
    context.user_data["edit_task_id"] = task_id
    await query.edit_message_text(
        "Что изменить в этом задании?", reply_markup=_edit_task_field_keyboard()
    )
    return EDIT_TASK_FIELD


async def edittask_field_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    field = query.data.split(":", 1)[1]

    if field == "subject":
        await query.edit_message_text("Выбери новый предмет:")
        await query.message.reply_text("Предмет:", reply_markup=subject_keyboard("edittasksub"))
        return EDIT_TASK_SUBJECT
    if field == "title":
        await query.edit_message_text("Напиши новый текст задания:")
        return EDIT_TASK_TITLE
    if field == "due_date":
        await query.edit_message_text(
            "Напиши новую дату в формате ДД.ММ или ДД.ММ.ГГГГ, либо «сегодня»/«завтра»:"
        )
        return EDIT_TASK_DATE
    if field == "due_time":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Без точного времени", callback_data="edittasktime:none")]
        ])
        await query.edit_message_text(
            "Напиши новое время в формате ЧЧ:ММ, или нажми кнопку, чтобы убрать время:",
            reply_markup=keyboard,
        )
        return EDIT_TASK_TIME
    if field == "description":
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Убрать описание", callback_data="edittaskdesc:none")]
        ])
        await query.edit_message_text(
            "Напиши новое описание, или нажми кнопку, чтобы убрать его:",
            reply_markup=keyboard,
        )
        return EDIT_TASK_DESCRIPTION
    if field == "attachment":
        task_id = context.user_data["edit_task_id"]
        current_count = len(get_task_attachments(task_id))
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑 Убрать все вложения", callback_data="edittaskattach:clear")],
            [InlineKeyboardButton("✅ Готово", callback_data="edittaskattach:done")],
        ])
        await query.edit_message_text(
            f"Сейчас вложений: {current_count}. Пришли фото/файлы, чтобы добавить ещё "
            "(можно несколько по очереди), либо выбери действие ниже:",
            reply_markup=keyboard,
        )
        return EDIT_TASK_ATTACHMENT


async def edittask_subject_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    subject = query.data.split(":")[1]
    task_id = context.user_data.pop("edit_task_id")
    update_task_field(task_id, "subject", subject)
    await query.edit_message_text(f"Готово ✅ Предмет изменён на «{SUBJECT_NAME[subject]}».")
    return ConversationHandler.END


async def edittask_title_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task_id = context.user_data.pop("edit_task_id")
    new_title = update.message.text.strip()
    update_task_field(task_id, "title", new_title)
    await update.message.reply_text(f"Готово ✅ Текст задания изменён на «{new_title}».")
    return ConversationHandler.END


async def edittask_date_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    d = parse_due_date(update.message.text)
    if d is None:
        await update.message.reply_text(
            "Не понял дату. Попробуй ещё раз, например: 05.10 или 05.10.2026"
        )
        return EDIT_TASK_DATE
    task_id = context.user_data.pop("edit_task_id")
    update_task_field(task_id, "due_date", d.isoformat())
    await update.message.reply_text(f"Готово ✅ Дата изменена на {d.strftime('%d.%m.%Y')}.")
    return ConversationHandler.END

async def edittask_time_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t = parse_due_time(update.message.text)
    if t is None:
        await update.message.reply_text(
            "Не понял время. Напиши в формате ЧЧ:ММ (например 09:00), "
            "или «нет», чтобы убрать время."
        )
        return EDIT_TASK_TIME
    task_id = context.user_data.pop("edit_task_id")
    update_task_field(task_id, "due_time", t or None)
    await update.message.reply_text(
        f"Готово ✅ Время изменено на {t}." if t else "Готово ✅ Время убрано."
    )
    return ConversationHandler.END


async def edittask_time_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = context.user_data.pop("edit_task_id")
    update_task_field(task_id, "due_time", None)
    await query.edit_message_text("Готово ✅ Время убрано.")
    return ConversationHandler.END


async def edittask_description_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task_id = context.user_data.pop("edit_task_id")
    raw = update.message.text.strip()
    status = None
    if len(raw) >= 220:
        status = await update.message.reply_text("✨ Структурирую описание…")
    text, is_html = await maybe_structure_description(raw)
    update_task_description(task_id, text, description_html=is_html)
    if status:
        try:
            await status.delete()
        except Exception:
            pass
    await update.message.reply_text("Готово ✅ Описание обновлено.")
    return ConversationHandler.END


async def edittask_description_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = context.user_data.pop("edit_task_id")
    update_task_field(task_id, "description", None)
    await query.edit_message_text("Готово ✅ Описание убрано.")
    return ConversationHandler.END


async def edittask_attachment_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task_id = context.user_data["edit_task_id"]
    file_id = update.message.photo[-1].file_id
    add_task_attachment(task_id, file_id, "photo")
    count = len(get_task_attachments(task_id))
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 Убрать все вложения", callback_data="edittaskattach:clear")],
        [InlineKeyboardButton("✅ Готово", callback_data="edittaskattach:done")],
    ])
    await update.message.reply_text(f"📎 Добавлено (всего {count}).", reply_markup=keyboard)
    return EDIT_TASK_ATTACHMENT


async def edittask_attachment_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task_id = context.user_data["edit_task_id"]
    doc = update.message.document
    add_task_attachment(task_id, doc.file_id, "document")
    count = len(get_task_attachments(task_id))
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 Убрать все вложения", callback_data="edittaskattach:clear")],
        [InlineKeyboardButton("✅ Готово", callback_data="edittaskattach:done")],
    ])
    await update.message.reply_text(f"📎 Добавлено (всего {count}).", reply_markup=keyboard)
    return EDIT_TASK_ATTACHMENT


async def edittask_attachment_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = context.user_data["edit_task_id"]
    clear_task_attachments(task_id)
    await query.edit_message_text(
        "Все вложения убраны. Можешь прислать новые или нажать «Готово».",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Готово", callback_data="edittaskattach:done")]]),
    )
    return EDIT_TASK_ATTACHMENT


async def edittask_attachment_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = context.user_data.pop("edit_task_id")
    count = len(get_task_attachments(task_id))
    await query.edit_message_text(f"Готово ✅ Вложений сохранено: {count}.")
    return ConversationHandler.END


async def edittask_attachment_invalid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Это не похоже на фото или файл. Пришли картинку/документ, или воспользуйся "
        "кнопками выше."
    )
    return EDIT_TASK_ATTACHMENT


async def taskdesc_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split(":")[1])
    task = get_task(task_id)
    if not task or not task["description"]:
        await query.message.reply_text("Описания нет.")
        return
    if task["description_html"]:
        header = f"📝 [{html.escape(SUBJECT_NAME[task['subject']])}] {html.escape(task['title'])}:\n\n"
        await query.message.reply_text(
            header + task["description"], parse_mode=ParseMode.HTML
        )
    else:
        await query.message.reply_text(
            f"📝 [{SUBJECT_NAME[task['subject']]}] {task['title']}:\n\n{task['description']}"
        )
