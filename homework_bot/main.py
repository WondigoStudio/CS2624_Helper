"""Application entry point: builds the Telegram Application, registers every
command/conversation/callback handler, schedules the background jobs, and
starts polling.

Run with:  python -m homework_bot.main
"""

from datetime import time as dtime

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    InlineQueryHandler,
    MessageHandler,
    filters,
)

from .config import (
    BOT_TOKEN,
    GROQ_API_KEY,
    MEDIA_DOWNLOAD_ENABLED,
    POLL_HOUR,
    POLL_MINUTE,
    TIMEZONE,
    TRANSCRIBE_ENABLED,
    TRANSLATE_ENABLED,
    logger,
    requests,
    start_health_check_server,
)
from .constants import ACTIONS
from .db import init_db
from .states import (
    BDAY_DATE,
    BDAY_TARGET,
    CHOOSING_SUBJECT,
    CHOOSING_TARGET_USER,
    EDIT_LESSON_FIELD,
    EDIT_LESSON_PICK,
    EDIT_LESSON_ROOM,
    EDIT_LESSON_SUBJECT,
    EDIT_LESSON_TIME,
    EDIT_LESSON_WEEKDAY,
    EDIT_TASK_ATTACHMENT,
    EDIT_TASK_DATE,
    EDIT_TASK_DESCRIPTION,
    EDIT_TASK_FIELD,
    EDIT_TASK_PICK,
    EDIT_TASK_SUBJECT,
    EDIT_TASK_TIME,
    EDIT_TASK_TITLE,
    PHOTO_ROOM_NAME,
    PHOTO_TARGET_USER,
    PHOTO_WAITING,
    REMIND_CUSTOM_DATE,
    REMIND_CUSTOM_TIME,
    REMIND_DAILY_TIME,
    REMIND_TEXT,
    REMIND_WEEKLY_DAY,
    REMIND_WEEKLY_TIME,
    REMIND_WHEN,
    SCH_ROOM,
    SCH_SUBJECT,
    SCH_TIME,
    SCH_WEEKDAY,
    TYPING_ATTACHMENT,
    TYPING_DATE,
    TYPING_DESCRIPTION,
    TYPING_TIME,
    TYPING_TITLE,
)

from .handlers.start import start
from .handlers.tasks import (
    add_attachment_document,
    add_attachment_invalid,
    add_attachment_photo,
    add_attachment_skip,
    add_cancel,
    add_date_typed,
    add_description_skip,
    add_description_typed,
    add_start,
    add_subject_chosen,
    add_time_skip,
    add_time_typed,
    add_title_typed,
    all_cmd,
    delete_chosen,
    delete_cmd,
    done_chosen,
    done_cmd,
    edittask_attachment_clear,
    edittask_attachment_document,
    edittask_attachment_invalid,
    edittask_attachment_photo,
    edittask_date_typed,
    edittask_description_clear,
    edittask_description_typed,
    edittask_field_chosen,
    edittask_picked,
    edittask_start,
    edittask_subject_chosen,
    edittask_time_skip,
    edittask_time_typed,
    edittask_title_typed,
    taskdesc_chosen,
    taskfile_chosen,
    taskfile_cmd,
    today_cmd,
    week_cmd,
)
from .handlers.schedule import (
    allow_schedule_cmd,
    editschedule_field_chosen,
    editschedule_picked,
    editschedule_room_typed,
    editschedule_start,
    editschedule_subject_chosen,
    editschedule_time_typed,
    editschedule_weekday_chosen,
    schedule_add_start,
    schedule_cancel,
    schedule_day_chosen,
    schedule_day_start,
    schedule_delete_chosen,
    schedule_delete_cmd,
    schedule_room_typed,
    schedule_subject_chosen,
    schedule_target_chosen,
    schedule_time_typed,
    schedule_today_cmd,
    schedule_week_cmd,
    schedule_weekday_chosen,
    toggle_schedule_permission_chosen,
)
from .handlers.roomphotos import (
    addroomphoto_document_received,
    addroomphoto_not_a_photo,
    addroomphoto_photo_received,
    addroomphoto_room_typed,
    addroomphoto_start,
    addroomphoto_target_chosen,
    roomphotos_cmd,
    testphoto_chosen,
    testphoto_cmd,
)
from .handlers.calendar import calendar_cmd, calendar_day_tap, calendar_nav, calendar_noop
from .handlers.admin import testmorning_cmd, users_cmd, viewschedule_chosen, viewschedule_cmd
from .handlers.social import call_cmd, set_report_cmd, topactions_cmd, track_group_members
from .handlers.transcribe import handle_transcribe
from .handlers.translate import handle_translate_reply, inline_translate
from .handlers.media import handle_media_link, youtube_download_chosen
from .handlers.reminders import (
    remind_cancel,
    remind_custom_date_typed,
    remind_custom_time_typed,
    remind_daily_time_typed,
    remind_start,
    remind_text_typed,
    remind_weekly_day_chosen,
    remind_weekly_time_typed,
    remind_when_chosen,
    reminder_confirm_chosen,
    reminder_delete_chosen,
    reminder_snooze_chosen,
    reminders_cmd,
)
from .handlers.birthdays import (
    addbirthday_cancel,
    addbirthday_date_typed,
    addbirthday_start,
    addbirthday_target_chosen,
    birthday_delete_chosen,
    birthday_filter_chosen,
    birthdays_cmd,
    nextbirthday_cmd,
)

