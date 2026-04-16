"""AppointmentService — orchestrates the full lifecycle of a haircut booking.

Business rules enforced here:
- One active future appointment per client at most.
- The requested slot must pass all booking rule checks (working day/hours,
  minimum notice, booking horizon).
- No two confirmed appointments may overlap in time.
- Calendar and local DB records are kept in sync; calendar is always mutated
  first so a failure there aborts the operation before touching the DB.
- Every state change is written to the audit log.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

logger = logging.getLogger(__name__)

from app.core.exceptions import (
    BookingConflictError,
    CalendarSyncError,
    NoAppointmentError,
    SlotUnavailableError,
    TooManyAppointmentsError,
)
from app.db.models import Appointment, AppointmentStatus
from app.integrations.google_calendar_mcp.calendar_adapter import CalendarAdapter
from app.repositories.appointment import AppointmentRepository
from app.repositories.audit_log import AuditLogRepository
from app.services.booking_rules_service import BookingRulesService
from app.services.notification_service import NotificationService

_APPOINTMENT_TITLE = "Стрижка"


class AppointmentService:
    def __init__(
        self,
        calendar_adapter: CalendarAdapter,
        rules: BookingRulesService,
        appointments: AppointmentRepository,
        audit: AuditLogRepository,
        notification: NotificationService | None = None,
    ) -> None:
        self._calendar = calendar_adapter
        self._rules = rules
        self._appointments = appointments
        self._audit = audit
        self._notification = notification

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_future_appointment_for_client(
        self,
        client_id: UUID,
        *,
        now: datetime | None = None,
    ) -> Appointment | None:
        now = now or datetime.now(tz=self._rules.timezone)
        return await self._appointments.get_active_by_client_id(client_id, now)

    async def get_active_appointments_in_range(
        self,
        start_at: datetime,
        end_at: datetime,
    ) -> list[Appointment]:
        """Return all non-cancelled appointments overlapping [start_at, end_at)."""
        return await self._appointments.get_active_in_range(start_at, end_at)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def create_booking(
        self,
        client_id: UUID,
        slot_start: datetime,
        actor_id: str = "system",
        *,
        client_label: str | None = None,
        now: datetime | None = None,
    ) -> Appointment:
        now = now or datetime.now(tz=self._rules.timezone)
        slot_end = slot_start + self._rules.slot_duration

        # 1. Enforce one-active-appointment rule
        existing = await self._appointments.get_active_by_client_id(client_id, now)
        if existing is not None:
            raise TooManyAppointmentsError(
                "Client already has an active future appointment."
            )

        # 2. Validate slot against booking rules
        self._validate_slot(slot_start, now)

        # 3. Check DB-level time overlap
        overlapping = await self._appointments.get_overlapping(slot_start, slot_end)
        if overlapping:
            raise BookingConflictError(
                "Requested slot overlaps with an existing appointment."
            )

        # 4. Create calendar event (mutate external state first so we can bail
        #    before writing to the DB if the call fails)
        try:
            event = await self._calendar.create_event(
                start_at=slot_start,
                end_at=slot_end,
                title=_APPOINTMENT_TITLE,
                description=client_label,
            )
        except CalendarSyncError:
            raise
        except Exception as exc:
            raise CalendarSyncError(
                f"Failed to create calendar event: {exc}"
            ) from exc

        # 5. Persist local record
        appointment = await self._appointments.create(
            client_id=client_id,
            google_event_id=event.event_id,
            start_at=slot_start,
            end_at=slot_end,
            status=AppointmentStatus.confirmed,
        )

        # 6. Audit
        await self._audit.create(
            appointment_id=appointment.id,
            actor_type="client",
            actor_id=actor_id,
            action="created",
            payload_json={
                "start_at": slot_start.isoformat(),
                "end_at": slot_end.isoformat(),
            },
        )

        # 7. Notify master (never let notification failures abort the booking)
        if self._notification is not None:
            try:
                await self._notification.notify_booking_created(
                    start_at=slot_start,
                    actor_id=actor_id,
                )
            except Exception:
                logger.exception("Master notification failed for booking created %s", appointment.id)

        return appointment

    async def reschedule_booking(
        self,
        client_id: UUID,
        new_slot_start: datetime,
        actor_id: str = "system",
        *,
        now: datetime | None = None,
    ) -> Appointment:
        now = now or datetime.now(tz=self._rules.timezone)
        new_slot_end = new_slot_start + self._rules.slot_duration

        # 1. Client must have an active appointment to reschedule
        appointment = await self._appointments.get_active_by_client_id(client_id, now)
        if appointment is None:
            raise NoAppointmentError(
                "Client has no active future appointment to reschedule."
            )

        # 2. Validate the new slot
        self._validate_slot(new_slot_start, now)

        # 3. Check overlap, excluding the current appointment
        overlapping = await self._appointments.get_overlapping(
            new_slot_start, new_slot_end, exclude_id=appointment.id
        )
        if overlapping:
            raise BookingConflictError(
                "New slot overlaps with an existing appointment."
            )

        old_start = appointment.start_at

        # 4. Update calendar event
        try:
            await self._calendar.update_event(
                event_id=appointment.google_event_id,
                start_at=new_slot_start,
                end_at=new_slot_end,
            )
        except CalendarSyncError:
            raise
        except Exception as exc:
            raise CalendarSyncError(
                f"Failed to update calendar event: {exc}"
            ) from exc

        # 5. Update local record
        appointment = await self._appointments.update(
            appointment,
            start_at=new_slot_start,
            end_at=new_slot_end,
        )

        # 6. Audit
        await self._audit.create(
            appointment_id=appointment.id,
            actor_type="client",
            actor_id=actor_id,
            action="rescheduled",
            payload_json={
                "old_start_at": old_start.isoformat(),
                "new_start_at": new_slot_start.isoformat(),
                "new_end_at": new_slot_end.isoformat(),
            },
        )

        # 7. Notify master (never let notification failures abort the reschedule)
        if self._notification is not None:
            try:
                await self._notification.notify_booking_rescheduled(
                    old_start_at=old_start,
                    new_start_at=new_slot_start,
                    actor_id=actor_id,
                )
            except Exception:
                logger.exception("Master notification failed for reschedule %s", appointment.id)

        return appointment

    async def cancel_booking(
        self,
        client_id: UUID,
        reason: str | None = None,
        actor_id: str = "system",
        *,
        now: datetime | None = None,
    ) -> Appointment:
        now = now or datetime.now(tz=self._rules.timezone)

        # 1. Client must have an active appointment to cancel
        appointment = await self._appointments.get_active_by_client_id(client_id, now)
        if appointment is None:
            raise NoAppointmentError(
                "Client has no active future appointment to cancel."
            )

        # 2. Remove from calendar.
        # If the event is already gone (e.g. deleted manually), treat as success —
        # the cancellation goal (event absent) is already achieved.
        try:
            await self._calendar.delete_event(appointment.google_event_id)
        except CalendarSyncError:
            logger.warning(
                "Calendar event %s could not be deleted during cancellation "
                "(may have been removed manually); proceeding with local cancel.",
                appointment.google_event_id,
            )
        except Exception as exc:
            raise CalendarSyncError(
                f"Failed to delete calendar event: {exc}"
            ) from exc

        # 3. Mark local record as cancelled
        appointment = await self._appointments.update(
            appointment,
            status=AppointmentStatus.cancelled,
            cancelled_at=now,
            cancel_reason=reason,
        )

        # 4. Audit
        await self._audit.create(
            appointment_id=appointment.id,
            actor_type="client",
            actor_id=actor_id,
            action="cancelled",
            payload_json={"reason": reason},
        )

        # 5. Notify master (never let notification failures abort the cancellation)
        if self._notification is not None:
            try:
                await self._notification.notify_booking_cancelled(
                    start_at=appointment.start_at,
                    actor_id=actor_id,
                )
            except Exception:
                logger.exception("Master notification failed for cancellation %s", appointment.id)

        return appointment

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _validate_slot(self, slot_start: datetime, now: datetime) -> None:
        if not self._rules.is_working_day(slot_start.date()):
            raise SlotUnavailableError("Slot is on a non-working day.")
        if not self._rules.is_within_working_hours(slot_start):
            raise SlotUnavailableError("Slot is outside working hours.")
        if not self._rules.satisfies_min_notice(slot_start, now):
            raise SlotUnavailableError(
                "Slot does not satisfy the minimum notice requirement."
            )
        if not self._rules.is_within_booking_horizon(slot_start, now):
            raise SlotUnavailableError("Slot is beyond the booking horizon.")
