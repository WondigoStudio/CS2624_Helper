"""ConversationHandler state constants, plus the lesson-reminder lead time."""

CHOOSING_SUBJECT, TYPING_TITLE, TYPING_DATE, TYPING_TIME = range(4)
CHOOSING_TARGET_USER, SCH_WEEKDAY, SCH_SUBJECT, SCH_TIME, SCH_ROOM = range(4, 9)
PHOTO_TARGET_USER, PHOTO_ROOM_NAME, PHOTO_WAITING = range(9, 12)
# Conversation states for /edittask (edit an existing homework task)
EDIT_TASK_PICK, EDIT_TASK_FIELD, EDIT_TASK_SUBJECT, EDIT_TASK_TITLE, EDIT_TASK_DATE, EDIT_TASK_TIME = range(12, 18)
# Conversation states for /editschedule (edit an existing timetable lesson)
EDIT_LESSON_PICK, EDIT_LESSON_FIELD, EDIT_LESSON_WEEKDAY, EDIT_LESSON_SUBJECT, EDIT_LESSON_TIME, EDIT_LESSON_ROOM = range(18, 24)
# Extra states for /add: description (after title) and attachment (after time)
TYPING_DESCRIPTION, TYPING_ATTACHMENT = range(24, 26)
# Extra state for /edittask: editing description or attachment of an existing task
EDIT_TASK_DESCRIPTION, EDIT_TASK_ATTACHMENT = range(26, 28)
# How long before a lesson starts to send a heads-up reminder
LESSON_REMINDER_MINUTES = 10
