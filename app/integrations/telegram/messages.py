# Russian messages for the Telegram presentation layer.
# Business-level strings live in app.core.constants.
# Format placeholders: {date} = "Пн 20 апр", {time} = "10:00"

WELCOME = (
    "Привет! Я помогу вам записаться на стрижку.\n"
    "Выберите действие из меню ниже:"
)

MAIN_MENU_PROMPT = "Выберите действие:"

CONTACT_MASTER = "Для связи с мастером воспользуйтесь контактом, указанным в профиле."

UNKNOWN_INPUT = "Я не понял. Пожалуйста, воспользуйтесь меню:"

# ── Availability ─────────────────────────────────────────────────────────────

SELECT_DATE = "Выберите дату:"

NO_SLOTS_AVAILABLE = "На ближайшие дни нет свободных слотов. Попробуйте позже."

SELECT_SLOT = "Выберите время — {date}:"

# ── Booking ───────────────────────────────────────────────────────────────────

CONFIRM_BOOKING = "Записать вас {date} в {time}?"

BOOKING_SUCCESS = "✅ Вы записаны {date} в {time}. Ждём вас!"

ALREADY_HAS_BOOKING = (
    "У вас уже есть активная запись — {date} в {time}.\n"
    "Сначала отмените или перенесите её."
)

# Used when TooManyAppointmentsError surfaces as a race condition at confirm step
ALREADY_BOOKED = "У вас уже есть активная запись. Отмените или перенесите её."

# ── My appointment ────────────────────────────────────────────────────────────

YOUR_APPOINTMENT = "📅 Ваша запись: {date} в {time}."

NO_APPOINTMENT = "У вас нет активных записей."

# ── Cancel ────────────────────────────────────────────────────────────────────

CONFIRM_CANCEL = "Отменить запись {date} в {time}?"

CANCEL_SUCCESS = "✅ Запись отменена."

CANCEL_ABORTED = "Отмена не выполнена. Запись сохранена."

# ── Reschedule ────────────────────────────────────────────────────────────────

RESCHEDULE_PROMPT = "Текущая запись: {date} в {time}.\n\nВыберите новую дату:"

CONFIRM_RESCHEDULE = "Перенести запись на {date} в {time}?"

RESCHEDULE_SUCCESS = "✅ Запись перенесена на {date} в {time}."

# ── Errors ────────────────────────────────────────────────────────────────────

BOOKING_CONFLICT_MSG = (
    "❌ На это время уже есть запись. Выберите другое время.\n"
    "Нажмите «Записаться» и начните заново."
)

SLOT_NO_LONGER_AVAILABLE = (
    "❌ Это время больше недоступно. Пожалуйста, выберите другое.\n"
    "Нажмите «Записаться» или «Перенести запись» и начните заново."
)

CALENDAR_ERROR = "Не удалось синхронизировать с календарём. Попробуйте ещё раз."

FLOW_EXPIRED = "Сессия устарела. Пожалуйста, начните заново."

ERROR_TRY_AGAIN = "Произошла ошибка. Попробуйте ещё раз."
