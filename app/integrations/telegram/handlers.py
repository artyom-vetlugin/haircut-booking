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

from telegram import CallbackQuery, Update
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
    confirm_keyboard,
    dates_keyboard,
    format_date_ru,
    format_time,
    main_menu_keyboard,
    slots_keyboard,
)
from app.repositories.bot_session import BotSessionRepository
from app.use_cases.booking_flow import BookingFlowUseCase
from app.use_cases.cancel_flow import CancelFlowUseCase
from app.use_cases.deps import HandlerServices, make_services
from app.use_cases.handle_free_text_message import HandleFreeTextMessageUseCase
from app.use_cases.reschedule_flow import RescheduleFlowUseCase
from app.use_cases.start import StartUseCase

logger = logging.getLogger(__name__)

_start_use_case = StartUseCase()
_free_text_use_case = HandleFreeTextMessageUseCase()
_booking_flow = BookingFlowUseCase()
_reschedule_flow = RescheduleFlowUseCase()
_cancel_flow = CancelFlowUseCase()


# ── Helpers ───────────────────────────────────────────────────────────────────

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


async def handle_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return
    user = update.effective_user
    text = update.message.text or ""
    if not text.strip():
        await update.message.reply_text(msg.UNKNOWN_INPUT, reply_markup=main_menu_keyboard())
        return

    reply: str
    async with AsyncSessionLocal() as session:
        async with session.begin():
            svc = make_services(session)
            reply = await _free_text_use_case.execute(user.id, text, svc)

    await update.message.reply_text(reply, reply_markup=main_menu_keyboard())


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
    await query.edit_message_text(msg.MAIN_MENU_PROMPT)
