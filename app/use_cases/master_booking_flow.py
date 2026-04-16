"""MasterBookingFlowUseCase — multi-step flow for booking a walk-in client.

Flow steps:
    1. on_name_entered   — master typed client name → fetch date slots,
                           advance to MASTER_BOOKING_SELECT_DATE
    2. on_date_selected  — master picked a date → fetch time slots,
                           advance to MASTER_BOOKING_SELECT_SLOT
    3. on_slot_selected  — master picked a time → save draft,
                           advance to MASTER_BOOKING_CONFIRM
    4. on_confirm        — master confirmed → create walk-in Client + Appointment,
                           advance to IDLE
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from app.core import states
from app.core.config import settings
from app.core.exceptions import FlowExpiredError
from app.db.models import Appointment
from app.schemas.availability import DaySlots, TimeSlot
from app.use_cases.deps import HandlerServices


class MasterBookingFlowUseCase:
    async def on_name_entered(
        self,
        master_id: int,
        name: str,
        svc: HandlerServices,
        now: datetime,
    ) -> list[DaySlots]:
        """Store the client name and return available dates in the booking horizon."""
        bot_session = await svc.session_repo.get_by_telegram_user_id(master_id)
        if bot_session is None or bot_session.current_state != states.MASTER_BOOKING_ENTER_NAME:
            raise FlowExpiredError()

        tz = now.tzinfo
        today = now.date()
        horizon = today + timedelta(days=settings.booking_horizon_days)
        horizon_dt = datetime(horizon.year, horizon.month, horizon.day, 23, 59, 59, tzinfo=tz)
        busy = await svc.calendar.get_busy_intervals(now, horizon_dt)
        day_slots = svc.availability.get_available_slots_for_range(today, horizon, busy, now)

        await svc.session_repo.upsert(
            master_id,
            states.MASTER_BOOKING_SELECT_DATE,
            {"client_name": name.strip()},
        )
        return day_slots

    async def on_date_selected(
        self,
        master_id: int,
        selected_date: date,
        svc: HandlerServices,
        now: datetime,
    ) -> list[TimeSlot]:
        """Fetch slots for the selected date and advance state."""
        bot_session = await svc.session_repo.get_by_telegram_user_id(master_id)
        if bot_session is None or bot_session.current_state != states.MASTER_BOOKING_SELECT_DATE:
            raise FlowExpiredError()

        tz = now.tzinfo
        day_start = datetime(selected_date.year, selected_date.month, selected_date.day, tzinfo=tz)
        day_end = day_start + timedelta(days=1)
        busy = await svc.calendar.get_busy_intervals(day_start, day_end)
        slots = svc.availability.get_available_slots(selected_date, busy, now)

        draft = dict(bot_session.draft_payload or {})
        draft["date"] = selected_date.isoformat()
        await svc.session_repo.upsert(master_id, states.MASTER_BOOKING_SELECT_SLOT, draft)
        return slots

    async def on_slot_selected(
        self,
        master_id: int,
        slot_iso: str,
        svc: HandlerServices,
    ) -> str:
        """Save the chosen slot, advance to confirm state, and return the client name."""
        bot_session = await svc.session_repo.get_by_telegram_user_id(master_id)
        if bot_session is None or bot_session.current_state != states.MASTER_BOOKING_SELECT_SLOT:
            raise FlowExpiredError()

        draft = dict(bot_session.draft_payload or {})
        draft["slot_start"] = slot_iso
        await svc.session_repo.upsert(master_id, states.MASTER_BOOKING_CONFIRM, draft)
        return draft.get("client_name", "")

    async def on_confirm(
        self,
        master_id: int,
        svc: HandlerServices,
        now: datetime,
    ) -> Appointment:
        """Create a walk-in Client + Appointment and return to IDLE.

        Returns the created Appointment.
        Raises FlowExpiredError, BookingConflictError, SlotUnavailableError, CalendarSyncError.
        """
        bot_session = await svc.session_repo.get_by_telegram_user_id(master_id)
        if bot_session is None or bot_session.current_state != states.MASTER_BOOKING_CONFIRM:
            raise FlowExpiredError()

        payload = bot_session.draft_payload or {}
        client_name: str = payload.get("client_name", "").strip()
        slot_iso: str | None = payload.get("slot_start")
        if not client_name or not slot_iso:
            raise FlowExpiredError()

        slot_start = datetime.fromisoformat(slot_iso)

        # Create a walk-in client (no telegram_user_id)
        client = await svc.client_repo.create(first_name=client_name)

        appt = await svc.appointment_service.create_booking(
            client.id,
            slot_start,
            actor_id=f"master:{master_id}",
            client_label=client_name,
            now=now,
        )

        await svc.session_repo.upsert(master_id, states.IDLE, {})
        return appt
