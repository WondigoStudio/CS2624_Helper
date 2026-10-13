"""Anonymous messages: to a person from the bot's database or to a group.

Honest design: the recipient never sees who wrote; the author is revealed to
admins only when a message is reported (this is what the user is told). Safeguards: rate limit, confirmation before
sending, report / block buttons for the recipient, opt-out, 30-day retention.
"""

import html
import logging
import time
from datetime import timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from ..config import ADMIN_IDS
from ..db import (
    anon_block, anon_count_since, anon_create, anon_get, anon_is_blocked, anon_is_optout,
    anon_recent, anon_set_optout, anon_set_status, display_name, list_known_users, register_chat,
)
from ..permissions import is_admin
from ..utils import _chunk_text, now_kz

logger = logging.getLogger("anon")

MAX_LEN = 500
PER_HOUR = 5
STATE_TTL = 600  # seconds the half-finished draft is remembered

DISCLOSURE = (
    "🕶 <b>Анонимные сообщения</b>\n\n"
    "Получатель не увидит, кто написал. Автор раскрывается администраторам "
    "<b>только если на сообщение подали жалобу</b> (травля, угрозы, спам). "
    "Не пиши угроз и оскорблений.\n\n"
    f"Лимит: {PER_HOUR} сообщений в час, до {MAX_LEN} символов, только текст."
)


def _kb(*rows):
    return InlineKeyboardMarkup([[InlineKeyboardButton(t, callback_data=d) for t, d in r] for r in rows])
 
 
def _state(context):
    st = context.user_data.get("anon")
    if st and time.time() - st.get("ts", 0) > STATE_TTL:
        context.user_data.pop("anon", None)
        return None
    return st
 
 
def _set(context, **kw):
    kw["ts"] = time.time()
    context.user_data["anon"] = kw
 
 
async def anon_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    if update.effective_chat.type != "private":
        await update.message.reply_text("Анонимные сообщения отправляются в личке с ботом: напиши мне /anon 🕶")
        return
    context.user_data.pop("anon", None)
    await update.message.reply_text(
        DISCLOSURE, parse_mode=ParseMode.HTML,
        reply_markup=_kb([("👤 Человеку", "anon:u"), ("👥 В группу", "anon:g")], [("✖ Отмена", "anon:cancel")]),
    )
 
 
async def anon_off_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    anon_set_optout(update.effective_user.id, True)
    await update.message.reply_text("Готово: анонимные сообщения тебе больше не придут. Вернуть: /anon_on")
 
 
async def anon_on_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    anon_set_optout(update.effective_user.id, False)
    await update.message.reply_text("Хорошо, анонимные сообщения снова можно присылать тебе.")
 
 
async def anon_log_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Эта команда доступна только администраторам бота.")
        return
    limit = 30
    if context.args and context.args[0].isdigit():
        limit = min(int(context.args[0]), 200)
    names = {r["chat_id"]: display_name(r) for r in list_known_users()}
    rows = [r for r in anon_recent(500) if r["status"] == "reported"][:limit]
    if not rows:
        await update.message.reply_text("Жалоб на анонимные сообщения пока нет.")
        return
    lines = []
    for r in rows:
        who = html.escape(names.get(r["sender_id"], f"id{r['sender_id']}"))
        to = html.escape(names.get(r["target_id"], f"id{r['target_id']}"))
        if r["target_kind"] == "group":
            to = "👥 " + to
        lines.append(
            f"#{r['id']} · {r['ts'][5:16].replace('T', ' ')} · <b>{who}</b> → <b>{to}</b> [{r['status']}]\n"
            f"{html.escape(r['text'])}"
        )
    for chunk in _chunk_text("\n\n".join(lines)):
        await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)
 
 
