"""Unit tests for AppointmentService.

All external dependencies (DB repositories, calendar) are replaced with
lightweight fakes or mocks — no database is required to run these tests.

Fixed datetimes are used throughout so results are deterministic:
  FIXED_NOW   = 2026-04-20 08:00 Asia/Almaty  (Monday, working day)
  VALID_SLOT  = 2026-04-21 10:00 Asia/Almaty  (Tuesday 10:00 — satisfies all rules)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest

from app.core.config import Settings
from app.core.exceptions import (
    BookingConflictError,
    CalendarSyncError,
    NoAppointmentError,
    SlotUnavailableError,
    TooManyAppointmentsError,
)
from app.db.models import Appointment, AppointmentStatus
from app.integrations.google_calendar_mcp.calendar_adapter import (
    CalendarAdapter,
    CalendarEvent,
)
from app.schemas.availability import BusyInterval
from app.services.appointment_service import AppointmentService
from app.services.booking_rules_service import BookingRulesService

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TZ = ZoneInfo("Asia/Almaty")
FIXED_NOW = datetime(2026, 4, 20, 8, 0, tzinfo=TZ)   # Monday 08:00
VALID_SLOT = datetime(2026, 4, 21, 10, 0, tzinfo=TZ)  # Tuesday 10:00

CLIENT_ID = uuid.uuid4()
ACTOR_ID = "999"

# ---------------------------------------------------------------------------
# Fake calendar adapter
# ---------------------------------------------------------------------------


class FakeCalendarAdapter(CalendarAdapter):
    """In-memory implementation of CalendarAdapter for testing."""

    def __init__(self) -> None:
        self.events: dict[str, CalendarEvent] = {}

    async def create_event(
        self,
        start_at: datetime,
        end_at: datetime,
        title: str,
        description: str | None = None,
    ) -> CalendarEvent:
        event_id = str(uuid.uuid4())
        event = CalendarEvent(
            event_id=event_id,
            start_at=start_at,
            end_at=end_at,
            title=title,
            description=description,
        )
        self.events[event_id] = event
        return event

    async def update_event(
        self,
        event_id: str,
        start_at: datetime,
        end_at: datetime,
        title: str | None = None,
        description: str | None = None,
    ) -> CalendarEvent:
        existing = self.events[event_id]
        updated = CalendarEvent(
            event_id=event_id,
            start_at=start_at,
            end_at=end_at,
            title=title if title is not None else existing.title,
            description=description if description is not None else existing.description,
        )
        self.events[event_id] = updated
        return updated

    async def delete_event(self, event_id: str) -> None:
        self.events.pop(event_id, None)

    async def list_events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        return [
            e for e in self.events.values()
            if e.start_at < end and e.end_at > start
        ]

    async def get_busy_intervals(
        self, start: datetime, end: datetime
    ) -> list[BusyInterval]:
        return [
            BusyInterval(start=e.start_at, end=e.end_at)
            for e in self.events.values()
            if e.start_at < end and e.end_at > start
        ]


class FailingCalendarAdapter(CalendarAdapter):
    """Calendar adapter that always raises a generic exception."""

    async def list_events(self, start_at, end_at):
        raise RuntimeError("calendar unavailable")

    async def create_event(self, start_at, end_at, title, description=None):
        raise RuntimeError("calendar unavailable")

    async def update_event(self, event_id, start_at, end_at, title=None, description=None):
        raise RuntimeError("calendar unavailable")

    async def delete_event(self, event_id):
        raise RuntimeError("calendar unavailable")

    async def get_busy_intervals(self, start, end):
        raise RuntimeError("calendar unavailable")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://test:test@localhost/test",
        telegram_bot_token="0:test",
        telegram_master_chat_id=12345,
    )


def _make_rules() -> BookingRulesService:
    return BookingRulesService(_make_settings())


def _make_appointment(
    client_id: uuid.UUID = CLIENT_ID,
    google_event_id: str = "evt-123",
    start_at: datetime = VALID_SLOT,
    status: AppointmentStatus = AppointmentStatus.confirmed,
) -> Appointment:
    from datetime import timedelta

    appt = MagicMock(spec=Appointment)
    appt.id = uuid.uuid4()
    appt.client_id = client_id
    appt.google_event_id = google_event_id
    appt.start_at = start_at
    appt.end_at = start_at + timedelta(hours=1)
    appt.status = status
    return appt


def _make_service(
    calendar: CalendarAdapter | None = None,
    active_appointment: Appointment | None = None,
    overlapping: list[Appointment] | None = None,
    notification: object | None = None,
) -> tuple[AppointmentService, MagicMock, MagicMock]:
    """Return (service, appointments_repo_mock, audit_repo_mock)."""
    cal = calendar or FakeCalendarAdapter()
    rules = _make_rules()

    appointments = MagicMock()
    appointments.get_active_by_client_id = AsyncMock(return_value=active_appointment)
    appointments.get_overlapping = AsyncMock(return_value=overlapping or [])
    appointments.create = AsyncMock(return_value=_make_appointment())
    appointments.update = AsyncMock(side_effect=lambda appt, **kw: appt)

    audit = MagicMock()
    audit.create = AsyncMock(return_value=None)

    service = AppointmentService(
        calendar_adapter=cal,
        rules=rules,
        appointments=appointments,
        audit=audit,
        notification=notification,  # type: ignore[arg-type]
    )
    return service, appointments, audit


def _make_notification_mock() -> MagicMock:
    n = MagicMock()
    n.notify_booking_created = AsyncMock()
    n.notify_booking_rescheduled = AsyncMock()
    n.notify_booking_cancelled = AsyncMock()
    return n


# ---------------------------------------------------------------------------
# get_future_appointment_for_client
# ---------------------------------------------------------------------------


class TestGetFutureAppointment:
    @pytest.mark.asyncio
    async def test_returns_appointment_when_exists(self):
        appt = _make_appointment()
        service, repo, _ = _make_service(active_appointment=appt)

        result = await service.get_future_appointment_for_client(
            CLIENT_ID, now=FIXED_NOW
        )

        assert result is appt
        repo.get_active_by_client_id.assert_awaited_once_with(CLIENT_ID, FIXED_NOW)

    @pytest.mark.asyncio
    async def test_returns_none_when_no_appointment(self):
        service, _, _ = _make_service(active_appointment=None)

        result = await service.get_future_appointment_for_client(
            CLIENT_ID, now=FIXED_NOW
        )

        assert result is None


# ---------------------------------------------------------------------------
# create_booking
# ---------------------------------------------------------------------------


class TestCreateBooking:
    @pytest.mark.asyncio
    async def test_happy_path_creates_appointment_and_calendar_event(self):
        cal = FakeCalendarAdapter()
        service, repo, audit = _make_service(calendar=cal)

        result = await service.create_booking(
            CLIENT_ID, VALID_SLOT, ACTOR_ID, now=FIXED_NOW
        )

        assert result is not None
        # Calendar event was created
        assert len(cal.events) == 1
        # Appointment repo.create called
        repo.create.assert_awaited_once()
        # Audit log written
        audit.create.assert_awaited_once()
        create_kwargs = repo.create.call_args.kwargs
        assert create_kwargs["status"] == AppointmentStatus.confirmed
        assert create_kwargs["start_at"] == VALID_SLOT

    @pytest.mark.asyncio
    async def test_raises_if_client_already_has_active_appointment(self):
        existing = _make_appointment()
        service, repo, _ = _make_service(active_appointment=existing)

        with pytest.raises(TooManyAppointmentsError):
            await service.create_booking(CLIENT_ID, VALID_SLOT, now=FIXED_NOW)

        repo.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_raises_on_non_working_day(self):
        # Sunday = weekday 6, not in [0..5]
        sunday_slot = datetime(2026, 4, 19, 10, 0, tzinfo=TZ)
        service, repo, _ = _make_service()

        with pytest.raises(SlotUnavailableError):
            await service.create_booking(CLIENT_ID, sunday_slot, now=FIXED_NOW)

        repo.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_raises_outside_working_hours(self):
        # 20:00 is past working_hours_end (19)
        after_hours = datetime(2026, 4, 21, 20, 0, tzinfo=TZ)
        service, repo, _ = _make_service()

        with pytest.raises(SlotUnavailableError):
            await service.create_booking(CLIENT_ID, after_hours, now=FIXED_NOW)

        repo.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_raises_when_min_notice_not_satisfied(self):
        # Slot only 1 hour away (min_notice = 2)
        from datetime import timedelta

        too_soon = FIXED_NOW + timedelta(hours=1)
        service, repo, _ = _make_service()

        with pytest.raises(SlotUnavailableError):
            await service.create_booking(CLIENT_ID, too_soon, now=FIXED_NOW)

        repo.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_raises_when_beyond_booking_horizon(self):
        from datetime import timedelta

        beyond = FIXED_NOW + timedelta(days=31)
        service, repo, _ = _make_service()

        with pytest.raises(SlotUnavailableError):
            await service.create_booking(CLIENT_ID, beyond, now=FIXED_NOW)

        repo.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_raises_on_overlapping_appointment(self):
        overlap = _make_appointment(start_at=VALID_SLOT)
        service, repo, _ = _make_service(overlapping=[overlap])

        with pytest.raises(BookingConflictError):
            await service.create_booking(CLIENT_ID, VALID_SLOT, now=FIXED_NOW)

        repo.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_raises_calendar_sync_error_on_calendar_failure(self):
        service, repo, _ = _make_service(calendar=FailingCalendarAdapter())

        with pytest.raises(CalendarSyncError):
            await service.create_booking(CLIENT_ID, VALID_SLOT, now=FIXED_NOW)

        repo.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_google_event_id_is_persisted(self):
        cal = FakeCalendarAdapter()
        service, repo, _ = _make_service(calendar=cal)

        await service.create_booking(CLIENT_ID, VALID_SLOT, now=FIXED_NOW)

        create_kwargs = repo.create.call_args.kwargs
        created_event_id = list(cal.events.keys())[0]
        assert create_kwargs["google_event_id"] == created_event_id


# ---------------------------------------------------------------------------
# reschedule_booking
# ---------------------------------------------------------------------------


class TestRescheduleBooking:
    def _appt_with_event(self, cal: FakeCalendarAdapter) -> Appointment:
        """Create a real calendar event and return a matching Appointment mock."""
        import asyncio

        event = asyncio.get_event_loop().run_until_complete(
            cal.create_event(VALID_SLOT, VALID_SLOT, "Стрижка")
        )
        return _make_appointment(google_event_id=event.event_id)

    @pytest.mark.asyncio
    async def test_happy_path_updates_appointment_and_calendar_event(self):
        from datetime import timedelta

        cal = FakeCalendarAdapter()
        event = await cal.create_event(VALID_SLOT, VALID_SLOT + timedelta(hours=1), "Стрижка")
        appt = _make_appointment(google_event_id=event.event_id)

        new_slot = datetime(2026, 4, 22, 11, 0, tzinfo=TZ)  # Wednesday 11:00

        service, repo, audit = _make_service(
            calendar=cal, active_appointment=appt
        )

        result = await service.reschedule_booking(
            CLIENT_ID, new_slot, ACTOR_ID, now=FIXED_NOW
        )

        assert result is appt
        assert cal.events[event.event_id].start_at == new_slot
        repo.update.assert_awaited_once()
        audit.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_raises_when_no_active_appointment(self):
        service, repo, _ = _make_service(active_appointment=None)
        new_slot = datetime(2026, 4, 22, 11, 0, tzinfo=TZ)

        with pytest.raises(NoAppointmentError):
            await service.reschedule_booking(CLIENT_ID, new_slot, now=FIXED_NOW)

        repo.update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_raises_on_invalid_new_slot(self):
        appt = _make_appointment()
        sunday_slot = datetime(2026, 4, 19, 10, 0, tzinfo=TZ)
        service, repo, _ = _make_service(active_appointment=appt)

        with pytest.raises(SlotUnavailableError):
            await service.reschedule_booking(CLIENT_ID, sunday_slot, now=FIXED_NOW)

        repo.update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_raises_when_new_slot_overlaps_another_appointment(self):
        appt = _make_appointment()
        other = _make_appointment(google_event_id="other-evt")
        service, repo, _ = _make_service(
            active_appointment=appt, overlapping=[other]
        )
        new_slot = datetime(2026, 4, 22, 11, 0, tzinfo=TZ)

        with pytest.raises(BookingConflictError):
            await service.reschedule_booking(CLIENT_ID, new_slot, now=FIXED_NOW)

        repo.update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_raises_calendar_sync_error_on_calendar_failure(self):
        appt = _make_appointment()
        service, repo, _ = _make_service(
            calendar=FailingCalendarAdapter(), active_appointment=appt
        )
        new_slot = datetime(2026, 4, 22, 11, 0, tzinfo=TZ)

        with pytest.raises(CalendarSyncError):
            await service.reschedule_booking(CLIENT_ID, new_slot, now=FIXED_NOW)

        repo.update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_audit_log_includes_old_and_new_start(self):
        from datetime import timedelta

        cal = FakeCalendarAdapter()
        event = await cal.create_event(VALID_SLOT, VALID_SLOT + timedelta(hours=1), "Стрижка")
        appt = _make_appointment(google_event_id=event.event_id)

        new_slot = datetime(2026, 4, 22, 11, 0, tzinfo=TZ)

        service, _, audit = _make_service(calendar=cal, active_appointment=appt)
        await service.reschedule_booking(CLIENT_ID, new_slot, now=FIXED_NOW)

        payload = audit.create.call_args.kwargs["payload_json"]
        assert "old_start_at" in payload
        assert "new_start_at" in payload


# ---------------------------------------------------------------------------
# cancel_booking
# ---------------------------------------------------------------------------


class TestCancelBooking:
    @pytest.mark.asyncio
    async def test_happy_path_cancels_appointment_and_removes_calendar_event(self):
        from datetime import timedelta

        cal = FakeCalendarAdapter()
        event = await cal.create_event(VALID_SLOT, VALID_SLOT + timedelta(hours=1), "Стрижка")
        appt = _make_appointment(google_event_id=event.event_id)

        service, repo, audit = _make_service(calendar=cal, active_appointment=appt)

        result = await service.cancel_booking(
            CLIENT_ID, reason="change of plans", actor_id=ACTOR_ID, now=FIXED_NOW
        )

        assert result is appt
        assert event.event_id not in cal.events
        update_kwargs = repo.update.call_args.kwargs
        assert update_kwargs["status"] == AppointmentStatus.cancelled
        assert update_kwargs["cancel_reason"] == "change of plans"
        audit.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_raises_when_no_active_appointment(self):
        service, repo, _ = _make_service(active_appointment=None)

        with pytest.raises(NoAppointmentError):
            await service.cancel_booking(CLIENT_ID, now=FIXED_NOW)

        repo.update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_raises_calendar_sync_error_on_calendar_failure(self):
        appt = _make_appointment()
        service, repo, _ = _make_service(
            calendar=FailingCalendarAdapter(), active_appointment=appt
        )

        with pytest.raises(CalendarSyncError):
            await service.cancel_booking(CLIENT_ID, now=FIXED_NOW)

        repo.update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cancel_succeeds_when_calendar_event_already_deleted(self):
        """If the calendar event was removed manually, cancel must still succeed locally."""

        class EventAlreadyGoneAdapter(CalendarAdapter):
            async def delete_event(self, event_id):
                raise CalendarSyncError("Event not found")

            async def list_events(self, start, end):
                return []

            async def create_event(self, start_at, end_at, title, description=None):
                raise NotImplementedError

            async def update_event(self, event_id, start_at, end_at, title=None, description=None):
                raise NotImplementedError

            async def get_busy_intervals(self, start, end):
                return []

        appt = _make_appointment()
        service, repo, audit = _make_service(
            calendar=EventAlreadyGoneAdapter(), active_appointment=appt
        )

        result = await service.cancel_booking(CLIENT_ID, now=FIXED_NOW)

        assert result is appt
        update_kwargs = repo.update.call_args.kwargs
        assert update_kwargs["status"] == AppointmentStatus.cancelled
        audit.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_cancelled_at_is_set_to_now(self):
        from datetime import timedelta

        cal = FakeCalendarAdapter()
        event = await cal.create_event(VALID_SLOT, VALID_SLOT + timedelta(hours=1), "Стрижка")
        appt = _make_appointment(google_event_id=event.event_id)

        service, repo, _ = _make_service(calendar=cal, active_appointment=appt)
        await service.cancel_booking(CLIENT_ID, now=FIXED_NOW)

        update_kwargs = repo.update.call_args.kwargs
        assert update_kwargs["cancelled_at"] == FIXED_NOW

    @pytest.mark.asyncio
    async def test_cancel_without_reason(self):
        from datetime import timedelta

        cal = FakeCalendarAdapter()
        event = await cal.create_event(VALID_SLOT, VALID_SLOT + timedelta(hours=1), "Стрижка")
        appt = _make_appointment(google_event_id=event.event_id)

        service, repo, _ = _make_service(calendar=cal, active_appointment=appt)
        await service.cancel_booking(CLIENT_ID, now=FIXED_NOW)

        update_kwargs = repo.update.call_args.kwargs
        assert update_kwargs["cancel_reason"] is None


# ---------------------------------------------------------------------------
# Notification integration
# ---------------------------------------------------------------------------


class TestNotifications:
    @pytest.mark.asyncio
    async def test_create_booking_calls_notify(self):
        notif = _make_notification_mock()
        service, _, _ = _make_service(notification=notif)

        await service.create_booking(CLIENT_ID, VALID_SLOT, ACTOR_ID, now=FIXED_NOW)

        notif.notify_booking_created.assert_awaited_once()
        call_kwargs = notif.notify_booking_created.call_args.kwargs
        assert call_kwargs["start_at"] == VALID_SLOT
        assert call_kwargs["actor_id"] == ACTOR_ID

    @pytest.mark.asyncio
    async def test_reschedule_booking_calls_notify(self):
        from datetime import timedelta

        cal = FakeCalendarAdapter()
        event = await cal.create_event(VALID_SLOT, VALID_SLOT + timedelta(hours=1), "Стрижка")
        appt = _make_appointment(google_event_id=event.event_id)
        new_slot = datetime(2026, 4, 22, 11, 0, tzinfo=TZ)

        notif = _make_notification_mock()
        service, _, _ = _make_service(calendar=cal, active_appointment=appt, notification=notif)

        await service.reschedule_booking(CLIENT_ID, new_slot, ACTOR_ID, now=FIXED_NOW)

        notif.notify_booking_rescheduled.assert_awaited_once()
        call_kwargs = notif.notify_booking_rescheduled.call_args.kwargs
        assert call_kwargs["old_start_at"] == VALID_SLOT
        assert call_kwargs["new_start_at"] == new_slot
        assert call_kwargs["actor_id"] == ACTOR_ID

    @pytest.mark.asyncio
    async def test_cancel_booking_calls_notify(self):
        from datetime import timedelta

        cal = FakeCalendarAdapter()
        event = await cal.create_event(VALID_SLOT, VALID_SLOT + timedelta(hours=1), "Стрижка")
        appt = _make_appointment(google_event_id=event.event_id)

        notif = _make_notification_mock()
        service, _, _ = _make_service(calendar=cal, active_appointment=appt, notification=notif)

        await service.cancel_booking(CLIENT_ID, actor_id=ACTOR_ID, now=FIXED_NOW)

        notif.notify_booking_cancelled.assert_awaited_once()
        call_kwargs = notif.notify_booking_cancelled.call_args.kwargs
        assert call_kwargs["start_at"] == VALID_SLOT
        assert call_kwargs["actor_id"] == ACTOR_ID

    @pytest.mark.asyncio
    async def test_no_notification_when_service_is_none(self):
        """AppointmentService works fine without a notification service."""
        service, _, _ = _make_service(notification=None)

        # Must not raise
        result = await service.create_booking(CLIENT_ID, VALID_SLOT, ACTOR_ID, now=FIXED_NOW)
        assert result is not None

    @pytest.mark.asyncio
    async def test_notification_failure_does_not_abort_booking(self):
        """A broken notification service must not propagate exceptions."""
        notif = _make_notification_mock()
        notif.notify_booking_created.side_effect = RuntimeError("telegram down")

        service, repo, _ = _make_service(notification=notif)

        # Must not raise even though notification fails
        result = await service.create_booking(CLIENT_ID, VALID_SLOT, ACTOR_ID, now=FIXED_NOW)

        # Booking was still persisted
        repo.create.assert_awaited_once()
        assert result is not None
