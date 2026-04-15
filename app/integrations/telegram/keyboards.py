from __future__ import annotations

from datetime import date, datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from app.core.constants import (
    BTN_BOOK,
    BTN_CANCEL,
    BTN_CONTACT_MASTER,
    BTN_MY_APPOINTMENT,
    BTN_RESCHEDULE,
)
from app.schemas.availability import DaySlots, TimeSlot

_RU_WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
_RU_MONTHS = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]

_DATES_PER_ROW = 2
_SLOTS_PER_ROW = 3


def format_date_ru(d: date) -> str:
    """Return a Russian short date string, e.g. «Пн 20 апр»."""
    weekday = _RU_WEEKDAYS[d.weekday()]
    month = _RU_MONTHS[d.month - 1]
    return f"{weekday} {d.day} {month}"


def format_time(dt: datetime) -> str:
    """Return HH:MM for the given datetime."""
    return dt.strftime("%H:%M")


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton(BTN_BOOK)],
        [KeyboardButton(BTN_MY_APPOINTMENT), KeyboardButton(BTN_RESCHEDULE)],
        [KeyboardButton(BTN_CANCEL), KeyboardButton(BTN_CONTACT_MASTER)],
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True, one_time_keyboard=False)


def dates_keyboard(day_slots: list[DaySlots], cb_prefix: str) -> InlineKeyboardMarkup:
    """Date-selection inline keyboard.

    Each button callback: ``{cb_prefix}:{date.isoformat()}``
    e.g. ``"book_date:2026-04-20"``
    """
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for ds in day_slots:
        label = format_date_ru(ds.date)
        btn = InlineKeyboardButton(label, callback_data=f"{cb_prefix}:{ds.date.isoformat()}")
        row.append(btn)
        if len(row) == _DATES_PER_ROW:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("❌ Отмена", callback_data="flow_back")])
    return InlineKeyboardMarkup(rows)


def slots_keyboard(slots: list[TimeSlot], cb_prefix: str) -> InlineKeyboardMarkup:
    """Time-slot selection inline keyboard.

    Each button callback: ``{cb_prefix}:{slot.start.isoformat()}``
    e.g. ``"book_slot:2026-04-20T10:00:00+05:00"``
    """
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for slot in slots:
        label = format_time(slot.start)
        btn = InlineKeyboardButton(label, callback_data=f"{cb_prefix}:{slot.start.isoformat()}")
        row.append(btn)
        if len(row) == _SLOTS_PER_ROW:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data="flow_back")])
    return InlineKeyboardMarkup(rows)


def confirm_keyboard(confirm_cb: str, back_cb: str = "flow_back") -> InlineKeyboardMarkup:
    """Two-button confirm / cancel keyboard."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Подтвердить", callback_data=confirm_cb),
        InlineKeyboardButton("❌ Отмена", callback_data=back_cb),
    ]])
