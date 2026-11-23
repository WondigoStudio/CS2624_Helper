"""Early warning: will this course end with FX, a retake or a lost scholarship?

Rules (from the student guide the user shared):
    Attendance > 70%, RegMid > 25%, RegEnd > 25%, RegTerm = (RegMid + RegEnd) / 2 > 50%, Final > 50%.
    Missing the admission rules (attendance / RegMid / RegEnd / RegTerm) means the course must be
    retaken ("ретейк", paid per credit); passing them but failing the final means FX (a paid exam resit).
The FX/Retake table of the guide did not come through, so this split is inferred from those rules and
the grade scale — keep it labelled as an estimate. The same logic lives in webapp/index.html (gRisk).
"""

from .syllabi import SYLLABI

MIN_REG = 25           # RegMid and RegEnd must be MORE than this
MIN_TERM = 50          # (RegMid + RegEnd) / 2 must be MORE than this
MIN_FINAL = 50         # Final must be MORE than this
MAX_MISSED_PCT = 30    # attendance above 70% = at most 30% of lessons missed
CREDIT_PRICE = 20000   # tenge per credit of a retake
FX_PRICE = 20000       # tenge for one FX exam
WARN_REG = 35          # an attestation this low is "on the edge"
WARN_TERM = 55

ICON = {"none": "⚪", "ok": "🟢", "warn": "🟡", "fx": "🟠", "retake": "🔴"}
LABEL = {"none": "нет данных", "ok": "всё в порядке", "warn": "есть риск", "fx": "FX", "retake": "ретейк"}

EXTRA_KEYS = {"_cr": 30, "_at": 500, "_am": 500}  # credits, lessons in total, lessons missed (saved with the scores)


def is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")


def _comps(sy):
    out = []
    for i, a in enumerate(sy["atts"]):
        for part in ("reg", "exam"):
            for c in a[part]:
                out.append({**c, "att": i, "part": part})
    if sy.get("final"):
        out.append({"k": "final", "max": 100, "outof": sy["final"]["outof"], "att": -1, "part": "final"})
    return out


def lms_syllabus(sy):
    """The "Register Midterm / Endterm from the LMS" view: each attestation is one 0–100 entry."""
    return {**sy, "atts": [{"name": a["name"], "reg": [], "exam": [{"k": f"att{i + 1}", "l": a["name"], "max": 100, "outof": 100}]}
                           for i, a in enumerate(sy["atts"])]}


def default_mode(sc) -> str:
    return "lms" if sc.get("att1") is not None or sc.get("att2") is not None else "detail"


def attestations(sy, sc):
    """[{total, best, complete, started}] — points of each attestation out of 100."""
    comps = _comps(sy)
    out = []
    for i in range(len(sy["atts"])):
        earned = mx = gmax = lost = 0.0
        for c in comps:
            if c["att"] != i:
                continue
            mx += c["max"]
            if is_num(sc.get(c["k"])):
                e = sc[c["k"]] / c["outof"] * c["max"]
                earned += e
                gmax += c["max"]
                lost += c["max"] - e
        out.append({"total": earned, "best": 100 - lost, "complete": gmax == mx and mx > 0, "started": gmax > 0})
    return out


def final_percent(sy, sc):
    if not sy.get("final") or not is_num(sc.get("final")):
        return None
    return sc["final"] / sy["final"]["outof"] * 100


