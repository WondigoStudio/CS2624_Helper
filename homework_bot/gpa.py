"""GPA maths and the grading scale — one source of truth for the bot and the Mini App.

The scale is the standard Kazakhstani point-rating letter system (the same one every
syllabus here uses); the Mini App receives it as JSON (`scale_json`) so both sides agree.

Formulas
    semester GPA   = Σ(gpa_i · credits_i) / Σ credits_i
    cumulative GPA = (prev_gpa · prev_credits + sem_gpa · sem_credits) / (prev_credits + sem_credits)
"""

import math
import re

from .syllabi import SCALE as _SYLLABI_SCALE

# (minimum percent, letter, GPA, traditional grade, short status)
_STATUS = {
    "A": "Максимальная оценка 🏆",
    "A-": "Отличная оценка",
    "B+": "Очень хорошая оценка",
    "B": "Хорошая оценка",
    "B-": "Хорошая оценка, нижняя граница",
    "C+": "Выше среднего",
    "C": "Средняя оценка",
    "C-": "Ниже среднего",
    "D+": "Низкий проходной балл",
    "D": "Минимальный проходной балл",
    "FX": "Не сдано (обычно можно пересдать экзамен)",
    "F": "Не сдано (курс нужно пройти заново)",
}


def _traditional(letter: str) -> str:
    if letter in ("A", "A-"):
        return "Отлично"
    if letter in ("B+", "B", "B-"):
        return "Хорошо"
    if letter in ("FX", "F"):
        return "Неудовлетворительно"
    return "Удовлетворительно"


# built from the syllabus scale so the two can never drift apart; FX/F are appended
SCALE = [(m, name, g, _traditional(name), _STATUS[name]) for m, name, g in _SYLLABI_SCALE] + [
    (25, "FX", 0.5, _traditional("FX"), _STATUS["FX"]),
    (0, "F", 0.0, _traditional("F"), _STATUS["F"]),
]
_BY_LETTER = {row[1]: row for row in SCALE}

MAX_ITEMS = 40
MAX_CREDITS = 60.0
ATTENDANCE_LIMIT_PCT = 30  # more than 30% of lessons missed = the course fails (PE: attendance must be ≥70%)

_LATIN = str.maketrans({"А": "A", "В": "B", "С": "C", "а": "A", "в": "B", "с": "C", "Х": "X", "х": "X",
                        "−": "-", "–": "-", "—": "-", "‐": "-"})


def scale_json() -> list:
    return [{"min": m, "letter": n, "gpa": g, "trad": t, "status": s} for m, n, g, t, s in SCALE]


def grade_for(percent: float) -> dict:
    """Map a 0–100 score to its row of the scale."""
    p = min(max(float(percent), 0.0), 100.0)
    for m, name, g, trad, status in SCALE:
        if p >= m:
            return {"percent": p, "letter": name, "gpa": g, "trad": trad, "status": status, "min": m}
    return {"percent": p, "letter": "F", "gpa": 0.0, "trad": "Неудовлетворительно", "status": _STATUS["F"], "min": 0}


def letter_row(letter: str):
    """Row of the scale for a letter like "B+" (Cyrillic look-alikes and odd dashes tolerated), else None."""
    key = (letter or "").strip().translate(_LATIN).upper()
    row = _BY_LETTER.get(key)
    if row is None:
        return None
    m, name, g, trad, status = row
    return {"letter": name, "gpa": g, "trad": trad, "status": status, "min": m}


def letter_range(letter: str) -> str:
    """"90–94" for A-, "95–100" for A, …"""
    key = letter.upper()
    idx = [r[1] for r in SCALE].index(key)
    lo = SCALE[idx][0]
    hi = 100 if idx == 0 else SCALE[idx - 1][0] - 1
    return f"{lo}–{hi}"


