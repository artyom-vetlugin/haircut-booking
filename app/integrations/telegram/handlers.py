import logging

from telegram import Update
from telegram.ext import ContextTypes

from app.integrations.telegram import messages as msg
from app.integrations.telegram.keyboards import main_menu_keyboard
from app.use_cases.start import StartUseCase

logger = logging.getLogger(__name__)

_start_use_case = StartUseCase()


async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return
    await _start_use_case.execute(telegram_user_id=update.effective_user.id)
    await update.message.reply_text(msg.WELCOME, reply_markup=main_menu_keyboard())


async def handle_book(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(msg.BOOK_STUB, reply_markup=main_menu_keyboard())


async def handle_my_appointment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(msg.MY_APPOINTMENT_STUB, reply_markup=main_menu_keyboard())


async def handle_reschedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(msg.RESCHEDULE_STUB, reply_markup=main_menu_keyboard())


async def handle_cancel_appointment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(msg.CANCEL_STUB, reply_markup=main_menu_keyboard())


async def handle_contact_master(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(msg.CONTACT_MASTER, reply_markup=main_menu_keyboard())


async def handle_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(msg.UNKNOWN_INPUT, reply_markup=main_menu_keyboard())
