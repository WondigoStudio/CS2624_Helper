"""GPA tools in the bot: converter, semester calculator, cumulative GPA, attendance.

Each tool is a small "mode" kept in context.user_data["gpa"]: after pressing the menu button
the bot waits for the next text message(s) and answers each one, until another menu button is
pressed, «Готово» is tapped, or 15 minutes pass. The same maths runs in the Mini App.
"""

import html
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from .. import gpa as G
from ..config import WEBAPP_URL
from ..db import register_chat

MODE_TTL = 15 * 60

PROMPTS = {
    "calc": (
        "🧮 <b>Калькулятор GPA</b>\n\n"
        "Отправь оценки и кредиты предметов через запятую.\n"
        "Формат: <code>Оценка Кредиты</code>\n"
        "Пример: <code>90 3, 85 4, 95 2</code>\n\n"
        "Оценка — баллы 0–100 или буква (<code>A-</code>, <code>B+</code>…). Можно по одной строке на предмет."
    ),
    "conv": (
        "🔄 <b>Конвертер GPA</b>\n\n"
        "Отправь балл от 0 до 100 — покажу GPA и буквенную оценку.\n"
        "Можно и наоборот: отправь букву (например <code>B+</code>) — покажу диапазон баллов."
    ),
    "cum_prev": (
        "📈 <b>Кумулятивный GPA</b>\n\n"
        "Сначала — что у тебя уже есть. Отправь текущий GPA и сколько кредитов набрано.\n"
        "Пример: <code>3.45 60</code>\n"
        "Если это твой первый семестр — отправь <code>0 0</code>."
    ),
    "att": (
        "🏫 <b>Посещаемость</b>\n\n"
        "Отправь два числа: сколько всего занятий по предмету за курс и сколько ты пропустил.\n"
        "Пример: <code>30 5</code>\n"
        f"Правило из силлабусов: пропустить можно не больше {G.ATTENDANCE_LIMIT_PCT}% занятий "
        "(опоздание больше 5 минут = пропуск)."
    ),
}
_CUM_SEM_PROMPT = (
    "Теперь оценки за этот семестр: <code>Оценка Кредиты</code> через запятую.\n"
    "Пример: <code>90 3, 85 4, 95 2</code>"
)

INSTRUCTION = (
    "📖 <b>Как пользоваться</b>\n\n"
    "🎯 <b>Итоговая оценка</b> — подробный расчёт по каждому предмету по силлабусу (все работы, "
    "аттестации, финал, прогноз и «сколько нужно на финале»). Он живёт в приложении, вкладка «Баллы».\n\n"
    "🧮 <b>Калькулятор GPA</b> — семестровый GPA по оценкам и кредитам.\n"
    "📈 <b>Кумулятивный GPA</b> — общий GPA с учётом прошлых семестров.\n"
    "🔄 <b>Конвертер GPA</b> — балл ↔ буква ↔ GPA.\n"
    "🏫 <b>Посещаемость</b> — сколько занятий ещё можно пропустить.\n"
    "📊 <b>Шкала оценок</b> — таблица баллов, букв и GPA.\n\n"
    "Формулы:\n"
    "• семестровый GPA = Σ(GPA × кредиты) / Σ кредиты\n"
    "• кумулятивный = (прошлый GPA × прошлые кредиты + семестровый × кредиты семестра) / все кредиты\n\n"
    "Все те же инструменты есть в приложении: «Баллы» → «GPA» (там ещё запоминаются введённые данные). "
    "Расчёты ориентировочные — официальные оценки смотри в LMS."
)


def _fmt(x: float, nd: int = 2) -> str:
    s = f"{x:.{nd}f}".rstrip("0").rstrip(".")
    return s or "0"


def _gpa(x: float) -> str:
    return f"{x:.2f}"


def _done_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("✖ Готово", callback_data="gpa:stop")]])


def webapp_url(tab: str) -> str:
    sep = "&" if "?" in WEBAPP_URL else "?"
    return f"{WEBAPP_URL}{sep}tab={tab}"


def _app_button(update: Update, tab: str, label: str):
    """Inline row opening the Mini App on a tab (private chats only — web_app buttons need them)."""
    if WEBAPP_URL and update.effective_chat and update.effective_chat.type == "private":
        return [[InlineKeyboardButton(label, web_app=WebAppInfo(url=webapp_url(tab)))]]
    return []