async def anon_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data.split(":")
    action = data[1]
    uid = update.effective_user.id
    bot = context.bot
 
    # --- recipient side: reply / block / report ---------------------------
    if action in ("r", "b", "rep"):
        msg = anon_get(int(data[2]))
        if not msg or (action != "rep" and msg["target_id"] != uid):
            await query.answer("Сообщение не найдено", show_alert=True)
            return
        if action == "b":
            anon_block(msg["target_id"], msg["sender_id"])
            logger.info("[ANON] block: target=%s blocked sender=%s (msg #%s)", msg["target_id"], msg["sender_id"], msg["id"])
            await query.answer("Отправитель заблокирован: его сообщения тебе больше не придут.", show_alert=True)
        elif action == "rep":
            anon_set_status(msg["id"], "reported")
            logger.warning("[ANON] REPORT msg #%s sender=%s target=%s by=%s", msg["id"], msg["sender_id"], msg["target_id"], uid)
            for admin in ADMIN_IDS:
                try:
                    await bot.send_message(
                        admin,
                        f"⚠️ Жалоба на анонимное сообщение #{msg['id']}\nОт: <code>{msg['sender_id']}</code> → "
                        f"<code>{msg['target_id']}</code>\n\n{html.escape(msg['text'])}",
                        parse_mode=ParseMode.HTML,
                    )
                except Exception:
                    pass
            await query.answer("Жалоба отправлена администраторам.", show_alert=True)
        else:  # reply
            if msg["target_kind"] != "user":
                await query.answer()
                return
            _set(context, step="text", kind="user", target=msg["sender_id"], reply=True)
            await query.answer()
            await bot.send_message(uid, f"Напиши ответ (до {MAX_LEN} символов). Он уйдёт анонимно 🕶\n/anon — отмена не нужна, просто не пиши.")
        return
 
    # --- sender side -------------------------------------------------------
    if query.message.chat.type != "private":
        await query.answer()
        return
    await query.answer()
    if action == "cancel":
        context.user_data.pop("anon", None)
        await query.edit_message_text("Отменено.")
    elif action == "u":
        _set(context, step="pick_user")
        await query.edit_message_text("Кому? Напиши имя или @username человека (он должен пользоваться ботом).")
    elif action == "g":
        groups = []
        for r in list_known_users():
            if r["chat_id"] < 0:
                try:
                    member = await bot.get_chat_member(r["chat_id"], uid)
                    if member.status in ("left", "kicked"):
                        continue
                    chat = await bot.get_chat(r["chat_id"])
                    groups.append((r["chat_id"], chat.title or str(r["chat_id"])))
                except Exception:
                    continue
        if not groups:
            await query.edit_message_text("Не нашёл групп, где есть и ты, и бот.")
            return
        rows = [[(f"👥 {t[:40]}", f"anon:to:g{cid}")] for cid, t in groups[:20]]
        await query.edit_message_text("В какую группу?", reply_markup=_kb(*rows, [("✖ Отмена", "anon:cancel")]))
    elif action == "to":
        raw = data[2]
        kind = "group" if raw.startswith("g") else "user"
        target = int(raw.lstrip("g"))
        if kind == "user" and anon_is_optout(target):
            await query.edit_message_text("Этот человек не принимает анонимные сообщения.")
            return
        _set(context, step="text", kind=kind, target=target)
        await query.edit_message_text(f"Напиши текст сообщения (до {MAX_LEN} символов). Пришлю предпросмотр перед отправкой.")
    elif action == "send":
        st = _state(context)
        if not st or st.get("step") != "confirm":
            await query.edit_message_text("Черновик устарел. Начни заново: /anon")
            return
        await _deliver(query, context, st)
    else:
        await query.answer()
 
 
async def _deliver(query, context, st):
    uid = query.from_user.id
    since = (now_kz() - timedelta(hours=1)).isoformat()
    if anon_count_since(uid, since) >= PER_HOUR:
        await query.edit_message_text(f"Лимит {PER_HOUR} анонимных сообщений в час, попробуй позже.")
        context.user_data.pop("anon", None)
        return
    kind, target, text = st["kind"], st["target"], st["text"]
    msg_id = anon_create(uid, target, kind, text)
    context.user_data.pop("anon", None)
    names = {r["chat_id"]: display_name(r) for r in list_known_users()}
    logger.warning(
        "[ANON] #%s %s(%s) -> %s(%s) [%s]: %r",
        msg_id, names.get(uid, "?"), uid, names.get(target, "?"), target, kind, text,
    )
    if kind == "user" and anon_is_blocked(target, uid):
        anon_set_status(msg_id, "blocked")
        await query.edit_message_text("✅ Отправлено анонимно.")  # the sender is not told about the block
        return
    body = f"🕶 <b>Анонимное сообщение</b>\n\n{html.escape(text)}"
    if kind == "user":
        kb = _kb([("↩️ Ответить анонимно", f"anon:r:{msg_id}")], [("⚠️ Пожаловаться", f"anon:rep:{msg_id}")])
    else:
        kb = _kb([("⚠️ Пожаловаться", f"anon:rep:{msg_id}")])
    try:
        await context.bot.send_message(target, body, parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception as exc:
        anon_set_status(msg_id, "failed")
        logger.warning("[ANON] #%s delivery failed: %s", msg_id, exc)
        await query.edit_message_text("Не получилось доставить: человек не запускал бота или заблокировал его.")
        return
    await query.edit_message_text("✅ Отправлено анонимно.")
 
 
async def anon_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Free text in a private chat: only reacts while an /anon draft is open."""
    if update.effective_chat.type != "private":
        return
    st = _state(context)
    if not st:
        return
    text = (update.message.text or "").strip()
    uid = update.effective_user.id
    if st["step"] == "pick_user":
        needle = text.lstrip("@").lower()
        found = [
            r for r in list_known_users()
            if r["chat_id"] > 0 and r["chat_id"] != uid and not anon_is_optout(r["chat_id"])
            and needle and needle in display_name(r).lower()
        ][:8]
        if not found:
            await update.message.reply_text("Не нашёл такого среди пользователей бота. Попробуй другое имя или @username.")
            return
        rows = [[(display_name(r)[:48], f"anon:to:{r['chat_id']}")] for r in found]
        await update.message.reply_text("Кого выбрать?", reply_markup=_kb(*rows, [("✖ Отмена", "anon:cancel")]))
    elif st["step"] == "text":
        if not text:
            return
        if len(text) > MAX_LEN:
            await update.message.reply_text(f"Слишком длинно ({len(text)}). Максимум {MAX_LEN}, сократи.")
            return
        _set(context, step="confirm", kind=st["kind"], target=st["target"], text=text)
        await update.message.reply_text(
            f"Предпросмотр:\n\n<i>{html.escape(text)}</i>\n\nОтправить анонимно? (админы бота видят автора)",
            parse_mode=ParseMode.HTML,
            reply_markup=_kb([("✅ Отправить", "anon:send"), ("✖ Отмена", "anon:cancel")]),
        )
