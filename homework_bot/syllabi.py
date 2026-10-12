"""Short, practical facts from each course syllabus (2026-2027) and the grade
structure the Mini App calculator uses.

Scoring model (same in every syllabus except PE):
    course = 0.3 * 1st attestation + 0.3 * 2nd attestation + 0.4 * final exam
Each attestation is worth 100 points and is split into
  * "reg" — the ongoing work before the exam of that attestation (assignments,
    quizzes, practice…), shown as РегМид / РегЭнд,
  * "exam" — the midterm / endterm itself, shown as Мид / Энд.
A component has `max` points inside its attestation; the student enters the
score out of `outof` (equal to `max` unless the syllabus grades it out of 100).
"""

THIRD = 100 / 3


def _c(k, label, mx, outof=None):
    return {"k": k, "l": label, "max": mx, "outof": outof if outof is not None else mx}


_STD_WEIGHTS = [0.3, 0.3, 0.4]

SYLLABI = {
    "ict": {
        "title": "Information Communication Technologies",
        "credits": 5,
        "weights": _STD_WEIGHTS,
        "final": {"l": "Final Project (сдача, презентация, защита)", "outof": 100},
        "min_att": 25,
        "atts": [
            {"name": "1-я аттестация", "reg": [
                _c("a1", "Assignment 1 — ICT, компьютерные системы и архитектура", 25),
                _c("a2", "Assignment 2 — Human-Computer Interaction", 25),
                _c("q1", "Quiz 1 (MCQ)", 10),
                _c("w1", "Еженедельные тесты на learn.astanait", 10),
            ], "exam": [_c("m", "Midterm — проект, этап 1", 30)]},
            {"name": "2-я аттестация", "reg": [
                _c("a3", "Assignment 3 — базы данных и кибербезопасность", 25),
                _c("a4", "Assignment 4 — умные технологии и анализ данных", 25),
                _c("q2", "Quiz 2 (MCQ)", 10),
                _c("w2", "Еженедельные тесты на learn.astanait", 10),
            ], "exam": [_c("e", "Endterm — проект, этап 2", 30)]},
        ],
        "facts": [
            "Midterm — проект, этап 1 (≈5-я неделя); Endterm — этап 2 (≈9-я неделя); финал — защита проекта.",
            "Практические задания сдаются с защитой; сдача после срока не засчитывается.",
            "Минимум 25% в каждой аттестации, иначе нельзя продолжать.",
            "Пропуски: не больше 30% занятий (иначе F или повтор летом). Опоздание больше 5 минут = пропуск.",
            "ИИ-инструменты только с явного разрешения преподавателя; плагиат = 0 за работу.",
        ],
    },
    "itp": {
        "title": "Introduction to Programming (C++)",
        "credits": 5,
        "weights": _STD_WEIGHTS,
        "final": {"l": "Final — квиз в смешанном формате (Moodle)", "outof": 100},
        "min_att": 25,
        "atts": [
            {"name": "1-я аттестация", "reg": [
                _c("p1", "Assignment 1 — синтаксис, if/else", 20),
                _c("p2", "Assignment 2 — циклы", 20),
                _c("p3", "Assignment 3 — массивы", 20),
            ], "exam": [
                _c("mq", "Midterm — квиз", 15),
                _c("mp", "Midterm — практический экзамен (код)", 25),
            ]},
            {"name": "2-я аттестация", "reg": [
                _c("p4", "Assignment 4 — функции", 20),
                _c("p5", "Assignment 5 — указатели и ссылки", 20),
                _c("p6", "Assignment 6 — структуры и строки", 20),
            ], "exam": [
                _c("eq", "Endterm — квиз", 15),
                _c("ep", "Endterm — практический экзамен (код)", 25),
            ]},
        ],
        "facts": [
            "Задания сдаются устной защитой: пишешь/объясняешь код перед преподавателем. Не смог объяснить — работа не принимается.",
            "Практический экзамен: код пишешь сам; учитываются синтаксические и логические ошибки и непокрытые случаи.",
            "Минимум 25% в каждой аттестации, иначе нельзя продолжать.",
            "Пропуски: не больше 30% (даже по справке) — иначе F или повтор летом. Опоздание больше 5 минут = пропуск.",
            "Поздние сдачи не принимаются; ИИ только с разрешения преподавателя.",
            "Учебник: Deitel, C++ How to Program (10th ed.).",
        ],
    },
    "psy": {
        "title": "Psychology (for IT professionals)",
        "credits": None,
        "weights": _STD_WEIGHTS,
        "final": {"l": "Final — квиз (офлайн в кампусе)", "outof": 100},
        "min_att": None,
        "atts": [
            {"name": "Mid Term", "reg": [
                _c("i1", "Independent study (SIS)", 20),
                _c("s1", "Practice session", 40),
            ], "exam": [_c("m", "Mid Term exam (квиз)", 40)]},
            {"name": "End Term", "reg": [
                _c("i2", "Independent study (SIS)", 20),
                _c("s2", "Practice session", 40),
            ], "exam": [_c("e", "End Term exam (квиз)", 40)]},
        ],
        "facts": [
            "Квиз 1 — 4-я неделя; Mid Term exam — 5-я (темы 1–5); case study — 9-я; End Term exam — 10-я (темы 6–10).",
            "SIS-задания открыты с первой недели и не продлеваются.",
            "Опоздание: −10% за день, работы позже 72 часов могут не принять.",
            "Пропуски: до 30% только по уважительной причине. Опоздание больше 10 минут могут засчитать как пропуск.",
            "Пересдавать итоговый экзамен ради повышения оценки нельзя. Почта — только Outlook, ответ в течение 3 рабочих дней.",
        ],
    },
    "soc": {
        "title": "Sociology",
        "credits": 2,
        "weights": _STD_WEIGHTS,
        "final": {"l": "Final — тест (PBT, выбор ответа)", "outof": 100},
        "min_att": None,
        "atts": [
            {"name": "1-я аттестация", "reg": [
                _c("a1", "Assignment 1", 30),
                _c("a2", "Assignment 2", 30),
            ], "exam": [_c("m", "Mid Term quiz (PBT) / устная проверка глоссария", 40)]},
            {"name": "2-я аттестация", "reg": [
                _c("a3", "Assignment 3", 30),
                _c("a4", "Assignment 4", 30),
            ], "exam": [_c("e", "End Term quiz (PBT) / устная проверка глоссария", 40)]},
        ],
        "facts": [
            "Посещаемость минимум 70%, иначе статус «not graded» и недопуск к финалу.",
            "Опоздание 15 минут и больше = пропуск. Пропущенное задание нужно защитить в течение недели (при уважительной причине).",
            "Текст работ проверяют на плагиат (уникальность не ниже 70%).",
        ],
    },
    "dm": {
        "title": "Discrete Mathematics",
        "credits": 5,
        "weights": _STD_WEIGHTS,
        "final": {"l": "Final — тест (выбор ответа)", "outof": 100},
        "min_att": 25,
        "atts": [
            {"name": "1-я аттестация", "reg": [
                _c("h1", "Homework 1", 20),
                _c("q1", "Quiz 1 (бумажный, 30 мин)", 20),
            ], "exam": [_c("m", "Mid Term Exam (открытые вопросы, 50–80 мин)", 60)]},
            {"name": "2-я аттестация", "reg": [
                _c("h2", "Homework 2", 20),
                _c("q2", "Quiz 2 (бумажный, 30 мин)", 20),
            ], "exam": [_c("e", "End Term Exam (открытые вопросы, 50–80 мин)", 60)]},
        ],
        "facts": [
            "Если меньше 25% за аттестацию — курс провален.",
            "Пропустил больше 30% занятий — «not graded» и недопуск к экзамену. Опоздание = пропуск.",
            "Поздние домашки не принимаются (0%). Summative-работы можно писать позже только по договорённости.",
            "Лектор: Sultan Abylkairov (s.abylkairov@astanait.edu.kz).",
        ],
    },
    "flb2": {
        "title": "English for Academic Purposes B2",
        "credits": 3,
        "weights": _STD_WEIGHTS,
        "final": {"l": "Final — устный экзамен (case study cards)", "outof": 100},
        "min_att": None,
        "min_exam": 25,
        "min_final": 50,
        "min_total": 50,
        "atts": [
            {"name": "1-я аттестация", "reg": [
                _c("art", "Research article analysis (до 2-й нед., вс)", THIRD, 100),
                _c("pres", "Case study presentation (до 4-й нед., вс)", THIRD, 100),
            ], "exam": [_c("m", "Midterm Quiz 1 (5-я нед., урок 3)", THIRD, 100)]},
            {"name": "2-я аттестация", "reg": [
                _c("pod", "Podcast (до 7-й нед., вс)", THIRD, 100),
                _c("press", "Press Conference (до 9-й нед., вс)", THIRD, 100),
            ], "exam": [_c("e", "Endterm Quiz 2 (10-я нед., урок 3)", THIRD, 100)]},
        ],
        "facts": [
            "Все 6 работ весят одинаково: каждая даёт 10% итога, оценка за каждую — по 100.",
            "Провал, если Midterm или Endterm меньше 25%, итог меньше 50% или финал меньше 50%.",
            "Финал — устный, 5 минут на студента; прийти надо за 15 минут до своего времени.",
            "Опоздание со сдачей: −5% за день, больше 3 дней — не принимают. Три опоздания до 15 минут = один пропуск.",
            "ИИ-текст — не больше 15% работы и с указанием источника.",
        ],
    },
    "pe": {
        "title": "Физическая культура",
        "credits": None,
        "weights": [0.5, 0.5],
        "final": None,
        "min_att": None,
        "note": "В силлабусе есть только Midterm и Endterm по 100 баллов; итоговая формула не указана — считаю как среднее двух.",
        "scale_fx": 30,
        "atts": [
            {"name": "Midterm (5-я неделя)", "reg": [],
             "exam": [_c("m", "Сдача контрольных нормативов (ОФП + вид спорта)", 100)]},
            {"name": "Endterm (10-я неделя)", "reg": [],
             "exam": [_c("e", "Сдача контрольных нормативов (ОФП + вид спорта)", 100)]},
        ],
        "facts": [
            "Одно занятие в неделю по 100 минут; вид спорта выбираешь сам в начале триместра (места ограничены).",
            "Посещать нужно не меньше 70% занятий. Опоздание больше 10 минут — преподаватель вправе не допустить.",
            "Нормативы на 5-й и 10-й неделе сдают все, кроме освобождённых по здоровью; участие в соревнованиях вуза может дать доп. балл.",
            "Освобождение от занятий (сборные, мастера спорта, секции вне вуза) — со справками, но нормативы всё равно обязательны.",
            "Телефон — только для учебных целей (самоконтроль, видео упражнений).",
        ],
    },
    "chn": {
        "title": "Китайский язык",
        "credits": None,
        "weights": _STD_WEIGHTS,
        "final": {"l": "Final", "outof": 100},
        "min_att": None,
        "generic": True,
        "note": "Силлабуса по этому предмету нет — стандартная схема университета 30% + 30% + 40%. Распределение баллов внутри аттестации условное: поправь под свои критерии.",
        "atts": [
            {"name": "1-я аттестация", "reg": [_c("r1", "Текущие работы", 60)],
             "exam": [_c("m", "Midterm", 40)]},
            {"name": "2-я аттестация", "reg": [_c("r2", "Текущие работы", 60)],
             "exam": [_c("e", "Endterm", 40)]},
        ],
        "facts": [
            "Общие правила университета: пропуски не больше 30% занятий.",
        ],
    },
}