def set_mode(context: ContextTypes.DEFAULT_TYPE, mode: str, **extra):
    context.user_data["gpa"] = {"mode": mode, "ts": time.time(), **extra}


def clear_mode(context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("gpa", None)


def _active(context: ContextTypes.DEFAULT_TYPE):
    st = context.user_data.get("gpa")
    if not st:
        return None
    if time.time() - st.get("ts", 0) > MODE_TTL:
        clear_mode(context)
        return None
    return st


# ---------------------------------------------------------------- menu actions
async def start_mode(update: Update, context: ContextTypes.DEFAULT_TYPE, mode: str):
    register_chat(update)
    set_mode(context, mode)
    kb = _done_kb()
    await update.message.reply_text(PROMPTS[mode], parse_mode=ParseMode.HTML, reply_markup=kb)


async def calc_start(update, context):
    await start_mode(update, context, "calc")


async def conv_start(update, context):
    await start_mode(update, context, "conv")


async def cum_start(update, context):
    await start_mode(update, context, "cum_prev")


async def att_start(update, context):
    await start_mode(update, context, "att")


async def final_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    clear_mode(context)
    rows = _app_button(update, "grades", "🎯 Открыть калькулятор баллов")
    text = (
        "🎯 <b>Итоговая оценка</b>\n\n"
        "Подробный расчёт по каждому предмету — работы, аттестации, финал, прогноз и "
        "«сколько нужно набрать на финале, чтобы получить нужную оценку» — "
        "находится в приложении (вкладка «Баллы»)."
    )
    if not rows:
        text += "\n\nОткрой его в личке с ботом: команда /app."
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows) if rows else None)


def scale_text() -> str:
    lines = ["Балл    Буква  GPA   Оценка"]
    for i, (m, name, g, trad, _s) in enumerate(G.SCALE):
        hi = 100 if i == 0 else G.SCALE[i - 1][0] - 1
        rng = f"{m}–{hi}"
        lines.append(f"{rng:<8}{name:<7}{_gpa(g):<6}{trad}")
    return "📊 <b>Шкала оценок</b>\n<pre>" + "\n".join(lines) + "</pre>"


async def scale_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    await update.message.reply_text(scale_text(), parse_mode=ParseMode.HTML)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    rows = _app_button(update, "gpa", "📱 GPA в приложении")
    await update.message.reply_text(INSTRUCTION, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(rows) if rows else None)


# ---------------------------------------------------------------- results
def convert_text(text: str):
    """Reply for the converter, or None when the text is neither a score nor a letter."""
    raw = text.strip().replace("%", "").strip()
    num = G._number(raw)
    if num is not None:
        if not 0 <= num <= 100:
            return "Балл должен быть от 0 до 100. Попробуй ещё раз."
        r = G.grade_for(num)
        return (
            "🔄 <b>Конвертер оценки</b>\n\n"
            f"Балл: <b>{_fmt(num)}%</b>\n"
            f"GPA: <b>{_gpa(r['gpa'])}</b> / 4.00\n"
            f"Буквенная оценка: <b>{r['letter']}</b>\n"
            f"Традиционная шкала: <b>{r['trad']}</b>\n"
            f"Статус: {html.escape(r['status'])}"
        )
    row = G.letter_row(raw)
    if row is None:
        return None
    return (
        "🔄 <b>Конвертер оценки</b>\n\n"
        f"Буквенная оценка: <b>{row['letter']}</b>\n"
        f"Баллы: <b>{G.letter_range(row['letter'])}%</b>\n"
        f"GPA: <b>{_gpa(row['gpa'])}</b> / 4.00\n"
        f"Традиционная шкала: <b>{row['trad']}</b>\n"
        f"Статус: {html.escape(row['status'])}"
    )


def _items_text(items) -> str:
    lines = []
    for n, it in enumerate(items, 1):
        mark = f"{_fmt(it['score'])}%" if it["score"] is not None else it["letter"]
        lines.append(f"{n}. {mark} → {it['letter']} ({_gpa(it['gpa'])}) · {_fmt(it['credits'])} кр.")
    return "\n".join(lines)


def _errors_text(errors) -> str:
    return "\n\n⚠️ " + "\n⚠️ ".join(html.escape(e) for e in errors) if errors else ""


