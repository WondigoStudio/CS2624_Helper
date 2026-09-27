"""/start — the welcome message and command list."""

from telegram import Update
from telegram.ext import ContextTypes

from ..config import (
    REMINDER_HOUR,
    REMINDER_MINUTE,
    SCHEDULE_HOUR,
    SCHEDULE_MINUTE,
    TRANSCRIBE_ENABLED,
    TRANSLATE_ENABLED,
)
from ..db import register_chat
from ..states import LESSON_REMINDER_MINUTES
from ..utils import SCHEDULE_OFFSET_MINUTES, TASKS_OFFSET_MINUTES


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    register_chat(update)
    text = (
        "Привет! Я помогу не забывать про домашние задания.\n\n"
        "⚠️ Список домашних заданий — общий для всех, кто пишет этому боту: "
        "если кто-то добавит задание, его увидят все, и наоборот.\n\n"
        "Предметы: ICT, ITP, Psychology, Sociology, Discrete Mathematics, "
        "Foreign Language B2, Китайский язык, Физра.\n\n"
        "Команды:\n"
        "/add — добавить задание\n"
        "/today — что сдавать сегодня\n"
        "/week — что сдавать на этой неделе\n"
        "/all — все предстоящие задания\n"
        "/done — отметить задание выполненным\n"
        "/delete — удалить задание\n"
        "/edittask — изменить задание (предмет/текст/описание/дату/время/вложение)\n"
        "/taskfile — показать вложение (фото/файл) у задания\n"
        "/calendar — календарь месяца кнопками: зелёная — свободный день, "
        "красная — есть задание, синяя — сегодня. Нажми на день — покажу "
        "что на него задано\n\n"
        "Расписание пар и кабинеты:\n"
        "/schedule_add — добавить пару в расписание (день недели → предмет → время → кабинет)\n"
        "/schedule — расписание на сегодня\n"
        "/schedule_week — расписание на всю неделю\n"
        "/schedule_day — расписание на выбранный день недели + фото кабинетов\n"
        "/schedule_delete — удалить пару из расписания\n"
        "/editschedule — изменить пару (день/предмет/время/кабинет)\n"
        "/addroomphoto — прикрепить фото (карту/фото) к кабинету\n"
        "/roomphotos — список кабинетов с сохранённым фото\n"
        "/testphoto — сразу прислать сохранённое фото кабинета (проверить, что оно сохранилось)\n"
        "/testmorning — прогнать утреннюю рассылку расписания+фото прямо сейчас, "
        "не дожидаясь 07:30\n"
        "/users — (только для админа) список пользователей, писавших боту\n"
        "/viewschedule — (только для админа) посмотреть расписание любого "
        "пользователя\n\n"
        f"В группах каждый день в {REMINDER_HOUR:02d}:{REMINDER_MINUTE:02d} по времени "
        f"Казахстана (UTC+5) я присылаю напоминание о заданиях на сегодня и завтра, "
        f"а в {SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d} — расписание на сегодня и фото "
        "кабинетов, если они сохранены.\n"
        f"В личных чатах эти два сообщения подстраиваются под твоё расписание: "
        f"расписание приходит за {SCHEDULE_OFFSET_MINUTES} минут, а задания — за "
        f"{TASKS_OFFSET_MINUTES} минут до твоей первой пары сегодня (если пар на сегодня "
        f"нет — как обычно, в {SCHEDULE_HOUR:02d}:{SCHEDULE_MINUTE:02d}/"
        f"{REMINDER_HOUR:02d}:{REMINDER_MINUTE:02d}).\n"
        f"Плюс за {LESSON_REMINDER_MINUTES} минут до каждой пары пришлю короткое напоминание."
    )
    if TRANSCRIBE_ENABLED:
        text += (
            "\n\n🎙 Ещё умею: пришли голосовое, аудио, кружок или видео — расшифрую "
            "речь в текст автоматически, без команд."
        )
    if TRANSLATE_ENABLED:
        text += (
            f"\n🌐 И перевожу: ответь на любое сообщение (реплаем) и упомяни меня "
            f"через @{context.bot.username} в тексте ответа — переведу его на русский.\n"
            f"А ещё можно вызвать меня где угодно, даже там, где меня нет в чате — "
            f"просто напиши @{context.bot.username} и текст в любом окне ввода Telegram."
        )
    text += (
        "\n\n🎭 Ещё есть весёлые команды: ответь на чьё-нибудь сообщение словом вроде "
        "«обнять», «погладить», «ударить», «поцеловать» и т.п. (всего 30 штук) — "
        "пришлю шуточную сценку с вашими именами. /topactions — топ по чату."
    )
    await update.message.reply_text(text)
