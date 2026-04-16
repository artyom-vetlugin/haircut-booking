"""Telegram update handlers — thin dispatcher for the button-driven booking state machine.

Each handler parses Telegram-specific input, delegates to the appropriate use case
for flow orchestration, then formats the result into a Telegram message.

Flow overview:
  Book:       handle_book → [book_date:…] → [book_slot:…] → [book_confirm]
  My appt:    handle_my_appointment  (no state)
  Cancel:     handle_cancel_appointment → [cancel_confirm]
  Reschedule: handle_reschedule → [res_date:…] → [res_slot:…] → [res_confirm]
  Any step:   [flow_back] → IDLE + main-menu prompt
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from app.core import states
from app.core.config import settings
from app.core.exceptions import (
    BookingConflictError,
    CalendarSyncError,
    FlowExpiredError,
    NoAppointmentError,
    SlotUnavailableError,
    TooManyAppointmentsError,
)
from app.db.models import Appointment, Client
from app.db.session import AsyncSessionLocal
from app.integrations.telegram import messages as msg

from app.integrations.telegram.keyboards import (
    appointments_keyboard,
    confirm_keyboard,
    dates_keyboard,
    format_date_ru,
    format_time,
    main_menu_keyboard,
    master_menu_keyboard,
    slots_keyboard,
)
from app.repositories.bot_session import BotSessionRepository
from app.use_cases.booking_flow import BookingFlowUseCase
from app.use_cases.cancel_flow import CancelFlowUseCase
from app.use_cases.deps import HandlerServices, make_services
from app.use_cases.handle_free_text_message import HandleFreeTextMessageUseCase
from app.use_cases.master_booking_flow import MasterBookingFlowUseCase
from app.use_cases.master_cancel_flow import MasterCancelFlowUseCase
from app.use_cases.master_reschedule_flow import MasterRescheduleFlowUseCase
from app.use_cases.reschedule_flow import RescheduleFlowUseCase
from app.use_cases.start import StartUseCase

logger = logging.getLogger(__name__)

_start_use_case = StartUseCase()
_free_text_use_case = HandleFreeTextMessageUseCase()
_booking_flow = BookingFlowUseCase()
_reschedule_flow = RescheduleFlowUseCase()
_cancel_flow = CancelFlowUseCase()
_master_booking_flow = MasterBookingFlowUseCase()
_master_reschedule_flow = MasterRescheduleFlowUseCase()
_master_cancel_flow = MasterCancelFlowUseCase()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_master(user_id: int) -> bool:
    """Return True if the given Telegram user ID belongs to the master."""
    return user_id == settings.telegram_master_chat_id


def _tz() -> ZoneInfo:
    return ZoneInfo(settings.app_timezone)


def _format_appt(appt: Appointment) -> tuple[str, str]:
    """Return (date_str, time_str) for an appointment in the configured timezone."""
    local = appt.start_at.astimezone(_tz())
    return format_date_ru(local.date()), format_time(local)


async def _upsert_client(svc: HandlerServices, tg_user: object) -> Client:
    """Return the Client for the given Telegram user, creating one if needed."""
    client = await svc.client_repo.get_by_telegram_user_id(tg_user.id)  # type: ignore[attr-defined]
    if client is None:
        client = await svc.client_repo.create(
            telegram_user_id=tg_user.id,  # type: ignore[attr-defined]
            first_name=tg_user.first_name,  # type: ignore[attr-defined]
            last_name=tg_user.last_name,  # type: ignore[attr-defined]
            telegram_username=tg_user.username,  # type: ignore[attr-defined]
        )
    return client


async def _reset_user_state(user_id: int) -> None:
    """Reset BotSession to IDLE in a fresh transaction. Failures are swallowed."""
    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                await BotSessionRepository(session).upsert(user_id, states.IDLE, {})
    except Exception:
        logger.exception("Failed to reset state for user %s after flow error", user_id)


def _horizon_dt(tz: ZoneInfo) -> datetime:
    horizon = datetime.now(tz=tz).date() + timedelta(days=settings.booking_horizon_days)
    return datetime(horizon.year, horizon.month, horizon.day, 23, 59, 59, tzinfo=tz)


# ── Main-menu handlers ────────────────────────────────────────────────────────

async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user is None or update.message is None:
        return
    user = update.effective_user
    async with AsyncSessionLocal() as session:
        async with session.begin():
            await _start_use_case.execute(
                session=session,
                telegram_user_id=user.id,
                first_name=user.first_name,
                last_name=user.last_name,
                username=user.username,
            )
    if _is_master(user.id):
        await update.message.reply_text(msg.MASTER_WELCOME, reply_markup=master_menu_keyboard())
    else:
        await update.message.reply_text(msg.WELCOME, reply_markup=main_menu_keyboard())


async def handle_book(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    user = update.effective_user
    tz = _tz()
    now = datetime.now(tz=tz)
    today = now.date()
    horizon = today + timedelta(days=settings.booking_horizon_days)

    day_slots = None
    async with AsyncSessionLocal() as session:
        async with session.begin():
            svc = make_services(session)
            client = await _upsert_client(svc, user)

            existing = await svc.appointment_service.get_future_appointment_for_client(
                client.id, now=now
            )
            if existing is not None:
                d, t = _format_appt(existing)
                await update.message.reply_text(
                    msg.ALREADY_HAS_BOOKING.format(date=d, time=t),
                    reply_markup=main_menu_keyboard(),
                )
                return

            busy = await svc.calendar.get_busy_intervals(now, _horizon_dt(tz))
            day_slots = svc.availability.get_available_slots_for_range(today, horizon, busy, now)
            await svc.session_repo.upsert(user.id, states.BOOKING_SELECT_DATE, {})

    if not day_slots:
        await update.message.reply_text(msg.NO_SLOTS_AVAILABLE, reply_markup=main_menu_keyboard())
        return

    await update.message.reply_text(msg.SELECT_DATE, reply_markup=dates_keyboard(day_slots, "book_date"))


async def handle_my_appointment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    user = update.effective_user
    tz = _tz()
    now = datetime.now(tz=tz)

    appt_date: str | None = None
    appt_time: str | None = None
    async with AsyncSessionLocal() as session:
        async with session.begin():
            svc = make_services(session)
            client = await _upsert_client(svc, user)
            appt = await svc.appointment_service.get_future_appointment_for_client(
                client.id, now=now
            )
            if appt is not None:
                appt_date, appt_time = _format_appt(appt)

    if appt_date is None:
        text = msg.NO_APPOINTMENT
    else:
        text = msg.YOUR_APPOINTMENT.format(date=appt_date, time=appt_time)
    await update.message.reply_text(text, reply_markup=main_menu_keyboard())


async def handle_cancel_appointment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    user = update.effective_user
    tz = _tz()
    now = datetime.now(tz=tz)

    appt_date: str | None = None
    appt_time: str | None = None
    async with AsyncSessionLocal() as session:
        async with session.begin():
            svc = make_services(session)
            client = await _upsert_client(svc, user)
            appt = await svc.appointment_service.get_future_appointment_for_client(
                client.id, now=now
            )
            if appt is None:
                await update.message.reply_text(msg.NO_APPOINTMENT, reply_markup=main_menu_keyboard())
                return
            appt_date, appt_time = _format_appt(appt)
            await svc.session_repo.upsert(user.id, states.CANCEL_CONFIRM, {})

    await update.message.reply_text(
        msg.CONFIRM_CANCEL.format(date=appt_date, time=appt_time),
        reply_markup=confirm_keyboard("cancel_confirm"),
    )


async def handle_reschedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    user = update.effective_user
    tz = _tz()
    now = datetime.now(tz=tz)
    today = now.date()
    horizon = today + timedelta(days=settings.booking_horizon_days)

    appt_date: str | None = None
    appt_time: str | None = None
    day_slots = None
    async with AsyncSessionLocal() as session:
        async with session.begin():
            svc = make_services(session)
            client = await _upsert_client(svc, user)
            appt = await svc.appointment_service.get_future_appointment_for_client(
                client.id, now=now
            )
            if appt is None:
                await update.message.reply_text(msg.NO_APPOINTMENT, reply_markup=main_menu_keyboard())
                return
            appt_date, appt_time = _format_appt(appt)
            busy = await svc.calendar.get_busy_intervals(now, _horizon_dt(tz))
            day_slots = svc.availability.get_available_slots_for_range(today, horizon, busy, now)
            await svc.session_repo.upsert(user.id, states.RESCHEDULE_SELECT_DATE, {})

    if not day_slots:
        await update.message.reply_text(msg.NO_SLOTS_AVAILABLE, reply_markup=main_menu_keyboard())
        return

    await update.message.reply_text(
        msg.RESCHEDULE_PROMPT.format(date=appt_date, time=appt_time),
        reply_markup=dates_keyboard(day_slots, "res_date"),
    )


async def handle_contact_master(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(msg.CONTACT_MASTER, reply_markup=main_menu_keyboard())


# ── Master main-menu handlers ─────────────────────────────────────────────────

async def handle_master_book_client(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    user = update.effective_user
    async with AsyncSessionLocal() as session:
        async with session.begin():
            svc = make_services(session)
            await svc.session_repo.upsert(user.id, states.MASTER_BOOKING_ENTER_NAME, {})
    await update.message.reply_text(msg.MASTER_ENTER_CLIENT_NAME)


async def handle_master_all_appointments(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if update.message is None or update.effective_user is None:
        return
    tz = _tz()
    now = datetime.now(tz=tz)
    async with AsyncSessionLocal() as session:
        async with session.begin():
            svc = make_services(session)
            appts = await svc.appointment_service.get_all_future_appointments(now=now)

    if not appts:
        await update.message.reply_text(
            msg.MASTER_NO_APPOINTMENTS, reply_markup=master_menu_keyboard()
        )
        return

    lines = []
    for appt in appts:
        local = appt.start_at.astimezone(tz)
        client_name = appt.client.first_name or "Клиент"
        if appt.client.last_name:
            client_name = f"{client_name} {appt.client.last_name}"
        lines.append(f"• {client_name} — {format_date_ru(local.date())} в {format_time(local)}")

    await update.message.reply_text(
        "\n".join(lines), reply_markup=master_menu_keyboard()
    )


async def handle_master_reschedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    user = update.effective_user
    tz = _tz()
    now = datetime.now(tz=tz)

    async with AsyncSessionLocal() as session:
        async with session.begin():
            svc = make_services(session)
            appts = await svc.appointment_service.get_all_future_appointments(now=now)
            await svc.session_repo.upsert(user.id, states.MASTER_RESCHEDULE_SELECT_APPT, {})

    if not appts:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                svc = make_services(session)
                await svc.session_repo.upsert(user.id, states.IDLE, {})
        await update.message.reply_text(
            msg.MASTER_NO_APPOINTMENTS, reply_markup=master_menu_keyboard()
        )
        return

    await update.message.reply_text(
        msg.MASTER_SELECT_APPOINTMENT,
        reply_markup=appointments_keyboard(appts, "master_res_appt"),
    )


async def handle_master_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    user = update.effective_user
    tz = _tz()
    now = datetime.now(tz=tz)

    async with AsyncSessionLocal() as session:
        async with session.begin():
            svc = make_services(session)
            appts = await svc.appointment_service.get_all_future_appointments(now=now)
            await svc.session_repo.upsert(user.id, states.MASTER_CANCEL_SELECT_APPT, {})

    if not appts:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                svc = make_services(session)
                await svc.session_repo.upsert(user.id, states.IDLE, {})
        await update.message.reply_text(
            msg.MASTER_NO_APPOINTMENTS, reply_markup=master_menu_keyboard()
        )
        return

    await update.message.reply_text(
        msg.MASTER_SELECT_APPOINTMENT,
        reply_markup=appointments_keyboard(appts, "master_cancel_appt"),
    )


async def handle_master_free_slots(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    tz = _tz()
    now = datetime.now(tz=tz)
    today = now.date()
    horizon = today + timedelta(days=settings.booking_horizon_days)

    async with AsyncSessionLocal() as session:
        async with session.begin():
            svc = make_services(session)
            busy = await svc.calendar.get_busy_intervals(now, _horizon_dt(tz))
            day_slots = svc.availability.get_available_slots_for_range(today, horizon, busy, now)

    if not day_slots:
        await update.message.reply_text(
            msg.NO_SLOTS_AVAILABLE, reply_markup=master_menu_keyboard()
        )
        return

    await update.message.reply_text(
        msg.SELECT_DATE,
        reply_markup=dates_keyboard(day_slots, "master_free_date"),
    )


async def handle_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    user = update.effective_user
    text = update.message.text or ""
    if not text.strip():
        menu = master_menu_keyboard() if _is_master(user.id) else main_menu_keyboard()
        await update.message.reply_text(msg.UNKNOWN_INPUT, reply_markup=menu)
        return

    # Master: intercept client-name entry during booking flow, then show menu for anything else
    if _is_master(user.id):
        handled = await _handle_master_text_input(update, user.id, text.strip())
        if handled:
            return
        await update.message.reply_text(msg.MASTER_MAIN_MENU_PROMPT, reply_markup=master_menu_keyboard())
        return

    reply: str
    in_flow: bool
    async with AsyncSessionLocal() as session:
        async with session.begin():
            svc = make_services(session)
            reply, in_flow = await _free_text_use_case.execute(user.id, text, svc)

    if in_flow:
        back_keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("◀️ Назад", callback_data="flow_back")]]
        )
        await update.message.reply_text(reply, reply_markup=back_keyboard)
    else:
        menu = master_menu_keyboard() if _is_master(user.id) else main_menu_keyboard()
        await update.message.reply_text(reply, reply_markup=menu)


async def _handle_master_text_input(update: Update, master_id: int, text: str) -> bool:
    """Handle free-text input from the master when in a state that expects it.

    Returns True if the input was consumed, False if it should fall through to Claude.
    """
    tz = _tz()
    now = datetime.now(tz=tz)

    # Check current master state
    async with AsyncSessionLocal() as session:
        async with session.begin():
            bot_session_repo = BotSessionRepository(session)
            bot_session = await bot_session_repo.get_by_telegram_user_id(master_id)

    if bot_session is None or bot_session.current_state != states.MASTER_BOOKING_ENTER_NAME:
        return False

    # Master is entering a client name
    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                svc = make_services(session)
                day_slots = await _master_booking_flow.on_name_entered(
                    master_id=master_id, name=text, svc=svc, now=now
                )
    except FlowExpiredError:
        await update.message.reply_text(msg.FLOW_EXPIRED, reply_markup=master_menu_keyboard())
        return True
    except Exception:
        logger.exception("Master booking flow name entry failed for user %s", master_id)
        await update.message.reply_text(msg.ERROR_TRY_AGAIN, reply_markup=master_menu_keyboard())
        return True

    if not day_slots:
        await update.message.reply_text(msg.NO_SLOTS_AVAILABLE, reply_markup=master_menu_keyboard())
        return True

    await update.message.reply_text(
        msg.SELECT_DATE,
        reply_markup=dates_keyboard(day_slots, "master_book_date"),
    )
    return True


# ── Callback query dispatcher ─────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    data = query.data or ""

    if data.startswith("book_date:"):
        await _on_book_date(query, data[len("book_date:"):])
    elif data.startswith("book_slot:"):
        await _on_book_slot(query, data[len("book_slot:"):])
    elif data == "book_confirm":
        await _on_book_confirm(query)
    elif data.startswith("res_date:"):
        await _on_res_date(query, data[len("res_date:"):])
    elif data.startswith("res_slot:"):
        await _on_res_slot(query, data[len("res_slot:"):])
    elif data == "res_confirm":
        await _on_res_confirm(query)
    elif data == "cancel_confirm":
        await _on_cancel_confirm(query)
    elif data.startswith("master_book_date:"):
        await _on_master_book_date(query, data[len("master_book_date:"):])
    elif data.startswith("master_book_slot:"):
        await _on_master_book_slot(query, data[len("master_book_slot:"):])
    elif data == "master_book_confirm":
        await _on_master_book_confirm(query)
    elif data.startswith("master_res_appt:"):
        await _on_master_res_appt(query, data[len("master_res_appt:"):])
    elif data.startswith("master_res_date:"):
        await _on_master_res_date(query, data[len("master_res_date:"):])
    elif data.startswith("master_res_slot:"):
        await _on_master_res_slot(query, data[len("master_res_slot:"):])
    elif data == "master_res_confirm":
        await _on_master_res_confirm(query)
    elif data.startswith("master_cancel_appt:"):
        await _on_master_cancel_appt(query, data[len("master_cancel_appt:"):])
    elif data == "master_cancel_confirm":
        await _on_master_cancel_confirm(query)
    elif data.startswith("master_free_date:"):
        await _on_master_free_date(query, data[len("master_free_date:"):])
    elif data == "flow_back":
        await _on_flow_back(query)
    else:
        logger.warning("Unhandled callback data: %s", data)


# ── Booking flow callbacks ────────────────────────────────────────────────────

async def _on_book_date(query: CallbackQuery, date_str: str) -> None:
    user = query.from_user
    tz = _tz()
    now = datetime.now(tz=tz)

    try:
        selected_date = date.fromisoformat(date_str)
    except ValueError:
        await query.edit_message_text(msg.ERROR_TRY_AGAIN)
        return

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                svc = make_services(session)
                slots_list = await _booking_flow.on_date_selected(
                    user_id=user.id, selected_date=selected_date, svc=svc, now=now
                )
    except FlowExpiredError:
        await query.edit_message_text(msg.FLOW_EXPIRED)
        return

    if not slots_list:
        await query.edit_message_text(msg.NO_SLOTS_AVAILABLE)
        return

    await query.edit_message_text(
        msg.SELECT_SLOT.format(date=format_date_ru(selected_date)),
        reply_markup=slots_keyboard(slots_list, "book_slot"),
    )


async def _on_book_slot(query: CallbackQuery, slot_iso: str) -> None:
    user = query.from_user
    tz = _tz()

    try:
        slot_start = datetime.fromisoformat(slot_iso)
    except ValueError:
        await query.edit_message_text(msg.ERROR_TRY_AGAIN)
        return

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                svc = make_services(session)
                await _booking_flow.on_slot_selected(user_id=user.id, slot_iso=slot_iso, svc=svc)
    except FlowExpiredError:
        await query.edit_message_text(msg.FLOW_EXPIRED)
        return

    local = slot_start.astimezone(tz)
    await query.edit_message_text(
        msg.CONFIRM_BOOKING.format(date=format_date_ru(local.date()), time=format_time(local)),
        reply_markup=confirm_keyboard("book_confirm"),
    )


async def _on_book_confirm(query: CallbackQuery) -> None:
    user = query.from_user
    tz = _tz()
    now = datetime.now(tz=tz)
    result_text: str = msg.ERROR_TRY_AGAIN

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                svc = make_services(session)
                appt = await _booking_flow.on_confirm(
                    user_id=user.id,
                    first_name=user.first_name,
                    last_name=user.last_name,
                    username=user.username,
                    svc=svc,
                    now=now,
                )
        local = appt.start_at.astimezone(tz)
        result_text = msg.BOOKING_SUCCESS.format(
            date=format_date_ru(local.date()), time=format_time(local)
        )
    except FlowExpiredError:
        result_text = msg.FLOW_EXPIRED
    except TooManyAppointmentsError:
        await _reset_user_state(user.id)
        result_text = msg.ALREADY_BOOKED
    except BookingConflictError:
        await _reset_user_state(user.id)
        result_text = msg.BOOKING_CONFLICT_MSG
    except SlotUnavailableError:
        await _reset_user_state(user.id)
        result_text = msg.SLOT_NO_LONGER_AVAILABLE
    except CalendarSyncError:
        logger.exception("Calendar sync error during booking for user %s", user.id)
        await _reset_user_state(user.id)
        result_text = msg.CALENDAR_ERROR
    except Exception:
        logger.exception("Booking failed for user %s", user.id)
        await _reset_user_state(user.id)
        result_text = msg.ERROR_TRY_AGAIN

    await query.edit_message_text(result_text)


# ── Reschedule flow callbacks ─────────────────────────────────────────────────

async def _on_res_date(query: CallbackQuery, date_str: str) -> None:
    user = query.from_user
    tz = _tz()
    now = datetime.now(tz=tz)

    try:
        selected_date = date.fromisoformat(date_str)
    except ValueError:
        await query.edit_message_text(msg.ERROR_TRY_AGAIN)
        return

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                svc = make_services(session)
                slots_list = await _reschedule_flow.on_date_selected(
                    user_id=user.id, selected_date=selected_date, svc=svc, now=now
                )
    except FlowExpiredError:
        await query.edit_message_text(msg.FLOW_EXPIRED)
        return

    if not slots_list:
        await query.edit_message_text(msg.NO_SLOTS_AVAILABLE)
        return

    await query.edit_message_text(
        msg.SELECT_SLOT.format(date=format_date_ru(selected_date)),
        reply_markup=slots_keyboard(slots_list, "res_slot"),
    )


async def _on_res_slot(query: CallbackQuery, slot_iso: str) -> None:
    user = query.from_user
    tz = _tz()

    try:
        slot_start = datetime.fromisoformat(slot_iso)
    except ValueError:
        await query.edit_message_text(msg.ERROR_TRY_AGAIN)
        return

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                svc = make_services(session)
                await _reschedule_flow.on_slot_selected(user_id=user.id, slot_iso=slot_iso, svc=svc)
    except FlowExpiredError:
        await query.edit_message_text(msg.FLOW_EXPIRED)
        return

    local = slot_start.astimezone(tz)
    await query.edit_message_text(
        msg.CONFIRM_RESCHEDULE.format(date=format_date_ru(local.date()), time=format_time(local)),
        reply_markup=confirm_keyboard("res_confirm"),
    )


async def _on_res_confirm(query: CallbackQuery) -> None:
    user = query.from_user
    tz = _tz()
    now = datetime.now(tz=tz)
    result_text: str = msg.ERROR_TRY_AGAIN

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                svc = make_services(session)
                appt = await _reschedule_flow.on_confirm(
                    user_id=user.id,
                    first_name=user.first_name,
                    last_name=user.last_name,
                    username=user.username,
                    svc=svc,
                    now=now,
                )
        local = appt.start_at.astimezone(tz)
        result_text = msg.RESCHEDULE_SUCCESS.format(
            date=format_date_ru(local.date()), time=format_time(local)
        )
    except FlowExpiredError:
        result_text = msg.FLOW_EXPIRED
    except NoAppointmentError:
        await _reset_user_state(user.id)
        result_text = msg.NO_APPOINTMENT
    except BookingConflictError:
        await _reset_user_state(user.id)
        result_text = msg.BOOKING_CONFLICT_MSG
    except SlotUnavailableError:
        await _reset_user_state(user.id)
        result_text = msg.SLOT_NO_LONGER_AVAILABLE
    except CalendarSyncError:
        logger.exception("Calendar sync error during reschedule for user %s", user.id)
        await _reset_user_state(user.id)
        result_text = msg.CALENDAR_ERROR
    except Exception:
        logger.exception("Reschedule failed for user %s", user.id)
        await _reset_user_state(user.id)
        result_text = msg.ERROR_TRY_AGAIN

    await query.edit_message_text(result_text)


# ── Cancel flow callbacks ─────────────────────────────────────────────────────

async def _on_cancel_confirm(query: CallbackQuery) -> None:
    user = query.from_user
    tz = _tz()
    now = datetime.now(tz=tz)
    result_text: str = msg.ERROR_TRY_AGAIN

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                svc = make_services(session)
                await _cancel_flow.on_confirm(
                    user_id=user.id,
                    first_name=user.first_name,
                    last_name=user.last_name,
                    username=user.username,
                    svc=svc,
                    now=now,
                )
        result_text = msg.CANCEL_SUCCESS
    except FlowExpiredError:
        result_text = msg.FLOW_EXPIRED
    except NoAppointmentError:
        await _reset_user_state(user.id)
        result_text = msg.NO_APPOINTMENT
    except CalendarSyncError:
        logger.exception("Calendar sync error during cancellation for user %s", user.id)
        await _reset_user_state(user.id)
        result_text = msg.CALENDAR_ERROR
    except Exception:
        logger.exception("Cancel failed for user %s", user.id)
        await _reset_user_state(user.id)
        result_text = msg.ERROR_TRY_AGAIN

    await query.edit_message_text(result_text)


async def _on_flow_back(query: CallbackQuery) -> None:
    user = query.from_user
    async with AsyncSessionLocal() as session:
        async with session.begin():
            svc = make_services(session)
            await svc.session_repo.upsert(user.id, states.IDLE, {})
    if _is_master(user.id):
        await query.edit_message_text(msg.MASTER_MAIN_MENU_PROMPT)
    else:
        await query.edit_message_text(msg.MAIN_MENU_PROMPT)


# ── Master booking flow callbacks ─────────────────────────────────────────────

async def _on_master_book_date(query: CallbackQuery, date_str: str) -> None:
    user = query.from_user
    tz = _tz()
    now = datetime.now(tz=tz)

    try:
        selected_date = date.fromisoformat(date_str)
    except ValueError:
        await query.edit_message_text(msg.ERROR_TRY_AGAIN)
        return

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                svc = make_services(session)
                slots_list = await _master_booking_flow.on_date_selected(
                    master_id=user.id, selected_date=selected_date, svc=svc, now=now
                )
    except FlowExpiredError:
        await query.edit_message_text(msg.FLOW_EXPIRED)
        return

    if not slots_list:
        await query.edit_message_text(msg.NO_SLOTS_AVAILABLE)
        return

    await query.edit_message_text(
        msg.SELECT_SLOT.format(date=format_date_ru(selected_date)),
        reply_markup=slots_keyboard(slots_list, "master_book_slot"),
    )


async def _on_master_book_slot(query: CallbackQuery, slot_iso: str) -> None:
    user = query.from_user
    tz = _tz()

    try:
        slot_start = datetime.fromisoformat(slot_iso)
    except ValueError:
        await query.edit_message_text(msg.ERROR_TRY_AGAIN)
        return

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                svc = make_services(session)
                await _master_booking_flow.on_slot_selected(
                    master_id=user.id, slot_iso=slot_iso, svc=svc
                )
    except FlowExpiredError:
        await query.edit_message_text(msg.FLOW_EXPIRED)
        return

    local = slot_start.astimezone(tz)
    await query.edit_message_text(
        msg.CONFIRM_BOOKING.format(date=format_date_ru(local.date()), time=format_time(local)),
        reply_markup=confirm_keyboard("master_book_confirm"),
    )


async def _on_master_book_confirm(query: CallbackQuery) -> None:
    user = query.from_user
    tz = _tz()
    now = datetime.now(tz=tz)
    result_text: str = msg.ERROR_TRY_AGAIN

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                svc = make_services(session)
                appt = await _master_booking_flow.on_confirm(
                    master_id=user.id, svc=svc, now=now
                )
        local = appt.start_at.astimezone(tz)
        # Load client name from the appointment (need a fresh read with client)
        async with AsyncSessionLocal() as session:
            async with session.begin():
                svc = make_services(session)
                fresh = await svc.appointment_service.get_by_id(appt.id, load_client=True)
        client_name = "Клиент"
        if fresh is not None and fresh.client is not None:
            client_name = fresh.client.first_name or "Клиент"
        result_text = msg.MASTER_BOOKING_SUCCESS_FOR.format(
            name=client_name, date=format_date_ru(local.date()), time=format_time(local)
        )
    except FlowExpiredError:
        result_text = msg.FLOW_EXPIRED
    except BookingConflictError:
        await _reset_user_state(user.id)
        result_text = msg.BOOKING_CONFLICT_MSG
    except SlotUnavailableError:
        await _reset_user_state(user.id)
        result_text = msg.SLOT_NO_LONGER_AVAILABLE
    except CalendarSyncError:
        logger.exception("Calendar sync error during master booking for user %s", user.id)
        await _reset_user_state(user.id)
        result_text = msg.CALENDAR_ERROR
    except Exception:
        logger.exception("Master booking confirm failed for user %s", user.id)
        await _reset_user_state(user.id)
        result_text = msg.ERROR_TRY_AGAIN

    await query.edit_message_text(result_text)


# ── Master reschedule flow callbacks ──────────────────────────────────────────

async def _on_master_res_appt(query: CallbackQuery, appt_id: str) -> None:
    user = query.from_user
    tz = _tz()
    now = datetime.now(tz=tz)

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                svc = make_services(session)
                day_slots = await _master_reschedule_flow.on_appointment_selected(
                    master_id=user.id, appointment_id=appt_id, svc=svc, now=now
                )
    except FlowExpiredError:
        await query.edit_message_text(msg.FLOW_EXPIRED)
        return

    if not day_slots:
        await query.edit_message_text(msg.NO_SLOTS_AVAILABLE)
        return

    await query.edit_message_text(msg.SELECT_DATE, reply_markup=dates_keyboard(day_slots, "master_res_date"))


async def _on_master_res_date(query: CallbackQuery, date_str: str) -> None:
    user = query.from_user
    tz = _tz()
    now = datetime.now(tz=tz)

    try:
        selected_date = date.fromisoformat(date_str)
    except ValueError:
        await query.edit_message_text(msg.ERROR_TRY_AGAIN)
        return

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                svc = make_services(session)
                slots_list = await _master_reschedule_flow.on_date_selected(
                    master_id=user.id, selected_date=selected_date, svc=svc, now=now
                )
    except FlowExpiredError:
        await query.edit_message_text(msg.FLOW_EXPIRED)
        return

    if not slots_list:
        await query.edit_message_text(msg.NO_SLOTS_AVAILABLE)
        return

    await query.edit_message_text(
        msg.SELECT_SLOT.format(date=format_date_ru(selected_date)),
        reply_markup=slots_keyboard(slots_list, "master_res_slot"),
    )


async def _on_master_res_slot(query: CallbackQuery, slot_iso: str) -> None:
    user = query.from_user
    tz = _tz()

    try:
        slot_start = datetime.fromisoformat(slot_iso)
    except ValueError:
        await query.edit_message_text(msg.ERROR_TRY_AGAIN)
        return

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                svc = make_services(session)
                await _master_reschedule_flow.on_slot_selected(
                    master_id=user.id, slot_iso=slot_iso, svc=svc
                )
    except FlowExpiredError:
        await query.edit_message_text(msg.FLOW_EXPIRED)
        return

    local = slot_start.astimezone(tz)
    await query.edit_message_text(
        msg.CONFIRM_RESCHEDULE.format(date=format_date_ru(local.date()), time=format_time(local)),
        reply_markup=confirm_keyboard("master_res_confirm"),
    )


async def _on_master_res_confirm(query: CallbackQuery) -> None:
    user = query.from_user
    tz = _tz()
    now = datetime.now(tz=tz)
    result_text: str = msg.ERROR_TRY_AGAIN

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                svc = make_services(session)
                appt = await _master_reschedule_flow.on_confirm(
                    master_id=user.id, svc=svc, now=now
                )
        local = appt.start_at.astimezone(tz)
        async with AsyncSessionLocal() as session:
            async with session.begin():
                svc = make_services(session)
                fresh = await svc.appointment_service.get_by_id(appt.id, load_client=True)
        client_name = "Клиент"
        if fresh is not None and fresh.client is not None:
            client_name = fresh.client.first_name or "Клиент"
        result_text = msg.MASTER_RESCHEDULE_SUCCESS.format(
            name=client_name, date=format_date_ru(local.date()), time=format_time(local)
        )
    except FlowExpiredError:
        result_text = msg.FLOW_EXPIRED
    except NoAppointmentError:
        await _reset_user_state(user.id)
        result_text = msg.ERROR_TRY_AGAIN
    except BookingConflictError:
        await _reset_user_state(user.id)
        result_text = msg.BOOKING_CONFLICT_MSG
    except SlotUnavailableError:
        await _reset_user_state(user.id)
        result_text = msg.SLOT_NO_LONGER_AVAILABLE
    except CalendarSyncError:
        logger.exception("Calendar sync error during master reschedule for user %s", user.id)
        await _reset_user_state(user.id)
        result_text = msg.CALENDAR_ERROR
    except Exception:
        logger.exception("Master reschedule confirm failed for user %s", user.id)
        await _reset_user_state(user.id)
        result_text = msg.ERROR_TRY_AGAIN

    await query.edit_message_text(result_text)


# ── Master cancel flow callbacks ──────────────────────────────────────────────

async def _on_master_cancel_appt(query: CallbackQuery, appt_id: str) -> None:
    user = query.from_user
    tz = _tz()

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                svc = make_services(session)
                appt = await _master_cancel_flow.on_appointment_selected(
                    master_id=user.id, appointment_id=appt_id, svc=svc
                )
        local = appt.start_at.astimezone(tz)
        client_name = appt.client.first_name if appt.client else "Клиент"
        await query.edit_message_text(
            msg.MASTER_CONFIRM_CANCEL.format(
                name=client_name,
                date=format_date_ru(local.date()),
                time=format_time(local),
            ),
            reply_markup=confirm_keyboard("master_cancel_confirm"),
        )
    except FlowExpiredError:
        await query.edit_message_text(msg.FLOW_EXPIRED)


async def _on_master_cancel_confirm(query: CallbackQuery) -> None:
    user = query.from_user
    tz = _tz()
    now = datetime.now(tz=tz)
    result_text: str = msg.ERROR_TRY_AGAIN

    try:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                svc = make_services(session)
                appt = await _master_cancel_flow.on_confirm(
                    master_id=user.id, svc=svc, now=now
                )
        async with AsyncSessionLocal() as session:
            async with session.begin():
                svc = make_services(session)
                fresh = await svc.appointment_service.get_by_id(appt.id, load_client=True)
        client_name = "Клиент"
        if fresh is not None and fresh.client is not None:
            client_name = fresh.client.first_name or "Клиент"
        result_text = msg.MASTER_CANCEL_SUCCESS.format(name=client_name)
    except FlowExpiredError:
        result_text = msg.FLOW_EXPIRED
    except NoAppointmentError:
        await _reset_user_state(user.id)
        result_text = msg.ERROR_TRY_AGAIN
    except CalendarSyncError:
        logger.exception("Calendar sync error during master cancel for user %s", user.id)
        await _reset_user_state(user.id)
        result_text = msg.CALENDAR_ERROR
    except Exception:
        logger.exception("Master cancel confirm failed for user %s", user.id)
        await _reset_user_state(user.id)
        result_text = msg.ERROR_TRY_AGAIN

    await query.edit_message_text(result_text)


# ── Master free slots callback ────────────────────────────────────────────────

async def _on_master_free_date(query: CallbackQuery, date_str: str) -> None:
    tz = _tz()
    now = datetime.now(tz=tz)

    try:
        selected_date = date.fromisoformat(date_str)
    except ValueError:
        await query.edit_message_text(msg.ERROR_TRY_AGAIN)
        return

    async with AsyncSessionLocal() as session:
        async with session.begin():
            svc = make_services(session)
            day_start = datetime(
                selected_date.year, selected_date.month, selected_date.day, tzinfo=tz
            )
            day_end = day_start + timedelta(days=1)
            busy = await svc.calendar.get_busy_intervals(day_start, day_end)
            slots = svc.availability.get_available_slots(selected_date, busy, now)

    if not slots:
        await query.edit_message_text(
            msg.MASTER_FREE_SLOTS_NONE.format(date=format_date_ru(selected_date))
        )
        return

    slots_text = "  ".join(format_time(s.start) for s in slots)
    await query.edit_message_text(
        msg.MASTER_FREE_SLOTS_FOR.format(date=format_date_ru(selected_date), slots=slots_text)
    )
