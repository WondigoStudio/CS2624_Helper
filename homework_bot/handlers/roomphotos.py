"""Room-photo commands: attaching a photo/map to a room number
(/addroomphoto), listing saved rooms (/roomphotos), and a quick self-test
that resends a saved photo (/testphoto)."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from ..constants import ALL_USERS_SENTINEL
from ..db import all_chat_ids, get_room_photo, list_room_photos, register_chat, set_room_photo
from ..keyboards import target_user_keyboard
from ..permissions import is_admin
from ..states import PHOTO_ROOM_NAME, PHOTO_TARGET_USER, PHOTO_WAITING


async def addroomphoto_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    if is_admin(update.effective_user.id):
        await update.message.reply_text(
            "Для кого добавляем фото кабинета?", reply_markup=target_user_keyboard("phtgt")
        )
        return PHOTO_TARGET_USER

    context.user_data["photo_target_chat"] = update.effective_chat.id
    await update.message.reply_text(
        "Название/номер кабинета, для которого добавляем фото "
        "(пиши так же, как указывал в расписании, например «305»):"
    )
    return PHOTO_ROOM_NAME


async def addroomphoto_target_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    raw = query.data.split(":", 1)[1]
    if raw == "self":
        target = update.effective_chat.id
    elif raw == "all":
        target = ALL_USERS_SENTINEL
    else:
        target = int(raw)
    context.user_data["photo_target_chat"] = target
    await query.edit_message_text(
        "Название/номер кабинета, для которого добавляем фото "
        "(пиши так же, как указывал в расписании, например «305»):"
    )
    return PHOTO_ROOM_NAME


async def addroomphoto_room_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["photo_room"] = update.message.text.strip()
    await update.message.reply_text(
        "Теперь пришли фото — карту этажа или сам кабинет, где будет видно, где это находится."
    )
    return PHOTO_WAITING


def _save_room_photo_for_target(target, room: str, file_id: str, kind: str) -> str:
    if target == ALL_USERS_SENTINEL:
        targets = all_chat_ids()
        for chat_id in targets:
            set_room_photo(chat_id, room, file_id, kind=kind)
        return f" для всех известных пользователей ({len(targets)} чел.)"
    set_room_photo(target, room, file_id, kind=kind)
    return ""


async def addroomphoto_photo_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    room = context.user_data.pop("photo_room")
    target = context.user_data.pop("photo_target_chat")
    file_id = update.message.photo[-1].file_id
    who_line = _save_room_photo_for_target(target, room, file_id, kind="photo")
    await update.message.reply_text(f"Сохранено ✅ Фото для кабинета «{room}» добавлено{who_line}.")
    return ConversationHandler.END


async def addroomphoto_document_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc.mime_type or not doc.mime_type.startswith("image/"):
        await update.message.reply_text(
            "Этот файл не похож на изображение. Пришли фото/картинку кабинета."
        )
        return PHOTO_WAITING
    room = context.user_data.pop("photo_room")
    target = context.user_data.pop("photo_target_chat")
    who_line = _save_room_photo_for_target(target, room, doc.file_id, kind="document")
    await update.message.reply_text(f"Сохранено ✅ Фото для кабинета «{room}» добавлено{who_line}.")
    return ConversationHandler.END


async def addroomphoto_not_a_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Это не похоже на фото. Пришли картинку кабинета — как обычное фото "
        "или файлом-изображением (jpg/png)."
    )
    return PHOTO_WAITING


async def roomphotos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    rooms = list_room_photos(update.effective_chat.id)
    if not rooms:
        await update.message.reply_text(
            "Пока нет ни одного сохранённого фото кабинета. Добавь через /addroomphoto."
        )
        return
    await update.message.reply_text("Сохранённые фото кабинетов:\n" + "\n".join(f"• {r}" for r in rooms))


async def testphoto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    rooms = list_room_photos(update.effective_chat.id)
    if not rooms:
        await update.message.reply_text(
            "Пока нет ни одного сохранённого фото. Сначала добавь через /addroomphoto."
        )
        return
    context.user_data["testphoto_rooms"] = rooms
    buttons = [
        [InlineKeyboardButton(room, callback_data=f"testph:{i}")]
        for i, room in enumerate(rooms)
    ]
    await update.message.reply_text(
        "Какой кабинет проверить?", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def testphoto_chosen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.split(":", 1)[1])
    rooms = context.user_data.get("testphoto_rooms") or []
    if idx >= len(rooms):
        await query.message.reply_text("Список устарел, вызови /testphoto ещё раз.")
        return
    room = rooms[idx]
    chat_id = update.effective_chat.id
    photo = get_room_photo(chat_id, room)
    if not photo:
        await query.message.reply_text(f"Для кабинета «{room}» фото не найдено.")
        return
    file_id, kind = photo
    if kind == "document":
        await query.message.reply_document(document=file_id, caption=f"Кабинет {room} (тест)")
    else:
        await query.message.reply_photo(photo=file_id, caption=f"Кабинет {room} (тест)")