# Letter scale (same in all syllabi; FX/F boundary differs for PE).
SCALE = [(95, "A", 4.0), (90, "A-", 3.67), (85, "B+", 3.33), (80, "B", 3.0), (75, "B-", 2.67),
         (70, "C+", 2.33), (65, "C", 2.0), (60, "C-", 1.67), (55, "D+", 1.33), (50, "D", 1.0)]


def valid_scores(code: str, raw) -> dict:
    """{key: number} — only known components, clamped to 0..outof; anything else dropped."""
    sy = SYLLABI.get(code)
    if sy is None or not isinstance(raw, dict):
        return {}
    limits = {}
    for att in sy["atts"]:
        for c in att["reg"] + att["exam"]:
            limits[c["k"]] = c["outof"]
    if sy.get("final"):
        limits["final"] = sy["final"]["outof"]
    out = {}
    for k, v in raw.items():
        if k not in limits or isinstance(v, bool):
            continue
        try:
            num = float(v)
        except (TypeError, ValueError):
            continue
        if num != num or num in (float("inf"), float("-inf")):
            continue
        out[k] = round(min(max(num, 0.0), float(limits[k])), 2)
    return out


def card_text(code: str) -> str:
    """Compact HTML card for the bot's /syllabus command."""
    import html

    sy = SYLLABI.get(code)
    if sy is None:
        return "Такого предмета нет."
    e = html.escape
    head = f"<b>{e(sy['title'])}</b>"
    if sy.get("credits"):
        head += f" · {sy['credits']} кр."
    lines = [head, ""]
    w = sy["weights"]
    if sy.get("final"):
        lines.append(f"<b>Итог:</b> {int(w[0]*100)}% 1-я аттестация + {int(w[1]*100)}% 2-я + {int(w[2]*100)}% финал")
    else:
        lines.append(f"<b>Итог:</b> {int(w[0]*100)}% Midterm + {int(w[1]*100)}% Endterm")
    for att in sy["atts"]:
        parts = [f"{e(c['l'])} — {c['max']:.0f}" if c["max"] == int(c["max"]) else f"{e(c['l'])} — 1/{len(att['reg'] + att['exam'])}"
                 for c in att["reg"] + att["exam"]]
        lines.append(f"\n<b>{e(att['name'])}</b> (всего 100):")
        lines.extend("• " + p for p in parts)
    if sy.get("final"):
        lines.append(f"\n<b>Финал:</b> {e(sy['final']['l'])}")
    if sy.get("note"):
        lines.append(f"\n<i>{e(sy['note'])}</i>")
    lines.append("\n<b>Важно:</b>")
    lines.extend("• " + e(f) for f in sy["facts"])
    lines.append("\n🎓 Посчитать баллы и прогноз — вкладка «Баллы» в приложении (/app).")
    return "\n".join(lines)
