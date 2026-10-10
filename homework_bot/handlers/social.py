"""Group-chat fun & utility: tracking members for /call, the "call
everyone" and morning-poll opt-in commands, and the reply-with-a-word fun
actions (обнять, ударить, ...)."""

import asyncio
import html
import random
import time

from telegram import MessageEntity, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ..config import logger
from ..constants import ACTIONS, CUSTOM_EMOJI_IDS
from ..db import db, display_name, get_chat_info, log_action_and_count, register_chat, top_actions
from ..formatting import short_name
from ..utils import now_kz, reply_text_chunked


_member_seen = {}  # (chat_id, user_id) -> (first_name, monotonic time of the last write)
_MEMBER_REFRESH_SECONDS = 6 * 3600


async def track_group_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Автоматически сохраняет обычных участников группы в БД при их активности.
    Запись делается только когда человек новый, сменил имя или давно не обновлялся —
    а не на каждое сообщение в группе."""
    chat = update.effective_chat
    user = update.effective_user

    if chat and chat.type in ("group", "supergroup") and user and not user.is_bot:
        key = (chat.id, user.id)
        seen = _member_seen.get(key)
        if seen and seen[0] == user.first_name and time.monotonic() - seen[1] < _MEMBER_REFRESH_SECONDS:
            return
        _member_seen[key] = (user.first_name, time.monotonic())
        await asyncio.to_thread(_save_member, chat.id, user.id, user.first_name)


def _save_member(chat_id: int, user_id: int, first_name):
    conn = db()
    conn.execute(
        """
        INSERT INTO group_members (chat_id, user_id, first_name, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(chat_id, user_id) DO UPDATE SET
            first_name = excluded.first_name,
            updated_at = excluded.updated_at
        """,
        (chat_id, user_id, first_name, now_kz().isoformat()),
    )
    conn.commit()
    conn.close()


async def call_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Эта команда работает только в группах!")
        return

    users_to_tag = {}

    # 1. Получаем всех админов напрямую из Telegram
    try:
        admins = await context.bot.get_chat_administrators(chat.id)
        for a in admins:
            if not a.user.is_bot:
                users_to_tag[a.user.id] = a.user.first_name or "Участник"
    except Exception as e:
        logger.warning(f"Не удалось получить список админов: {e}")

    # 2. Добавляем ВСЕХ обычных участников, которые есть в нашей БД
    conn = db()
    try:
        rows = conn.execute(
            "SELECT user_id, first_name FROM group_members WHERE chat_id = ?", 
            (chat.id,)
        ).fetchall()
        for r in rows:
            users_to_tag[r["user_id"]] = r["first_name"] or "Участник"
    except Exception as e:
        logger.warning(f"Ошибка при чтении участников из БД: {e}")
    finally:
        conn.close()

    if not users_to_tag:
        await update.message.reply_text("Пока нет участников для вызова.")
        return

    # Формируем кликабельные тэги для каждого человека
    mentions = [
        f'<a href="tg://user?id={uid}">{html.escape(name)}</a>'
        for uid, name in users_to_tag.items()
    ]

    text = "📢 <b>ОБЩИЙ СОЗЫВ!</b>\n\n" + " ".join(mentions)
    await reply_text_chunked(update.message, text, parse_mode=ParseMode.HTML)
async def set_report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    if update.effective_chat.type not in ("group", "supergroup"):
        await update.message.reply_text("Эту команду можно использовать только в группе!")
        return
    
    chat_id = update.effective_chat.id
    conn = db()
    conn.execute(
        "INSERT INTO report_chats (chat_id, enabled, created_at) VALUES (?, 1, ?) "
        "ON CONFLICT(chat_id) DO UPDATE SET enabled = 1",
        (chat_id, now_kz().isoformat())
    )
    conn.commit()
    conn.close()
    await update.message.reply_text("Утренний опрос (07:45 со ВТ по СБ) с закрепом и созывом активирован! 📌")

async def handle_action_reply(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str):
    msg = update.message
    original = msg.reply_to_message
    target_user = original.from_user if original else None
    if target_user is None:
        return

    emoji, phrases = ACTIONS[action]
    
    # Чтобы правильно рассчитать смещения ссылок, формируем имена до сборки HTML-строки
    actor_name = short_name(update.effective_user)
    target_name = short_name(target_user)
    
    # Берем случайную фразу и подставляем чистые имена (без HTML-тегов)
    raw_phrase = random.choice(phrases).format(a=actor_name, t=target_name)
    
    count = log_action_and_count(
        update.effective_chat.id, update.effective_user.id, target_user.id, action
    )
    tail = f" (уже {count}-й раз!)" if count > 1 else ""
    
    # Собираем чистый текст без тегов, который увидит пользователь
    # Формат: "Эмодзи[пробел]Фраза[хвост с количеством]"
    full_text = f"{emoji} {raw_phrase}{tail}"

    # Считаем длину смайлика и пробела в UTF-16 (для Telegram API)
    utf16_emoji_length = len(emoji.encode('utf-16-le')) // 2
    utf16_prefix_offset = utf16_emoji_length + 1  # учитываем пробел после эмодзи

    from telegram import MessageEntity

    # Массив для хранения сущностей (кастомного эмодзи и кликабельных имен)
    entities = []

    # 1. Если для действия задан премиум-эмодзи, добавляем его сущность на позицию 0
    custom_id = CUSTOM_EMOJI_IDS.get(action)
    if custom_id:
        entities.append(
            MessageEntity(
                type=MessageEntity.CUSTOM_EMOJI,
                offset=0,
                length=utf16_emoji_length,
                custom_emoji_id=str(custom_id)
            )
        )

    # 2. Математически находим точные позиции имен внутри фразы для создания нативных ссылок
    # Ищем, где во фразе начинается имя инициатора (actor)
    actor_idx = raw_phrase.find(actor_name)
    if actor_idx != -1:
        # Переводим индекс начала в UTF-16 смещение
        actor_offset = len(raw_phrase[:actor_idx].encode('utf-16-le')) // 2
        actor_length = len(actor_name.encode('utf-16-le')) // 2
        entities.append(
            MessageEntity(
                type=MessageEntity.TEXT_MENTION,
                offset=utf16_prefix_offset + actor_offset,
                length=actor_length,
                user=update.effective_user
            )
        )

    # Ищем, где во фразе начинается имя цели (target)
    target_idx = raw_phrase.find(target_name)
    if target_idx != -1:
        # Переводим индекс начала в UTF-16 смещение
        target_offset = len(raw_phrase[:target_idx].encode('utf-16-le')) // 2
        target_length = len(target_name.encode('utf-16-le')) // 2
        entities.append(
            MessageEntity(
                type=MessageEntity.TEXT_MENTION,
                offset=utf16_prefix_offset + target_offset,
                length=target_length,
                user=target_user
            )
        )

    # Отправляем сообщение: текст собран, сущности размечены, никаких скрытых отправлений в чат 0
    await msg.reply_text(
        text=full_text,
        entities=entities
    )




async def topactions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    rows = top_actions(update.effective_chat.id)
    if not rows:
        await update.message.reply_text(
            "Пока никто никого не обнял, не ударил и вообще ничего не делал 🙂"
        )
        return
    lines = ["🏆 Топ действий в этом чате:"]
    for i, r in enumerate(rows, start=1):
        info_a = get_chat_info(r["actor_id"])
        info_t = get_chat_info(r["target_id"])
        name_a = display_name(info_a) if info_a else f"id{r['actor_id']}"
        name_t = display_name(info_t) if info_t else f"id{r['target_id']}"
        emoji = ACTIONS.get(r["action"], ("🎲", None))[0]
        lines.append(f"{i}. {emoji} {name_a} → {r['action']} → {name_t}: {r['c']} раз(а)")
    await update.message.reply_text("\n".join(lines))




async def sethere_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/sethere — in a group (or one of its topics): send the bot's daily
    messages (schedule, weather, homework, reminders, poll) to THIS place."""
    from ..db import set_chat_thread
    from ..permissions import is_admin

    register_chat(update)
    chat, user, msg = update.effective_chat, update.effective_user, update.message
    if chat.type not in ("group", "supergroup"):
        await msg.reply_text("Эту команду нужно писать в группе — в том топике, куда присылать напоминания.")
        return
    allowed = is_admin(user.id)
    if not allowed:
        try:
            member = await context.bot.get_chat_member(chat.id, user.id)
            allowed = member.status in ("creator", "administrator")
        except Exception:
            allowed = False
    if not allowed:
        await msg.reply_text("Назначать место для напоминаний могут только админы группы.")
        return
    thread = msg.message_thread_id if msg.is_topic_message else None
    set_chat_thread(chat.id, thread)
    if thread:
        await msg.reply_text("✅ Готово: напоминания, расписание, погода и опрос теперь приходят в этот топик.")
    elif getattr(chat, "is_forum", False):
        await msg.reply_text("✅ Напоминания будут приходить в основной топик (General).")
    else:
        await msg.reply_text("✅ Напоминания приходят в этот чат. (В группе с топиками напиши /sethere в нужном топике.)")
