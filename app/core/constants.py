# Fixed business constant — always 60 min per spec, not configurable
APPOINTMENT_DURATION_MINUTES = 60

# Telegram main menu button labels (Russian)
BTN_BOOK = "Записаться"
BTN_MY_APPOINTMENT = "Моя запись"
BTN_RESCHEDULE = "Перенести запись"
BTN_CANCEL = "Отменить запись"
BTN_CONTACT_MASTER = "Связаться с мастером"

# Master menu button labels (Russian)
BTN_MASTER_BOOK_CLIENT      = "Записать клиента"
BTN_MASTER_ALL_APPOINTMENTS = "Все записи"
BTN_MASTER_RESCHEDULE       = "Перенести запись клиента"
BTN_MASTER_CANCEL           = "Отменить запись клиента"
BTN_MASTER_FREE_SLOTS       = "Свободные слоты"

# User-facing Russian messages
MSG_BOOKING_SUCCESS = "Вы записаны! Ждём вас."
MSG_BOOKING_CANCELLED = "Запись отменена."
MSG_BOOKING_RESCHEDULED = "Запись перенесена."
MSG_NO_APPOINTMENT = "У вас нет активных записей."
MSG_BOOKING_CONFLICT = "На это время уже есть запись. Пожалуйста, выберите другое время."
MSG_OUTSIDE_HOURS = "Запись доступна с {start}:00 до {end}:00. Пожалуйста, выберите другое время."
MSG_TOO_SOON = "Запись возможна не менее чем за {hours} ч. до начала."
MSG_TOO_FAR = "Запись доступна не более чем на {days} дней вперёд."
MSG_INTERNAL_ERROR = "Произошла ошибка. Попробуйте позже или свяжитесь с мастером."