def calc_text(text: str):
    items, errors = G.parse_pairs(text)
    if not items:
        return "❌ " + "\n".join(html.escape(e) for e in errors) + "\n\nПример: <code>90 3, 85 4, 95 2</code>"
    sem = G.semester_gpa(items)
    out = (
        "🧮 <b>Семестровый GPA</b>\n\n" + html.escape(_items_text(items)) + "\n\n"
        f"📊 GPA: <b>{_gpa(sem['gpa'])}</b> / 4.00\n"
        f"Кредитов: {_fmt(sem['credits'])}"
    )
    if sem["avg_percent"] is not None:
        out += f" · средний балл: {_fmt(sem['avg_percent'], 1)}%"
    return out + _errors_text(errors) + "\n\nМожно отправить другой список."


def cum_text(text: str, prev):
    items, errors = G.parse_pairs(text)
    if not items:
        return "❌ " + "\n".join(html.escape(e) for e in errors) + "\n\nПример: <code>90 3, 85 4, 95 2</code>"
    sem = G.semester_gpa(items)
    pg, pc = prev
    cum = G.cumulative_gpa(pg, pc, sem["gpa"], sem["credits"])
    return (
        "📈 <b>Кумулятивный GPA</b>\n\n" + html.escape(_items_text(items)) + "\n\n"
        f"Семестр: GPA <b>{_gpa(sem['gpa'])}</b> · {_fmt(sem['credits'])} кр.\n"
        f"Было: GPA <b>{_gpa(pg)}</b> · {_fmt(pc)} кр.\n\n"
        f"🎓 Итого: GPA <b>{_gpa(cum['gpa'])}</b> / 4.00 · {_fmt(cum['credits'])} кр."
        + _errors_text(errors) + "\n\nМожно отправить другой список за семестр."
    )


def attendance_text(total: int, missed: int) -> str:
    a = G.attendance(total, missed)
    head = (
        "🏫 <b>Посещаемость</b>\n\n"
        f"Всего занятий: {a['total']}\n"
        f"Пропущено: <b>{a['missed']}</b> ({_fmt(a['pct'], 1)}%)\n"
        f"Был на занятиях: {_fmt(a['present_pct'], 1)}%\n"
        f"Можно пропустить максимум: {a['allowed']} ({a['limit_pct']}%)\n\n"
    )
    if a["status"] == "bad":
        return head + f"❌ Лимит превышен на {-a['left']} — по правилам силлабуса это F или повтор курса. Поговори с преподавателем."
    if a["status"] == "edge":
        return head + (f"⚠️ Осталось пропусков: <b>{a['left']}</b>. Ещё одна лишняя — и это уже край." if a["left"] == 1
                       else "⚠️ Лимит исчерпан: следующий пропуск уже будет лишним.")
    return head + f"✅ Можно пропустить ещё: <b>{a['left']}</b>."


# ---------------------------------------------------------------- free text while a mode is open
async def gpa_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st = _active(context)
    if not st or not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    mode = st["mode"]
    st["ts"] = time.time()
    reply = None
    if mode == "conv":
        reply = convert_text(text)
        if reply is None:
            reply = "Не понял. Отправь балл от 0 до 100 (например <code>87.5</code>) или букву (например <code>B+</code>)."
    elif mode == "calc":
        reply = calc_text(text)
    elif mode == "cum_prev":
        parsed = G.parse_prev(text)
        if isinstance(parsed, str):
            reply = "❌ " + html.escape(parsed)
        else:
            st["mode"], st["prev"] = "cum_sem", parsed
            reply = f"Записал: GPA <b>{_gpa(parsed[0])}</b>, кредитов <b>{_fmt(parsed[1])}</b>.\n\n" + _CUM_SEM_PROMPT
    elif mode == "cum_sem":
        reply = cum_text(text, st["prev"])
    elif mode == "att":
        parsed = G.parse_attendance(text)
        reply = "❌ " + html.escape(parsed) if isinstance(parsed, str) else attendance_text(*parsed)
    if reply:
        await update.message.reply_text(reply, parse_mode=ParseMode.HTML, reply_markup=_done_kb())


async def gpa_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "gpa:stop":
        clear_mode(context)
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        await query.message.reply_text("Готово ✅ Выбирай дальше в меню.")

