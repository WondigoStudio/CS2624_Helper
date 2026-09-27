"""Homework-task commands: add/today/week/all/done/delete, the /edittask
conversation, and re-sending a task's attachment (/taskfile)."""

from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from ..config import SHARED_TASKS_ID
from ..constants import SUBJECT_NAME
from ..db import (
    add_task,
    delete_task,
    get_task,
    get_tasks,
    mark_done,
    register_chat,
    update_task_attachment,
    update_task_field,
)
from ..formatting import _overdue_block, user_short_name
from ..keyboards import _edit_task_field_keyboard, subject_keyboard
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
    context.user_data["new_description"] = update.message.text.strip()
    await update.message.reply_text(
        "Когда сдавать? Напиши дату в формате ДД.ММ или ДД.ММ.ГГГГ, "
        "либо просто «сегодня» / «завтра»."
    )
    return TYPING_DATE


async def add_description_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["new_description"] = None
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


async def _prompt_for_attachment(target_message):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Без вложения", callback_data="noattach")]
    ])
    await target_message(
        "Прикрепи фото или файл к заданию (скан условия, фото с доски и т.п.), "
        "или нажми кнопку, если вложение не нужно.",
        reply_markup=keyboard,
    )


async def _finish_add_task(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int,
    attachment_file_id: str = None, attachment_kind: str = None,
):
    subject = context.user_data.pop("new_subject")
    title = context.user_data.pop("new_title")
    description = context.user_data.pop("new_description", None)
    due_date_iso = context.user_data.pop("new_date")
    due_time = context.user_data.pop("new_time", "")
    creator_name = context.user_data.pop("new_creator", None)
    add_task(
        chat_id, subject, title, due_date_iso, due_time or None,
        created_by=creator_name, description=description,
        attachment_file_id=attachment_file_id, attachment_kind=attachment_kind,
    )
    d = datetime.strptime(due_date_iso, "%Y-%m-%d").date()
    time_part = f", {due_time}" if due_time else ""
    desc_part = f"\n📝 {description}" if description else ""
    attach_part = "\n📎 вложение сохранено" if attachment_file_id else ""
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
    text = await _finish_add_task(context, SHARED_TASKS_ID, file_id, "photo")
    await update.message.reply_text(text)
    return ConversationHandler.END


async def add_attachment_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    text = await _finish_add_task(context, SHARED_TASKS_ID, doc.file_id, "document")
    await update.message.reply_text(text)
    return ConversationHandler.END


async def add_attachment_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    text = await _finish_add_task(context, SHARED_TASKS_ID)
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
    rows = [r for r in get_tasks(SHARED_TASKS_ID, only_undone=False) if r["attachment_file_id"]]
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
    if not task or not task["attachment_file_id"]:
        await query.message.reply_text("Вложение не найдено.")
        return
    caption = f"[{SUBJECT_NAME[task['subject']]}] {task['title']}"
    if task["attachment_kind"] == "document":
        await query.message.reply_document(document=task["attachment_file_id"], caption=caption)
    else:
        await query.message.reply_photo(photo=task["attachment_file_id"], caption=caption)


async def edittask_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Убрать вложение", callback_data="edittaskattach:none")]
        ])
        await query.edit_message_text(
            "Пришли новое фото/файл вложения, или нажми кнопку, чтобы убрать его:",
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
    new_description = update.message.text.strip()
    update_task_field(task_id, "description", new_description)
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
    task_id = context.user_data.pop("edit_task_id")
    file_id = update.message.photo[-1].file_id
    update_task_attachment(task_id, file_id, "photo")
    await update.message.reply_text("Готово ✅ Вложение обновлено.")
    return ConversationHandler.END


async def edittask_attachment_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    task_id = context.user_data.pop("edit_task_id")
    doc = update.message.document
    update_task_attachment(task_id, doc.file_id, "document")
    await update.message.reply_text("Готово ✅ Вложение обновлено.")
    return ConversationHandler.END


async def edittask_attachment_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = context.user_data.pop("edit_task_id")
    update_task_attachment(task_id, None, None)
    await query.edit_message_text("Готово ✅ Вложение убрано.")
    return ConversationHandler.END


async def edittask_attachment_invalid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Это не похоже на фото или файл. Пришли картинку/документ, или нажми "
        "«Убрать вложение»."
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
    await query.message.reply_text(
        f"📝 [{SUBJECT_NAME[task['subject']]}] {task['title']}:\n\n{task['description']}"
    )


