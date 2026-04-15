"""
BookingRulesService — stateless validator for configurable booking rules.

All rule checks are pure functions of the inputs and settings; no I/O is
performed here. External callers (AvailabilityService, booking flows) use
this service to decide whether a candidate slot is acceptable.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.config import Settings


class BookingRulesService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._tz = ZoneInfo(settings.app_timezone)
        self._duration = timedelta(minutes=settings.appointment_duration_minutes)

    # ------------------------------------------------------------------
    # Public helpers consumed by AvailabilityService and booking flows
    # ------------------------------------------------------------------

    @property
    def timezone(self) -> ZoneInfo:
        return self._tz

    @property
    def slot_duration(self) -> timedelta:
        return self._duration

    def is_working_day(self, d: date) -> bool:
        """Return True if *d* falls on a configured working day (0=Mon, 6=Sun)."""
        return d.weekday() in self._settings.working_days

    def is_within_working_hours(self, slot_start: datetime) -> bool:
        """Return True if the slot fits entirely within configured working hours.

        Useful for validating externally-supplied slot times. The slot is
        assumed to have the standard appointment duration.
        """
        local = slot_start.astimezone(self._tz)
        slot_end = local + self._duration
        day_start = local.replace(
            hour=self._settings.working_hours_start,
            minute=0,
            second=0,
            microsecond=0,
        )
        day_end = local.replace(
            hour=self._settings.working_hours_end,
            minute=0,
            second=0,
            microsecond=0,
        )
        return local >= day_start and slot_end <= day_end

    def satisfies_min_notice(self, slot_start: datetime, now: datetime) -> bool:
        """Return True if slot starts at least *min_notice_hours* after *now*."""
        return slot_start >= now + timedelta(hours=self._settings.min_notice_hours)

    def is_within_booking_horizon(self, slot_start: datetime, now: datetime) -> bool:
        """Return True if slot falls within the booking horizon from *now*."""
        horizon_end = now + timedelta(days=self._settings.booking_horizon_days)
        return slot_start < horizon_end