def _number(text: str):
    try:
        v = float(text.replace(",", "."))
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def parse_pairs(text: str):
    """"90 3, 85 4, A- 2" → ([{score|letter, credits, gpa, letter}], [error, …]).

    Pairs are separated by commas, semicolons or new lines; without any separator the
    numbers are paired in order ("90 3 85 4").
    """
    text = (text or "").strip()
    if not text:
        return [], ["Пусто — отправь оценки и кредиты, например: 90 3, 85 4, 95 2"]
    chunks = [c.strip() for c in re.split(r"[;,\n]+", text) if c.strip()]
    if len(chunks) == 1:
        toks = chunks[0].split()
        if len(toks) > 2 and len(toks) % 2 == 0:
            chunks = [f"{toks[i]} {toks[i + 1]}" for i in range(0, len(toks), 2)]
    items, errors = [], []
    for ch in chunks[:MAX_ITEMS]:
        toks = ch.split()
        if len(toks) != 2:
            errors.append(f"«{ch[:30]}» — нужен формат «Оценка Кредиты»")
            continue
        credits = _number(toks[1])
        if credits is None or not 0 < credits <= MAX_CREDITS:
            errors.append(f"«{ch[:30]}» — кредиты должны быть числом больше 0 (до {MAX_CREDITS:g})")
            continue
        score = _number(toks[0])
        if score is not None:
            if not 0 <= score <= 100:
                errors.append(f"«{ch[:30]}» — оценка должна быть от 0 до 100")
                continue
            row = grade_for(score)
            items.append({"score": score, "credits": credits, "gpa": row["gpa"], "letter": row["letter"]})
            continue
        row = letter_row(toks[0])
        if row is None:
            errors.append(f"«{ch[:30]}» — не понял оценку (число 0–100 или буква вроде B+)")
            continue
        items.append({"score": None, "credits": credits, "gpa": row["gpa"], "letter": row["letter"]})
    if len(chunks) > MAX_ITEMS:
        errors.append(f"Беру только первые {MAX_ITEMS} предметов")
    return items, errors


def semester_gpa(items) -> dict:
    """Credit-weighted GPA of parsed items ({gpa, credits}); gpa is None without credits."""
    credits = sum(i["credits"] for i in items)
    if credits <= 0:
        return {"gpa": None, "credits": 0.0, "points": 0.0, "avg_percent": None}
    points = sum(i["gpa"] * i["credits"] for i in items)
    scored = [i for i in items if i.get("score") is not None]
    sc_credits = sum(i["credits"] for i in scored)
    avg = sum(i["score"] * i["credits"] for i in scored) / sc_credits if sc_credits else None
    return {"gpa": points / credits, "credits": credits, "points": points, "avg_percent": avg}


def cumulative_gpa(prev_gpa: float, prev_credits: float, sem_gpa: float, sem_credits: float) -> dict:
    total = prev_credits + sem_credits
    if total <= 0:
        return {"gpa": None, "credits": 0.0}
    return {"gpa": (prev_gpa * prev_credits + sem_gpa * sem_credits) / total, "credits": total}


def parse_prev(text: str):
    """"3.45 60" → (gpa, credits) or an error string."""
    toks = (text or "").replace(";", " ").replace("/", " ").split()
    if len(toks) != 2:
        return "Нужны два числа: текущий GPA и сколько кредитов уже набрано. Например: 3.45 60"
    gpa, credits = _number(toks[0]), _number(toks[1])
    if gpa is None or not 0 <= gpa <= 4:
        return "GPA должен быть числом от 0 до 4 (например 3.45)."
    if credits is None or not 0 <= credits <= 400:
        return "Кредиты — число от 0 до 400 (например 60)."
    return gpa, credits


def attendance(total: int, missed: int, limit_pct: int = ATTENDANCE_LIMIT_PCT) -> dict:
    """How many lessons may still be skipped. `status`: ok / edge (≤1 left) / bad (limit exceeded)."""
    if total <= 0:
        return {"error": "Всего занятий должно быть больше 0."}
    if missed < 0 or missed > total:
        return {"error": "Пропусков не может быть меньше 0 или больше, чем всего занятий."}
    allowed = int(total * limit_pct / 100 + 1e-9)
    left = allowed - missed
    pct = missed / total * 100
    status = "bad" if left < 0 else "edge" if left <= 1 else "ok"
    return {"total": total, "missed": missed, "allowed": allowed, "left": left,
            "pct": pct, "present_pct": 100 - pct, "status": status, "limit_pct": limit_pct}


def parse_attendance(text: str):
    """"30 5" (всего, пропущено) → (total, missed) or an error string."""
    toks = re.split(r"[\s,;/]+", (text or "").strip())
    toks = [t for t in toks if t]
    if len(toks) != 2:
        return "Нужны два числа: сколько всего занятий по предмету и сколько пропущено. Например: 30 5"
    try:
        total, missed = int(toks[0]), int(toks[1])
    except ValueError:
        return "Нужны целые числа. Например: 30 5"
    if total <= 0 or total > 500:
        return "Всего занятий — целое число от 1 до 500."
    if missed < 0 or missed > total:
        return "Пропусков не может быть меньше 0 или больше, чем всего занятий."
    return total, missed