from .jobs import (
    check_adaptive_schedule,
    check_adaptive_tasks,
    check_lesson_reminders,
    check_reminders,
    send_morning_poll_job,
)


def main():
    if not BOT_TOKEN:
        raise SystemExit(
            "BOT_TOKEN is not set. Set it as an environment variable "
            "(locally: export BOT_TOKEN=...; on Render: Environment tab)."
        )
    init_db()
    start_health_check_server()
    app = Application.builder().token(BOT_TOKEN).build()

    add_conv = ConversationHandler(
        entry_points=[CommandHandler("add", add_start)],
        states={
            CHOOSING_SUBJECT: [CallbackQueryHandler(add_subject_chosen, pattern="^addsub:")],
            TYPING_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_title_typed)],
            TYPING_DESCRIPTION: [
                CallbackQueryHandler(add_description_skip, pattern="^nodesc$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_description_typed),
            ],
            TYPING_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_date_typed)],
            TYPING_TIME: [
                CallbackQueryHandler(add_time_skip, pattern="^notime$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_time_typed),
            ],
            TYPING_ATTACHMENT: [
                CallbackQueryHandler(add_attachment_skip, pattern="^noattach$"),
                MessageHandler(filters.PHOTO, add_attachment_photo),
                MessageHandler(filters.Document.IMAGE | filters.Document.PDF, add_attachment_document),
                MessageHandler(~filters.COMMAND, add_attachment_invalid),
            ],
        },
        fallbacks=[CommandHandler("cancel", add_cancel)],
    )

    schedule_add_conv = ConversationHandler(
        entry_points=[CommandHandler("schedule_add", schedule_add_start)],
        states={
            CHOOSING_TARGET_USER: [CallbackQueryHandler(schedule_target_chosen, pattern="^schtgt:")],
            SCH_WEEKDAY: [CallbackQueryHandler(schedule_weekday_chosen, pattern="^schwd:")],
            SCH_SUBJECT: [CallbackQueryHandler(schedule_subject_chosen, pattern="^schsub:")],
            SCH_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule_time_typed)],
            SCH_ROOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, schedule_room_typed)],
        },
        fallbacks=[CommandHandler("cancel", schedule_cancel)],
    )

    addroomphoto_conv = ConversationHandler(
        entry_points=[CommandHandler("addroomphoto", addroomphoto_start)],
        states={
            PHOTO_TARGET_USER: [CallbackQueryHandler(addroomphoto_target_chosen, pattern="^phtgt:")],
            PHOTO_ROOM_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, addroomphoto_room_typed)],
            PHOTO_WAITING: [
                MessageHandler(filters.PHOTO, addroomphoto_photo_received),
                MessageHandler(filters.Document.IMAGE, addroomphoto_document_received),
                MessageHandler(~filters.COMMAND, addroomphoto_not_a_photo),
            ],
        },
        fallbacks=[CommandHandler("cancel", schedule_cancel)],
    )

    edittask_conv = ConversationHandler(
        entry_points=[CommandHandler("edittask", edittask_start)],
        states={
            EDIT_TASK_PICK: [CallbackQueryHandler(edittask_picked, pattern="^edittask:")],
            EDIT_TASK_FIELD: [CallbackQueryHandler(edittask_field_chosen, pattern="^editfield:")],
            EDIT_TASK_SUBJECT: [CallbackQueryHandler(edittask_subject_chosen, pattern="^edittasksub:")],
            EDIT_TASK_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edittask_title_typed)],
            EDIT_TASK_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edittask_date_typed)],
            EDIT_TASK_TIME: [
                CallbackQueryHandler(edittask_time_skip, pattern="^edittasktime:none$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, edittask_time_typed),
            ],
            EDIT_TASK_DESCRIPTION: [
                CallbackQueryHandler(edittask_description_clear, pattern="^edittaskdesc:none$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, edittask_description_typed),
            ],
            EDIT_TASK_ATTACHMENT: [
                CallbackQueryHandler(edittask_attachment_clear, pattern="^edittaskattach:none$"),
                MessageHandler(filters.PHOTO, edittask_attachment_photo),
                MessageHandler(filters.Document.IMAGE | filters.Document.PDF, edittask_attachment_document),
                MessageHandler(~filters.COMMAND, edittask_attachment_invalid),
            ],
        },
        fallbacks=[CommandHandler("cancel", add_cancel)],
    )

    editschedule_conv = ConversationHandler(
        entry_points=[CommandHandler("editschedule", editschedule_start)],
        states={
            EDIT_LESSON_PICK: [CallbackQueryHandler(editschedule_picked, pattern="^editlesson:")],
            EDIT_LESSON_FIELD: [CallbackQueryHandler(editschedule_field_chosen, pattern="^editlfield:")],
            EDIT_LESSON_WEEKDAY: [CallbackQueryHandler(editschedule_weekday_chosen, pattern="^editlwd:")],
            EDIT_LESSON_SUBJECT: [CallbackQueryHandler(editschedule_subject_chosen, pattern="^editlsub:")],
            EDIT_LESSON_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, editschedule_time_typed)],
            EDIT_LESSON_ROOM: [MessageHandler(filters.TEXT & ~filters.COMMAND, editschedule_room_typed)],
        },
        fallbacks=[CommandHandler("cancel", schedule_cancel)],
    )

    remind_conv = ConversationHandler(
        entry_points=[CommandHandler("remind", remind_start)],
        states={
            REMIND_TEXT: [MessageHandler(filters.TEXT & ~filters.COMMAND, remind_text_typed)],
            REMIND_WHEN: [CallbackQueryHandler(remind_when_chosen, pattern="^remwhen:")],
            REMIND_DAILY_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, remind_daily_time_typed)],
            REMIND_WEEKLY_DAY: [CallbackQueryHandler(remind_weekly_day_chosen, pattern="^remwd:")],
            REMIND_WEEKLY_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, remind_weekly_time_typed)],
            REMIND_CUSTOM_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, remind_custom_date_typed)],
            REMIND_CUSTOM_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, remind_custom_time_typed)],
        },
        fallbacks=[CommandHandler("cancel", remind_cancel)],
    )

    addbirthday_conv = ConversationHandler(
        entry_points=[CommandHandler("addbirthday", addbirthday_start)],
        states={
            BDAY_TARGET: [CallbackQueryHandler(addbirthday_target_chosen, pattern="^bdaytarget:")],
            BDAY_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, addbirthday_date_typed)],
        },
        fallbacks=[CommandHandler("cancel", addbirthday_cancel)],
    )

    # ----------------------------------------------------
    # Основные хэндлеры бота
    # ----------------------------------------------------
    app.add_handler(CommandHandler("start", start))
    app.add_handler(add_conv)
    app.add_handler(schedule_add_conv)
    app.add_handler(addroomphoto_conv)
    app.add_handler(edittask_conv)
    app.add_handler(editschedule_conv)
    app.add_handler(remind_conv)
    app.add_handler(CommandHandler("reminders", reminders_cmd))
    app.add_handler(CallbackQueryHandler(reminder_delete_chosen, pattern="^remdel:"))
    app.add_handler(CallbackQueryHandler(reminder_snooze_chosen, pattern="^remsnooze:"))
    app.add_handler(CallbackQueryHandler(reminder_confirm_chosen, pattern="^remconfirm:"))
    app.add_handler(addbirthday_conv)
    app.add_handler(CommandHandler("birthdays", birthdays_cmd))
    app.add_handler(CommandHandler("nextbirthday", nextbirthday_cmd))
    app.add_handler(CallbackQueryHandler(birthday_delete_chosen, pattern="^bdaydel:"))
    app.add_handler(CallbackQueryHandler(birthday_filter_chosen, pattern="^bdayfilter:"))
    app.add_handler(CommandHandler("today", today_cmd))
    app.add_handler(CommandHandler("week", week_cmd))
    app.add_handler(CommandHandler("all", all_cmd))
    app.add_handler(CommandHandler("done", done_cmd))
    app.add_handler(CallbackQueryHandler(done_chosen, pattern="^done:"))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(CallbackQueryHandler(delete_chosen, pattern="^del:"))
    app.add_handler(CommandHandler("taskfile", taskfile_cmd))
    app.add_handler(CallbackQueryHandler(taskfile_chosen, pattern="^taskfile:"))
    app.add_handler(CallbackQueryHandler(taskdesc_chosen, pattern="^taskdesc:"))
    app.add_handler(CommandHandler("calendar", calendar_cmd))
    app.add_handler(CallbackQueryHandler(calendar_nav, pattern="^cal:"))
    app.add_handler(CallbackQueryHandler(calendar_day_tap, pattern="^day:"))
    app.add_handler(CallbackQueryHandler(calendar_noop, pattern="^noop$"))
    app.add_handler(CommandHandler("schedule", schedule_today_cmd))
    app.add_handler(CommandHandler("schedule_week", schedule_week_cmd))
    app.add_handler(CommandHandler("schedule_day", schedule_day_start))
    app.add_handler(CallbackQueryHandler(schedule_day_chosen, pattern="^schday:"))
    app.add_handler(CommandHandler("schedule_delete", schedule_delete_cmd))
    app.add_handler(CallbackQueryHandler(schedule_delete_chosen, pattern="^schdel:"))
    app.add_handler(CommandHandler("roomphotos", roomphotos_cmd))
    app.add_handler(CommandHandler("users", users_cmd))
    app.add_handler(CommandHandler("viewschedule", viewschedule_cmd))
    app.add_handler(CallbackQueryHandler(viewschedule_chosen, pattern="^viewsch:"))
    app.add_handler(CommandHandler("testphoto", testphoto_cmd))
    app.add_handler(CallbackQueryHandler(testphoto_chosen, pattern="^testph:"))
    app.add_handler(CommandHandler("testmorning", testmorning_cmd))

    # --- НОВЫЕ ХЭНДЛЕРЫ ---
    app.add_handler(CommandHandler("call", call_cmd))
    app.add_handler(CommandHandler("set_report", set_report_cmd))
    app.add_handler(CommandHandler("allow_schedule", allow_schedule_cmd))
    app.add_handler(CallbackQueryHandler(toggle_schedule_permission_chosen, pattern="^toggle_sch_perm:"))

    # ----------------------------------------------------
    # Модули расширений (Голос / Перевод)
    # ----------------------------------------------------
    # Автоматическое отслеживание ВСЕХ участников группы (для созыва)
    app.add_handler(
        MessageHandler(filters.ChatType.GROUPS & ~filters.COMMAND, track_group_members),
        group=-1
    )
    if TRANSCRIBE_ENABLED and requests is not None:
        app.add_handler(
            MessageHandler(
                filters.VOICE | filters.AUDIO | filters.VIDEO_NOTE | filters.VIDEO,
                handle_transcribe,
            )
        )
        logger.info("Voice/audio/video transcription enabled (Groq).")
    elif GROQ_API_KEY and requests is None:
        logger.warning(
            "GROQ_API_KEY is set but the 'requests' package isn't installed — "
            "transcription is disabled. Run: pip install -r requirements.txt"
        )

    app.add_handler(
        MessageHandler(
            filters.TEXT
            & filters.Regex(
                r"https?://(?:www\.|vt\.|vm\.|m\.)?"
                r"(?:instagram\.com|instagr\.am|tiktok\.com|twitter\.com|x\.com|"
                r"youtube\.com|youtu\.be)/\S+"
            )
            & ~filters.COMMAND,
            handle_media_link,
        )
    )
    app.add_handler(CallbackQueryHandler(youtube_download_chosen, pattern="^ytdl:"))
    if MEDIA_DOWNLOAD_ENABLED:
        logger.info("Media downloader enabled (Instagram/TikTok/Twitter/YouTube).")
    else:
        logger.warning(
            "yt-dlp isn't installed — the media downloader is disabled. "
            "Run: pip install -r requirements.txt"
        )

    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.REPLY & ~filters.COMMAND,
            handle_translate_reply,
        )
    )
    app.add_handler(CommandHandler("topactions", topactions_cmd))

    logger.info("Fun reply-actions enabled (%d actions).", len(ACTIONS))

    if TRANSLATE_ENABLED and requests is not None:
        app.add_handler(InlineQueryHandler(inline_translate))
        logger.info("Reply-to-translate and inline translation enabled (Groq).")

    # ----------------------------------------------------
    # Планировщик задач (JobQueue)
    # ----------------------------------------------------
    # Расписание и напоминание о заданиях теперь адаптивные: в личке время
    # отправки подстраивается под первую пару конкретного человека сегодня
    # (см. check_adaptive_schedule / check_adaptive_tasks), в группах — как
    # раньше, статично в 07:30/08:00. Поэтому вместо двух run_daily — две
    # поминутные проверки, как уже сделано для check_lesson_reminders.
    # Staggered `first=` offsets (5/15/25/35s) so these don't all land on the
    # same wall-clock second every minute and contend for the scheduler's
    # executor — when they piled up together, a slow job earlier in the
    # batch could push a later one past APScheduler's misfire grace window
    # and cause that entire run to be skipped outright (seen in production:
    # a reminder's exact-minute check got silently dropped this way).
    app.job_queue.run_repeating(check_adaptive_schedule, interval=60, first=5)
    app.job_queue.run_repeating(check_adaptive_tasks, interval=60, first=15)
    # Новый ежедневный утренний опрос (07:45 Вт-Сб)
    app.job_queue.run_daily(
        send_morning_poll_job,
        time=dtime(hour=POLL_HOUR, minute=POLL_MINUTE, tzinfo=TIMEZONE),
    )
    app.job_queue.run_repeating(check_lesson_reminders, interval=60, first=25)
    app.job_queue.run_repeating(check_reminders, interval=60, first=35)

    logger.info("Bot starting (polling)...")
    app.run_polling()


if __name__ == "__main__":
    main()