def assess(code: str, sc: dict, mode: str = None) -> dict:
    """{status, reasons, tips, retake_cost, fx_cost, grant_risk}. `sc` is the saved scores dict
    (components + optional _cr credits, _at/_am lessons total/missed)."""
    base = SYLLABI[code]
    sy = lms_syllabus(base) if (mode or default_mode(sc)) == "lms" else base
    atts = attestations(sy, sc)
    fin = final_percent(sy, sc)
    credits = sc.get("_cr") if is_num(sc.get("_cr")) and sc.get("_cr") > 0 else base.get("credits")
    at, am = sc.get("_at"), sc.get("_am")
    has_att = is_num(at) and is_num(am) and at > 0 and 0 <= am <= at
    has_scores = any(a["started"] for a in atts) or fin is not None
    retake, fx, warn, tips = [], [], [], []

    if has_att:
        missed = am / at * 100
        if missed > MAX_MISSED_PCT:
            retake.append(f"посещаемость {100 - missed:.0f}% — нужно больше 70%")
        elif missed == MAX_MISSED_PCT:
            warn.append("посещаемость ровно 70% — по правилам нужно больше 70%, следующий пропуск уже критичен")
        elif at * MAX_MISSED_PCT / 100 - am < 1:
            warn.append("почти исчерпан лимит пропусков")
    for i, a in enumerate(atts):
        name = ("RegMid" if i == 0 else "RegEnd")
        if a["best"] <= MIN_REG:
            retake.append(f"{name}: даже с максимумом за остальное выйдет {a['best']:.0f}% — нужно больше {MIN_REG}%")
        elif a["complete"] and a["total"] <= WARN_REG:
            warn.append(f"{name} {a['total']:.0f}% — на грани (нужно больше {MIN_REG}%)")
    if len(atts) == 2:
        best_term = (atts[0]["best"] + atts[1]["best"]) / 2
        if best_term <= MIN_TERM:
            retake.append(f"RegTerm: максимум, который ещё можно получить, — {best_term:.0f}% (нужно больше {MIN_TERM}%)")
        elif atts[0]["complete"] and atts[1]["complete"]:
            term = (atts[0]["total"] + atts[1]["total"]) / 2
            if term <= WARN_TERM:
                warn.append(f"RegTerm {term:.0f}% — на грани (нужно больше {MIN_TERM}%)")
            if fin is None:
                tips.append(f"на финале нужно набрать больше {MIN_FINAL}%")
        elif atts[0]["complete"] != atts[1]["complete"]:
            done = atts[0] if atts[0]["complete"] else atts[1]
            if done["total"] <= WARN_TERM:
                warn.append(f"одна аттестация закрыта на {done['total']:.0f}% — средний RegTerm должен быть больше {MIN_TERM}%")

    if sy.get("final"):
        if fin is not None and fin <= MIN_FINAL and not retake:
            fx.append(f"финал {fin:.0f}% — нужно больше {MIN_FINAL}%")
    else:  # no final exam (PE): the total itself must reach 50
        total = sum(a["total"] * w for a, w in zip(atts, sy["weights"]))
        if all(a["complete"] for a in atts) and total < 50 and not retake:
            fx.append(f"итог {total:.0f}% — меньше 50%")

    if not has_scores and not has_att:
        status = "none"
    elif retake:
        status = "retake"
    elif fx:
        status = "fx"
    elif warn:
        status = "warn"
    else:
        status = "ok"
    return {
        "status": status,
        "reasons": retake or fx or warn,
        "tips": tips,
        "credits": credits,
        "retake_cost": credits * CREDIT_PRICE if status == "retake" and credits else None,
        "fx_cost": FX_PRICE if status == "fx" else None,
        "grant_risk": status in ("fx", "retake"),
    }


# ---------------------------------------------------------------- scholarship tiers
# Base: no grade below B- (75+) in the session; increased (+15%): only A / A- (90+);
# presidential: only A / A- for the whole study period plus achievements (olympiads, conferences…).
TIERS = [("base", "Базовая", 75, "ни одной оценки ниже B-"),
         ("high", "Повышенная (+15%)", 90, "только A и A-")]
TIER_ICON = {"lost": "🔴", "risk": "🟡", "ontrack": "🟢", "secure": "✅", "unknown": "⚪"}
TIER_LABEL = {"lost": "потеряна", "risk": "под угрозой", "ontrack": "пока на уровне", "secure": "выполнено", "unknown": "нет данных"}


def totals(sy, sc):
    """{cur, best, pace (0..100 or None), all, graded} — points of the whole course out of 100."""
    cur = lost = graded = rem = 0.0
    for c in _comps(sy):
        cp = sy["weights"][c["att"]] * c["max"] if c["att"] >= 0 else sy["weights"][2] * 100
        if is_num(sc.get(c["k"])):
            e = sc[c["k"]] / c["outof"] * cp
            cur += e
            lost += cp - e
            graded += cp
        else:
            rem += cp
    return {"cur": cur, "best": 100 - lost, "pace": cur / graded * 100 if graded > 0 else None,
            "all": rem == 0, "graded": graded}


def subject_tier(t: dict, need: float) -> str:
    if t["graded"] <= 0:
        return "unknown"
    if t["best"] < need:
        return "lost"
    if t["all"]:
        return "secure"
    return "ontrack" if t["pace"] >= need else "risk"


def scholarship(grades: dict, codes) -> list:
    """For each tier: {key, label, need, rule, status, subjects: [{code, status, cur, best, pace}]}.
    The overall status is the worst of the subjects (lost > unknown > risk > ontrack > secure)."""
    out = []
    for key, label, need, rule in TIERS:
        rows = []
        for code in codes:
            if code not in SYLLABI:
                continue
            sc = grades.get(code) or {}
            base = SYLLABI[code]
            sy = lms_syllabus(base) if default_mode(sc) == "lms" else base
            t = totals(sy, sc)
            rows.append({"code": code, "status": subject_tier(t, need), "cur": t["cur"], "best": t["best"], "pace": t["pace"]})
        sts = [r["status"] for r in rows]
        if not rows:
            status = "unknown"
        elif "lost" in sts:
            status = "lost"
        elif "unknown" in sts:
            status = "unknown"
        elif "risk" in sts:
            status = "risk"
        elif all(x == "secure" for x in sts):
            status = "secure"
        else:
            status = "ontrack"
        out.append({"key": key, "label": label, "need": need, "rule": rule, "status": status, "subjects": rows})
    return out


