"""MasterRescheduleFlowUseCase — multi-step flow for rescheduling an appointment.

Flow steps:
    1. on_appointment_selected — master chose an appointment from the list →
                                  fetch date slots, advance to MASTER_RESCHEDULE_SELECT_DATE
    2. on_date_selected        — master picked a date → fetch time slots,
                                  advance to MASTER_RESCHEDULE_SELECT_SLOT
    3. on_slot_selected        — master picked a time → save draft,
                                  advance to MASTER_RESCHEDULE_CONFIRM
    4. on_confirm              — master confirmed → reschedule via service, advance to IDLE
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from app.core import states
from app.core.config import settings
from app.core.exceptions import FlowExpiredError
from app.db.models import Appointment
from app.schemas.availability import DaySlots, TimeSlot
from app.use_cases.deps import HandlerServices


class MasterRescheduleFlowUseCase:
    async def on_appointment_selected(
        self,
        master_id: int,
        appointment_id: str,
        svc: HandlerServices,
        now: datetime,
    ) -> list[DaySlots]:
        """Store the appointment ID and return available dates."""
        bot_session = await svc.session_repo.get_by_telegram_user_id(master_id)
        if bot_session is None or bot_session.current_state != states.MASTER_RESCHEDULE_SELECT_APPT:
            raise FlowExpiredError()

        tz = now.tzinfo
        today = now.date()
        horizon = today + timedelta(days=settings.booking_horizon_days)
        horizon_dt = datetime(horizon.year, horizon.month, horizon.day, 23, 59, 59, tzinfo=tz)
        busy = await svc.calendar.get_busy_intervals(now, horizon_dt)
        day_slots = svc.availability.get_available_slots_for_range(today, horizon, busy, now)

        await svc.session_repo.upsert(
            master_id,
            states.MASTER_RESCHEDULE_SELECT_DATE,
            {"appointment_id": appointment_id},
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
        if bot_session is None or bot_session.current_state != states.MASTER_RESCHEDULE_SELECT_DATE:
            raise FlowExpiredError()

        tz = now.tzinfo
        day_start = datetime(selected_date.year, selected_date.month, selected_date.day, tzinfo=tz)
        day_end = day_start + timedelta(days=1)
        busy = await svc.calendar.get_busy_intervals(day_start, day_end)
        slots = svc.availability.get_available_slots(selected_date, busy, now)

        draft = dict(bot_session.draft_payload or {})
        draft["date"] = selected_date.isoformat()
        await svc.session_repo.upsert(master_id, states.MASTER_RESCHEDULE_SELECT_SLOT, draft)
        return slots

    async def on_slot_selected(
        self,
        master_id: int,
        slot_iso: str,
        svc: HandlerServices,
    ) -> None:
        """Save the chosen slot and advance to confirm state."""
        bot_session = await svc.session_repo.get_by_telegram_user_id(master_id)
        if bot_session is None or bot_session.current_state != states.MASTER_RESCHEDULE_SELECT_SLOT:
            raise FlowExpiredError()

        draft = dict(bot_session.draft_payload or {})
        draft["slot_start"] = slot_iso
        await svc.session_repo.upsert(master_id, states.MASTER_RESCHEDULE_CONFIRM, draft)

    async def on_confirm(
        self,
        master_id: int,
        svc: HandlerServices,
        now: datetime,
    ) -> Appointment:
        """Reschedule the appointment and return to IDLE.

        Returns the updated Appointment.
        Raises FlowExpiredError, NoAppointmentError, BookingConflictError,
               SlotUnavailableError, CalendarSyncError.
        """
        bot_session = await svc.session_repo.get_by_telegram_user_id(master_id)
        if bot_session is None or bot_session.current_state != states.MASTER_RESCHEDULE_CONFIRM:
            raise FlowExpiredError()

        payload = bot_session.draft_payload or {}
        appt_id_str: str | None = payload.get("appointment_id")
        slot_iso: str | None = payload.get("slot_start")
        if not appt_id_str or not slot_iso:
            raise FlowExpiredError()

        appointment_id = uuid.UUID(appt_id_str)
        new_slot_start = datetime.fromisoformat(slot_iso)

        appt = await svc.appointment_service.reschedule_appointment_by_id(
            appointment_id,
            new_slot_start,
            actor_type="master",
            actor_id=f"master:{master_id}",
            now=now,
        )

        await svc.session_repo.upsert(master_id, states.IDLE, {})
        return appt
