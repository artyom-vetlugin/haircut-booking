"""Claude tool definitions (JSON schemas) for booking operations.

Implementations live in app/tools/booking_tools.py.
"""

from __future__ import annotations

from typing import Any

GET_AVAILABLE_SLOTS: dict[str, Any] = {
    "name": "get_available_slots",
    "description": (
        "Получить список доступных временных слотов для записи. "
        "Если дата не указана, возвращаются слоты на ближайшие 7 дней."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "date": {
                "type": "string",
                "description": "Дата в формате YYYY-MM-DD. Необязательно.",
            },
        },
        "required": [],
    },
}

GET_MY_APPOINTMENT: dict[str, Any] = {
    "name": "get_my_appointment",
    "description": "Получить текущую активную запись пользователя.",
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

CREATE_BOOKING: dict[str, Any] = {
    "name": "create_booking",
    "description": (
        "Создать запись на указанное время. "
        "Вызывай только после того, как пользователь явно подтвердил конкретный слот."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "slot_start": {
                "type": "string",
                "description": (
                    "Начало записи в ISO 8601 с часовым поясом, "
                    "например 2026-04-21T10:00:00+06:00"
                ),
            },
        },
        "required": ["slot_start"],
    },
}

CANCEL_APPOINTMENT: dict[str, Any] = {
    "name": "cancel_appointment",
    "description": "Отменить текущую активную запись пользователя.",
    "input_schema": {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Причина отмены. Необязательно.",
            },
        },
        "required": [],
    },
}

RESCHEDULE_APPOINTMENT: dict[str, Any] = {
    "name": "reschedule_appointment",
    "description": (
        "Перенести текущую запись пользователя на новое время. "
        "Вызывай только после того, как пользователь явно подтвердил новый слот."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "new_slot_start": {
                "type": "string",
                "description": "Новое время записи в ISO 8601 с часовым поясом.",
            },
        },
        "required": ["new_slot_start"],
    },
}

ALL_TOOLS: list[dict[str, Any]] = [
    GET_AVAILABLE_SLOTS,
    GET_MY_APPOINTMENT,
    CREATE_BOOKING,
    CANCEL_APPOINTMENT,
    RESCHEDULE_APPOINTMENT,
]
