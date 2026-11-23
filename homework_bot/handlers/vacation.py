"""/vacation — admin panel for vacation mode (the bot stops writing on its own)."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from .. import vacation as V
from ..db import register_chat
from ..permissions import is_admin

_HELP = (
    "🏖 <b>Режим каникул</b>\n\n"
    "Пока он включён, бот сам никому не пишет: ни утреннего расписания и заданий, ни напоминаний "
    "перед парами и о дедлайнах, ни утреннего опроса. Команды и приложение работают как обычно.\n"
)


def _panel():
    cfg = V.get()
    text = _HELP + f"\nСейчас: <b>{V.describe(cfg)}</b>"
    if cfg:
        text += "\nЛичные напоминания (/remind): " + ("тоже молчат" if cfg["personal"] else "приходят как обычно")
    rows = [
        [InlineKeyboardButton("7 дней", callback_data="vac:d7"), InlineKeyboardButton("14 дней", callback_data="vac:d14"),
         InlineKeyboardButton("30 дней", callback_data="vac:d30")],
        [InlineKeyboardButton("📅 Указать даты", callback_data="vac:custom"),
         InlineKeyboardButton("♾ Пока не выключу", callback_data="vac:open")],
    ]
    if cfg:
        rows.append([InlineKeyboardButton(
            "🔔 Личные напоминания: пусть приходят" if cfg["personal"] else "🔕 Заглушить и личные напоминания",
            callback_data="vac:personal")])
        rows.append([InlineKeyboardButton("✅ Выключить режим каникул", callback_data="vac:off")])
    return text, InlineKeyboardMarkup(rows)


async def vacation_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("Эта команда доступна только администраторам бота.")
        return
    context.user_data.pop("vac_wait", None)
    text, kb = _panel()
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=kb)


async def vacation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not is_admin(query.from_user.id):
        await query.answer("Только для администраторов.", show_alert=True)
        return
    action = query.data.split(":", 1)[1]
    cfg = V.get()
    personal = cfg.get("personal", False) if cfg else False
    note = None
    if action in ("d7", "d14", "d30"):
        V.save(*V.days_from_today(int(action[1:])), personal)
        note = "Режим каникул включён"
    elif action == "open":
        V.save(V.parse_period("бессрочно")[0], None, personal)
        note = "Включён без даты окончания"
    elif action == "off":
        V.clear()
        note = "Режим каникул выключен"
    elif action == "personal" and cfg:
        V.save(cfg["start"], cfg["end"], not cfg["personal"])
    elif action == "custom":
        context.user_data["vac_wait"] = True
        await query.answer()
        await query.message.reply_text(
            "Напиши период: <code>до 10.01</code> или <code>с 25.12 по 10.01</code> "
            "(год можно не писать), либо <code>бессрочно</code>.", parse_mode="HTML")
        return
    await query.answer(note)
    text, kb = _panel()
    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        pass


async def vacation_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """The dates typed after «Указать даты» (admins only, only while that step is open)."""
    if not context.user_data.get("vac_wait") or not update.message or not update.message.text:
        return
    if not is_admin(update.effective_user.id):
        return
    period = V.parse_period(update.message.text)
    if period is None:
        await update.message.reply_text(
            "Не понял даты. Примеры: «до 10.01», «с 25.12 по 10.01», «бессрочно». Или нажми /vacation заново.")
        return
    context.user_data.pop("vac_wait", None)
    cfg = V.get()
    V.save(period[0], period[1], cfg.get("personal", False) if cfg else False)
    text, kb = _panel()
    await update.message.reply_text("Готово ✅\n\n" + text, parse_mode="HTML", reply_markup=kb)

