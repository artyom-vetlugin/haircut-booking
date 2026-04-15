"""
AvailabilityService — generates and filters available appointment slots.

Calendar busy intervals are accepted as an abstract input (list[BusyInterval])
so this service has no dependency on any specific calendar backend. The caller
is responsible for fetching busy intervals from Google Calendar, the local DB,
or any other source and passing them in.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from app.core.config import Settings
from app.schemas.availability import BusyInterval, DaySlots, TimeSlot
from app.services.booking_rules_service import BookingRulesService


class AvailabilityService:
    def __init__(self, rules: BookingRulesService, settings: Settings) -> None:
        self._rules = rules
        self._settings = settings

    # ------------------------------------------------------------------
    # Slot generation
    # ------------------------------------------------------------------

    def generate_candidate_slots(self, d: date) -> list[TimeSlot]:
        """Generate all 1-hour slots within working hours for *d*.

        Slots are produced at exact hour boundaries starting at
        ``working_hours_start`` and stop when the next slot would extend past
        ``working_hours_end``. Working-day membership is NOT checked here;
        callers decide whether to call this for non-working days.
        """
        tz = self._rules.timezone
        duration = self._rules.slot_duration
        day_end = datetime(
            d.year, d.month, d.day,
            self._settings.working_hours_end, 0, 0,
            tzinfo=tz,
        )
        current = datetime(
            d.year, d.month, d.day,
            self._settings.working_hours_start, 0, 0,
            tzinfo=tz,
        )
        slots: list[TimeSlot] = []
        while current + duration <= day_end:
            slots.append(TimeSlot(start=current, end=current + duration))
            current += duration
        return slots

    # ------------------------------------------------------------------
    # Overlap detection (static — no state required)
    # ------------------------------------------------------------------

    @staticmethod
    def slot_overlaps_busy(slot: TimeSlot, busy: BusyInterval) -> bool:
        """Return True if *slot* overlaps with *busy*.

        Adjacent intervals (slot.end == busy.start or vice-versa) do NOT
        overlap; a booking ending exactly when the next busy period begins is
        allowed.
        """
        return slot.start < busy.end and slot.end > busy.start

    def _overlaps_any(self, slot: TimeSlot, busy_intervals: list[BusyInterval]) -> bool:
        return any(self.slot_overlaps_busy(slot, b) for b in busy_intervals)

    # ------------------------------------------------------------------
    # Public availability queries
    # ------------------------------------------------------------------

    def get_available_slots(
        self,
        d: date,
        busy_intervals: list[BusyInterval],
        now: Optional[datetime] = None,
    ) -> list[TimeSlot]:
        """Return slots available on *d* after applying all booking rules.

        Filters applied (in order):
        1. Non-working day → return empty immediately.
        2. Minimum notice — slots too close to *now* are excluded.
        3. Booking horizon — slots beyond the horizon are excluded.
        4. Busy interval overlap — slots that conflict with any busy interval
           are excluded.
        """
        if now is None:
            now = datetime.now(tz=self._rules.timezone)

        if not self._rules.is_working_day(d):
            return []

        available: list[TimeSlot] = []
        for slot in self.generate_candidate_slots(d):
            if not self._rules.satisfies_min_notice(slot.start, now):
                continue
            if not self._rules.is_within_booking_horizon(slot.start, now):
                continue
            if self._overlaps_any(slot, busy_intervals):
                continue
            available.append(slot)

        return available

    def get_available_slots_for_range(
        self,
        start_date: date,
        end_date: date,
        busy_intervals: list[BusyInterval],
        now: Optional[datetime] = None,
    ) -> list[DaySlots]:
        """Return available slots grouped by date for a date range (inclusive).

        Days with no available slots are omitted from the result.
        """
        if now is None:
            now = datetime.now(tz=self._rules.timezone)

        result: list[DaySlots] = []
        current = start_date
        while current <= end_date:
            slots = self.get_available_slots(current, busy_intervals, now)
            if slots:
                result.append(DaySlots(date=current, slots=slots))
            current += timedelta(days=1)
        return result
