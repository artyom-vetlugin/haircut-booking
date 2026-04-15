from telegram import KeyboardButton, ReplyKeyboardMarkup

from app.core.constants import (
    BTN_BOOK,
    BTN_CANCEL,
    BTN_CONTACT_MASTER,
    BTN_MY_APPOINTMENT,
    BTN_RESCHEDULE,
)


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton(BTN_BOOK)],
        [KeyboardButton(BTN_MY_APPOINTMENT), KeyboardButton(BTN_RESCHEDULE)],
        [KeyboardButton(BTN_CANCEL), KeyboardButton(BTN_CONTACT_MASTER)],
    ]
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True, one_time_keyboard=False)