def scholarship_text(grades: dict, names: dict) -> str:
    import html

    tiers = scholarship(grades, list(names))
    lines = ["🎓 <b>Стипендия</b> (по итогам сессии)"]
    for t in tiers:
        lines.append(f"{TIER_ICON[t['status']]} <b>{t['label']}</b>: {TIER_LABEL[t['status']]} — {t['rule']} (от {t['need']} баллов в каждом предмете)")
        for r in t["subjects"]:
            nm = html.escape(names[r["code"]])
            if r["status"] == "lost":
                lines.append(f"   • {nm}: максимум {r['best']:.0f} — меньше {t['need']}")
            elif r["status"] == "risk":
                lines.append(f"   • {nm}: сейчас {r['cur']:.0f}, в текущем темпе выйдет {r['pace']:.0f} — нужно {t['need']}")
            elif r["status"] == "unknown":
                lines.append(f"   • {nm}: нет баллов")
    high = next(t for t in tiers if t["key"] == "high")
    pres = {"lost": "по оценкам этой сессии условие «только A и A-» уже не выполнить", "secure": "оценки сессии подходят"}.get(
        high["status"], "пока по оценкам сессии есть шанс")
    lines.append(f"🏅 <b>Президентская</b>: нужны только A и A- за весь период обучения и достижения (олимпиады, конференции, "
                 f"общественная работа) — {pres}; остальное бот проверить не может.")
    return "\n".join(lines)


# ---------------------------------------------------------------- text for the bot
SEVERITY = {"none": 0, "ok": 0, "warn": 1, "fx": 2, "retake": 3}


def _money(n: int) -> str:
    return f"{n:,}".replace(",", " ") + " ₸"


def subject_text(code: str, a: dict, name: str) -> str:
    """One subject as HTML lines (the caller passes the display name)."""
    import html

    head = f"{ICON[a['status']]} <b>{html.escape(name)}</b> — {LABEL[a['status']]}"
    lines = [head]
    for r in a["reasons"]:
        lines.append("   • " + html.escape(r))
    for t in a["tips"]:
        lines.append("   ℹ️ " + html.escape(t))
    if a["status"] == "retake":
        if a["retake_cost"]:
            lines.append(f"   💸 Ретейк: {a['credits']:g} кр. × {CREDIT_PRICE:,}".replace(",", " ") + f" = {_money(int(a['retake_cost']))}")
        else:
            lines.append("   💸 Ретейк: укажи кредиты предмета в приложении — посчитаю цену (1 кр. = 20 000 ₸)")
    if a["status"] == "fx":
        lines.append(f"   💸 FX-пересдача: {_money(FX_PRICE)} за дисциплину (оплата за 2 рабочих дня до экзамена)")
    return "\n".join(lines)


def report(grades: dict, names: dict) -> str:
    """HTML report over the saved scores of one person: {code: scores} and {code: display name}."""
    blocks, worst, retake_sum, fx_n, unknown_cost = [], 0, 0, 0, False
    for code, name in names.items():
        if code not in SYLLABI:
            continue
        a = assess(code, grades.get(code) or {})
        blocks.append(subject_text(code, a, name))
        worst = max(worst, SEVERITY[a["status"]])
        if a["status"] == "retake":
            if a["retake_cost"]:
                retake_sum += a["retake_cost"]
            else:
                unknown_cost = True
        if a["status"] == "fx":
            fx_n += 1
    out = "⚠️ <b>Риски по предметам</b>\n\n" + "\n\n".join(blocks)
    costs = []
    if retake_sum:
        costs.append(f"ретейк {_money(int(retake_sum))}")
    if fx_n:
        costs.append(f"FX {_money(fx_n * FX_PRICE)}")
    if costs:
        out += "\n\n💸 Если ничего не изменится: " + " + ".join(costs) + (" (цена ретейка части предметов неизвестна)" if unknown_cost else "")
    out += "\n\n" + scholarship_text(grades, names)
    out += ("\n\n<i>Оценка по правилам гайда: посещаемость > 70%, RegMid и RegEnd > 25%, RegTerm > 50%, финал > 50%. "
            "Это расчёт по введённым баллам, не официальный статус — смотри LMS и силлабус.</i>")
    return out

