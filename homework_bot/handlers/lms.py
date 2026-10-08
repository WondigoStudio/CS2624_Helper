"""/lms — each person connects their own LMS (Moodle) calendar link; the bot
then pulls deadlines from it into the shared task list (see lms_sync.py).

The link holds a personal access token, so it can only be sent in a private
chat, and the message carrying it is deleted right after it is read.
/lmsoff disconnects; /lmssync pulls right now (admins: everyone's feeds).
"""

import asyncio

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from ..config import LMS_HOST
from ..db import delete_lms_feed, get_lms_feed, register_chat, save_lms_feed
from ..lms_sync import sync_all_feeds, sync_feed, validate_feed_url
from ..permissions import is_admin, is_schedule_allowed
from ..states import LMS_URL

_HOW_TO = (
    f"Как получить ссылку (на {LMS_HOST}):\n"
    "1. Открой Calendar → Export calendar.\n"
    "2. Events to export: All events. Time period: Recent and next 60 days.\n"
    "3. Нажми «Get calendar URL» → «Copy URL».\n"
    "4. Пришли эту ссылку сюда одним сообщением.\n\n"
    "🔒 В ссылке есть твой личный токен, поэтому я удалю это сообщение из чата "
    "сразу после того, как прочитаю. /cancel — отмена."
)


def _format_report(report: dict) -> str:
    lines = [f"Событий в календаре: {report['events']}"]
    if report["added"]:
        lines.append(f"\n➕ Добавлено заданий ({len(report['added'])}):")
        lines += [f"• {t}" for t in report["added"][:20]]
        if len(report["added"]) > 20:
            lines.append(f"…и ещё {len(report['added']) - 20}")
    if report["updated"]:
        lines.append(f"\n🔄 Обновлено ({len(report['updated'])}):")
        lines += [f"• {t}" for t in report["updated"][:10]]
    if not report["added"] and not report["updated"]:
        lines.append("Новых дедлайнов нет — всё уже в списке.")
    if report["unknown_subject"]:
        lines.append("\n⚠️ Не знаю предмет для: " + "; ".join(report["unknown_subject"][:5]))
    return "\n".join(lines)


async def lms_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    if update.effective_chat.type != "private":
        await update.message.reply_text(
            "Ссылку с личным токеном нельзя присылать в группу. Напиши мне в личку и нажми /lms."
        )
        return ConversationHandler.END
    if not is_schedule_allowed(update.effective_user.id):
        await update.message.reply_text("У вас нет доступа к управлению заданиями.")
        return ConversationHandler.END
    feed = get_lms_feed(update.effective_user.id)
    head = (
        "✅ Календарь LMS уже подключён — дедлайны обновляются каждые 10 минут. "
        "Чтобы заменить ссылку, пришли новую. Отключить: /lmsoff.\n\n"
        if feed else
        "Подключим календарь LMS: бот сам будет забирать дедлайны в список заданий.\n\n"
    )
    await update.message.reply_text(head + _HOW_TO)
    return LMS_URL


async def lms_url_typed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    url = validate_feed_url(msg.text)
    # whatever it was, take the (possibly token-bearing) message out of the chat
    try:
        await msg.delete()
    except Exception:
        pass
    chat = update.effective_chat
    if url is None:
        await context.bot.send_message(
            chat.id,
            f"Это не похоже на ссылку календаря. Нужна ссылка с {LMS_HOST}, вида "
            "…/calendar/export_execute.php?userid=…&authtoken=… — пришли её ещё раз или /cancel.",
        )
        return LMS_URL
    status = await context.bot.send_message(chat.id, "⏳ Проверяю ссылку и забираю дедлайны…")
    try:
        report = await asyncio.to_thread(sync_feed, url)
    except Exception as e:
        await status.edit_text(f"❌ Не получилось: {e}\nПришли ссылку ещё раз или /cancel.")
        return LMS_URL
    save_lms_feed(update.effective_user.id, url)
    await status.edit_text(
        "✅ Календарь подключён. Дальше я сам проверяю его каждые 10 минут. "
        "Сообщение со ссылкой удалено.\n\n" + _format_report(report)
    )
    return ConversationHandler.END


async def lms_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Отменено.")
    return ConversationHandler.END


async def lmsoff_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    if delete_lms_feed(update.effective_user.id):
        await update.message.reply_text(
            "Календарь LMS отключён. Уже добавленные задания остаются в списке."
        )
    else:
        await update.message.reply_text("Календарь LMS у тебя и не был подключён (/lms).")


async def lmssync_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pull deadlines right now. Your own feed; for an admin, everyone's."""
    register_chat(update)
    user_id = update.effective_user.id
    if is_admin(user_id):
        status = await update.message.reply_text("⏳ Обновляю календари всех подключённых…")
        results = await asyncio.to_thread(sync_all_feeds)
        if not results:
            await status.edit_text("Пока никто не подключил календарь LMS (/lms).")
            return
        added = sum(len(r["added"]) for _, r, e in results if r)
        failed = sum(1 for _, _, e in results if e)
        text = f"✅ Календарей: {len(results)}, добавлено заданий: {added}"
        if failed:
            text += f"\n⚠️ Не удалось обновить: {failed}"
        await status.edit_text(text)
        return
    feed = get_lms_feed(user_id)
    if not feed:
        await update.message.reply_text("Календарь LMS не подключён — сделай это командой /lms.")
        return
    status = await update.message.reply_text("⏳ Забираю дедлайны из LMS…")
    try:
        report = await asyncio.to_thread(sync_feed, feed["url"])
    except Exception as e:
        await status.edit_text(f"❌ Не получилось: {e}")
        return
    await status.edit_text("✅ Готово.\n\n" + _format_report(report))
