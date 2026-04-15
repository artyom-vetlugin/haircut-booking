"""BookingFlowUseCase — orchestrates the multi-step appointment booking flow.

Flow steps:
    1. on_date_selected  — user picked a date → fetch slots, advance to BOOKING_SELECT_SLOT
    2. on_slot_selected  — user picked a time → save draft, advance to BOOKING_CONFIRM
    3. on_confirm        — user confirmed     → create appointment, advance to IDLE
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from app.core import states
from app.core.exceptions import FlowExpiredError
from app.db.models import Appointment
from app.schemas.availability import TimeSlot
from app.use_cases.deps import HandlerServices, get_or_create_client


class BookingFlowUseCase:
    async def on_date_selected(
        self,
        user_id: int,
        selected_date: date,
        svc: HandlerServices,
        now: datetime,
    ) -> list[TimeSlot]:
        """Validate BOOKING_SELECT_DATE state, fetch slots, advance to BOOKING_SELECT_SLOT.

        Returns the list of available slots for the selected date.
        Raises FlowExpiredError if the current state does not match.
        """
        bot_session = await svc.session_repo.get_by_telegram_user_id(user_id)
        if bot_session is None or bot_session.current_state != states.BOOKING_SELECT_DATE:
            raise FlowExpiredError()

        tz = now.tzinfo
        day_start = datetime(selected_date.year, selected_date.month, selected_date.day, tzinfo=tz)
        day_end = day_start + timedelta(days=1)
        busy = await svc.calendar.get_busy_intervals(day_start, day_end)
        slots = svc.availability.get_available_slots(selected_date, busy, now)
        await svc.session_repo.upsert(
            user_id, states.BOOKING_SELECT_SLOT, {"date": selected_date.isoformat()}
        )
        return slots

    async def on_slot_selected(
        self,
        user_id: int,
        slot_iso: str,
        svc: HandlerServices,
    ) -> None:
        """Validate BOOKING_SELECT_SLOT state, save slot choice, advance to BOOKING_CONFIRM.

        Raises FlowExpiredError if the current state does not match.
        """
        bot_session = await svc.session_repo.get_by_telegram_user_id(user_id)
        if bot_session is None or bot_session.current_state != states.BOOKING_SELECT_SLOT:
            raise FlowExpiredError()
        await svc.session_repo.upsert(user_id, states.BOOKING_CONFIRM, {"slot_start": slot_iso})

    async def on_confirm(
        self,
        user_id: int,
        first_name: str | None,
        last_name: str | None,
        username: str | None,
        svc: HandlerServices,
        now: datetime,
    ) -> Appointment:
        """Validate BOOKING_CONFIRM state, create booking, advance to IDLE.

        Returns the created appointment.
        Raises FlowExpiredError, TooManyAppointmentsError, BookingConflictError,
               SlotUnavailableError, CalendarSyncError.
        """
        bot_session = await svc.session_repo.get_by_telegram_user_id(user_id)
        if bot_session is None or bot_session.current_state != states.BOOKING_CONFIRM:
            raise FlowExpiredError()

        slot_iso = (bot_session.draft_payload or {}).get("slot_start")
        if not slot_iso:
            raise FlowExpiredError()

        slot_start = datetime.fromisoformat(slot_iso)
        client = await get_or_create_client(svc, user_id, first_name, last_name, username)
        appt = await svc.appointment_service.create_booking(
            client.id, slot_start, actor_id=str(user_id), now=now
        )
        await svc.session_repo.upsert(user_id, states.IDLE, {})
        return appt
