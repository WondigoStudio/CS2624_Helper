"""Static data: subjects, fun-action phrases, weekday/month names.

Nothing here reads the environment or imports anything else in the package.
"""

SUBJECTS = [
    ("ict", "ICT"),
    ("itp", "ITP"),
    ("psy", "Psychology"),
    ("soc", "Sociology"),
    ("dm", "Discrete Mathematics"),
    ("flb2", "Foreign Language B2"),
    ("chn", "Китайский язык"),
    ("pe", "Физра"),
]
SUBJECT_NAME = dict(SUBJECTS)
SUBJECT_EMOJI = {
    "ict": "💻", "itp": "🖥", "psy": "🧠", "soc": "🧑‍🤝‍🧑",
    "dm": "🔢", "flb2": "🌍", "chn": "🇨🇳", "pe": "🏃",
}
# ---------------------------------------------------------------------------
# Fun reply actions ("обнять", "ударить", etc.) — Iris-bot style. Reply to
# someone's message with one of these words (no slash, just the plain word)
# and the bot posts a little scene with both names. {a} = the person who
# sent the action, {t} = the person being replied to.
# ---------------------------------------------------------------------------
ACTIONS = {
    "обнять":        ("🤗", ["{a} крепко обнял(а) {t}"]),
    "погладить":     ("🥰", ["{a} нежно погладил(а) {t} по голове"]),
    "поцеловать":    ("😘", ["{a} поцеловал(а) {t}"]),
    "ударить":       ("👊", ["{a} со всей силы ударил(а) {t}"]),
    "пнуть":         ("🦵", ["{a} от души пнул(а) {t}"]),
    "укусить":       ("😈", ["{a} укусил(а) {t}"]),
    "ущипнуть":      ("🤏", ["{a} ущипнул(а) {t}"]),
    "толкнуть":      ("🫸", ["{a} толкнул(а) {t}"]),
    "шлёпнуть":      ("👋", ["{a} шлёпнул(а) {t}"]),
    "дать пять":     ("✋", ["{a} дал(а) пять {t}"]),
    "потрепать":     ("🖐", ["{a} потрепал(а) {t} по щеке"]),
    "взъерошить":    ("💇", ["{a} взъерошил(а) волосы {t}"]),
    "подмигнуть":    ("😉", ["{a} подмигнул(а) {t}"]),
    "помахать":      ("👋", ["{a} помахал(а) {t}"]),
    "потискать":     ("🫂", ["{a} затискал(а) {t}"]),
    "защекотать":    ("🤣", ["{a} защекотал(а) {t} до слёз"]),
    "взять за руку": ("🤝", ["{a} взял(а) {t} за руку"]),
    "станцевать":    ("💃", ["{a} закружил(а) {t} в танце"]),
    "подзатыльник":  ("🖐", ["{a} дал(а) подзатыльник {t}"]),
    "оплеуха":       ("✋", ["{a} отвесил(а) оплеуху {t}"]),
    "погрозить":     ("✊", ["{a} погрозил(а) кулаком {t}"]),
    "показать язык": ("😛", ["{a} показал(а) язык {t}"]),
    "комплимент":    ("💬", ["{a} сделал(а) комплимент {t}"]),
    "признаться":    ("❤️", ["{a} признался(ась) в любви {t}"]),
    "торт в лицо":   ("🎂", ["{a} кинул(а) тортом в лицо {t}"]),
    "облить водой":  ("💦", ["{a} облил(а) водой {t}"]),
    "напугать":      ("👻", ["{a} напугал(а) {t}"]),
    "засмеять":      ("🙂", ["{a} поднял(а) на смех {t}"]),
    "извиниться":    ("🙏", ["{a} извинился(лась) перед {t}"]),
    "поблагодарить": ("🙌", ["{a} поблагодарил(а) {t}"]),
}
# Optional: map an action key -> a Telegram custom (premium) emoji ID, to
# use INSTEAD of the plain unicode emoji when sending the standalone
# animated-emoji message. Requires the account that created this bot (via
# @BotFather) to have Telegram Premium — otherwise sending silently falls
# back to plain emoji. Get IDs with /emojiid (admin-only, see below), then
# fill them in here, e.g.:
#   CUSTOM_EMOJI_IDS = {"обнять": "5368324170671202286", ...}
CUSTOM_EMOJI_IDS: dict = {
    "засмеять": "5461117441612462242",
}
WEEKDAY_NAMES_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
WEEKDAY_NAMES_FULL_RU = [
    "Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье",
]
WEEKDAY_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣"]
ALL_USERS_SENTINEL = "ALL"
MONTH_NAMES_RU = [
    "", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]
