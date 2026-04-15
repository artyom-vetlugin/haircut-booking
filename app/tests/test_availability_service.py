"""Unit tests for BookingRulesService and AvailabilityService.

All tests are synchronous and self-contained. Settings are constructed with
model_construct() to avoid loading .env or requiring external services.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.core.config import Settings
from app.schemas.availability import BusyInterval, TimeSlot
from app.services.availability_service import AvailabilityService
from app.services.booking_rules_service import BookingRulesService

TZ = ZoneInfo("Europe/Moscow")


def _make_settings(**overrides: object) -> Settings:
    """Build a Settings instance without .env loading."""
    defaults: dict[str, object] = dict(
        database_url="postgresql+asyncpg://test:test@localhost/test",
        telegram_bot_token="test-token",
        telegram_master_chat_id=123456,
        booking_horizon_days=30,
        min_notice_hours=2,
        working_hours_start=9,
        working_hours_end=19,
        appointment_duration_minutes=60,
        working_days=[0, 1, 2, 3, 4, 5],  # Mon–Sat
        app_timezone="Europe/Moscow",
    )
    defaults.update(overrides)
    return Settings.model_construct(**defaults)  # type: ignore[arg-type]


def _dt(hour: int, minute: int = 0, day_offset: int = 0) -> datetime:
    """Return a tz-aware datetime on 2026-04-14 (Tuesday) + day_offset."""
    base = date(2026, 4, 14)  # Tuesday
    d = base + timedelta(days=day_offset)
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=TZ)


def _slot(start_hour: int, day_offset: int = 0) -> TimeSlot:
    s = _dt(start_hour, day_offset=day_offset)
    return TimeSlot(start=s, end=s + timedelta(hours=1))


def _busy(start_hour: int, end_hour: int, day_offset: int = 0) -> BusyInterval:
    return BusyInterval(
        start=_dt(start_hour, day_offset=day_offset),
        end=_dt(end_hour, day_offset=day_offset),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings() -> Settings:
    return _make_settings()


@pytest.fixture
def rules(settings: Settings) -> BookingRulesService:
    return BookingRulesService(settings)


@pytest.fixture
def svc(rules: BookingRulesService, settings: Settings) -> AvailabilityService:
    return AvailabilityService(rules, settings)


# ---------------------------------------------------------------------------
# BookingRulesService — is_working_day
# ---------------------------------------------------------------------------


class TestIsWorkingDay:
    def test_monday_is_working(self, rules: BookingRulesService) -> None:
        assert rules.is_working_day(date(2026, 4, 13))  # Monday

    def test_tuesday_is_working(self, rules: BookingRulesService) -> None:
        assert rules.is_working_day(date(2026, 4, 14))  # Tuesday

    def test_saturday_is_working_by_default(self, rules: BookingRulesService) -> None:
        assert rules.is_working_day(date(2026, 4, 18))  # Saturday

    def test_sunday_is_not_working(self, rules: BookingRulesService) -> None:
        assert not rules.is_working_day(date(2026, 4, 19))  # Sunday

    def test_custom_working_days_excludes_saturday(self) -> None:
        settings = _make_settings(working_days=[0, 1, 2, 3, 4])  # Mon–Fri only
        r = BookingRulesService(settings)
        assert not r.is_working_day(date(2026, 4, 18))  # Saturday

    def test_custom_working_days_includes_sunday(self) -> None:
        settings = _make_settings(working_days=[6])  # Sunday only
        r = BookingRulesService(settings)
        assert r.is_working_day(date(2026, 4, 19))  # Sunday


# ---------------------------------------------------------------------------
# BookingRulesService — is_within_working_hours
# ---------------------------------------------------------------------------


class TestIsWithinWorkingHours:
    def test_first_slot_of_day(self, rules: BookingRulesService) -> None:
        assert rules.is_within_working_hours(_dt(9))  # 09:00–10:00

    def test_last_slot_of_day(self, rules: BookingRulesService) -> None:
        assert rules.is_within_working_hours(_dt(18))  # 18:00–19:00

    def test_slot_before_working_hours(self, rules: BookingRulesService) -> None:
        assert not rules.is_within_working_hours(_dt(8))  # 08:00–09:00

    def test_slot_starting_at_closing_time(self, rules: BookingRulesService) -> None:
        # 19:00 start → 20:00 end, exceeds working_hours_end
        assert not rules.is_within_working_hours(_dt(19))

    def test_slot_partially_outside_at_end(self, rules: BookingRulesService) -> None:
        # 18:30 start → 19:30 end, exceeds working_hours_end=19
        start = _dt(18, 30)
        assert not rules.is_within_working_hours(start)

    def test_midday_slot(self, rules: BookingRulesService) -> None:
        assert rules.is_within_working_hours(_dt(13))


# ---------------------------------------------------------------------------
# BookingRulesService — satisfies_min_notice
# ---------------------------------------------------------------------------


class TestSatisfiesMinNotice:
    def test_slot_far_in_future_satisfies(self, rules: BookingRulesService) -> None:
        now = _dt(10)
        slot_start = _dt(14)  # 4 h ahead, min_notice=2
        assert rules.satisfies_min_notice(slot_start, now)

    def test_slot_exactly_at_min_notice_satisfies(
        self, rules: BookingRulesService
    ) -> None:
        now = _dt(10)
        slot_start = _dt(12)  # exactly 2 h ahead
        assert rules.satisfies_min_notice(slot_start, now)

    def test_slot_one_minute_before_min_notice_fails(
        self, rules: BookingRulesService
    ) -> None:
        now = _dt(10)
        slot_start = now + timedelta(hours=2) - timedelta(minutes=1)
        assert not rules.satisfies_min_notice(slot_start, now)

    def test_slot_in_the_past_fails(self, rules: BookingRulesService) -> None:
        now = _dt(10)
        slot_start = _dt(9)  # 1 h in the past
        assert not rules.satisfies_min_notice(slot_start, now)

    def test_zero_min_notice_allows_immediate_slot(self) -> None:
        settings = _make_settings(min_notice_hours=0)
        r = BookingRulesService(settings)
        now = _dt(10)
        assert r.satisfies_min_notice(now, now)


# ---------------------------------------------------------------------------
# BookingRulesService — is_within_booking_horizon
# ---------------------------------------------------------------------------


class TestIsWithinBookingHorizon:
    def test_slot_today_is_within_horizon(self, rules: BookingRulesService) -> None:
        now = _dt(10)
        assert rules.is_within_booking_horizon(_dt(14), now)

    def test_slot_one_day_before_horizon_end_is_within(
        self, rules: BookingRulesService
    ) -> None:
        now = _dt(10)
        slot_start = now + timedelta(days=29)
        assert rules.is_within_booking_horizon(slot_start, now)

    def test_slot_at_exact_horizon_end_is_outside(
        self, rules: BookingRulesService
    ) -> None:
        now = _dt(10)
        slot_start = now + timedelta(days=30)  # horizon_end is exclusive
        assert not rules.is_within_booking_horizon(slot_start, now)

    def test_slot_beyond_horizon_fails(self, rules: BookingRulesService) -> None:
        now = _dt(10)
        slot_start = now + timedelta(days=60)
        assert not rules.is_within_booking_horizon(slot_start, now)


# ---------------------------------------------------------------------------
# AvailabilityService — generate_candidate_slots
# ---------------------------------------------------------------------------


class TestGenerateCandidateSlots:
    def test_count_matches_working_window(self, svc: AvailabilityService) -> None:
        # working 09:00–19:00 with 1 h slots → 10 slots
        slots = svc.generate_candidate_slots(date(2026, 4, 14))
        assert len(slots) == 10

    def test_first_slot_starts_at_working_hours_start(
        self, svc: AvailabilityService
    ) -> None:
        slots = svc.generate_candidate_slots(date(2026, 4, 14))
        assert slots[0].start.hour == 9

    def test_last_slot_ends_at_working_hours_end(
        self, svc: AvailabilityService
    ) -> None:
        slots = svc.generate_candidate_slots(date(2026, 4, 14))
        assert slots[-1].end.hour == 19

    def test_each_slot_is_one_hour(self, svc: AvailabilityService) -> None:
        slots = svc.generate_candidate_slots(date(2026, 4, 14))
        for slot in slots:
            assert slot.end - slot.start == timedelta(hours=1)

    def test_slots_are_contiguous(self, svc: AvailabilityService) -> None:
        slots = svc.generate_candidate_slots(date(2026, 4, 14))
        for a, b in zip(slots, slots[1:]):
            assert a.end == b.start


# ---------------------------------------------------------------------------
# AvailabilityService — slot_overlaps_busy (overlap detection)
# ---------------------------------------------------------------------------


class TestSlotOverlapsBusy:
    """Exhaustive overlap/adjacency tests for the static helper."""

    def test_slot_fully_inside_busy(self) -> None:
        slot = _slot(11)
        busy = _busy(10, 13)
        assert AvailabilityService.slot_overlaps_busy(slot, busy)

    def test_slot_contains_busy(self) -> None:
        slot = _slot(10)
        busy = BusyInterval(start=_dt(10, 15), end=_dt(10, 45))
        assert AvailabilityService.slot_overlaps_busy(slot, busy)

    def test_slot_starts_inside_busy(self) -> None:
        slot = _slot(11)
        busy = _busy(10, 12)
        assert AvailabilityService.slot_overlaps_busy(slot, busy)

    def test_slot_ends_inside_busy(self) -> None:
        slot = _slot(10)
        busy = _busy(10, 12)  # slot 10–11, busy 10–12 → overlap at start
        assert AvailabilityService.slot_overlaps_busy(slot, busy)

    def test_slot_exactly_equals_busy(self) -> None:
        slot = _slot(10)
        busy = _busy(10, 11)
        assert AvailabilityService.slot_overlaps_busy(slot, busy)

    def test_slot_completely_before_busy(self) -> None:
        slot = _slot(9)
        busy = _busy(11, 13)
        assert not AvailabilityService.slot_overlaps_busy(slot, busy)

    def test_slot_completely_after_busy(self) -> None:
        slot = _slot(14)
        busy = _busy(10, 12)
        assert not AvailabilityService.slot_overlaps_busy(slot, busy)

    def test_slot_end_equals_busy_start_no_overlap(self) -> None:
        # slot 10–11, busy 11–13 → adjacent, not overlapping
        slot = _slot(10)
        busy = _busy(11, 13)
        assert not AvailabilityService.slot_overlaps_busy(slot, busy)

    def test_slot_start_equals_busy_end_no_overlap(self) -> None:
        # slot 12–13, busy 10–12 → adjacent, not overlapping
        slot = _slot(12)
        busy = _busy(10, 12)
        assert not AvailabilityService.slot_overlaps_busy(slot, busy)

    def test_busy_starts_one_minute_before_slot_ends(self) -> None:
        # slot 10:00–11:00, busy 10:59–12:00 → overlap
        slot = _slot(10)
        busy = BusyInterval(start=_dt(10, 59), end=_dt(12))
        assert AvailabilityService.slot_overlaps_busy(slot, busy)


# ---------------------------------------------------------------------------
# AvailabilityService — get_available_slots (integration of all filters)
# ---------------------------------------------------------------------------


class TestGetAvailableSlots:
    def test_non_working_day_returns_empty(self, svc: AvailabilityService) -> None:
        sunday = date(2026, 4, 19)
        result = svc.get_available_slots(sunday, [], now=_dt(10))
        assert result == []

    def test_all_slots_free_returns_full_day(self, svc: AvailabilityService) -> None:
        # now = 00:00 same day, no busy, min_notice=2 → slots 9–19 all pass
        now = datetime(2026, 4, 14, 0, 0, tzinfo=TZ)
        slots = svc.get_available_slots(date(2026, 4, 14), [], now=now)
        assert len(slots) == 10

    def test_min_notice_filters_early_slots(self, svc: AvailabilityService) -> None:
        now = _dt(10)  # min_notice=2 h → 09:00 and 10:00 slots removed
        slots = svc.get_available_slots(date(2026, 4, 14), [], now=now)
        assert all(s.start >= now + timedelta(hours=2) for s in slots)
        assert not any(s.start.hour < 12 for s in slots)

    def test_booking_horizon_filters_far_future_slots(self) -> None:
        settings = _make_settings(booking_horizon_days=1, min_notice_hours=0)
        r = BookingRulesService(settings)
        svc = AvailabilityService(r, settings)
        # now = start of day; horizon = 1 day → tomorrow's slots excluded
        now = datetime(2026, 4, 14, 0, 0, tzinfo=TZ)
        slots = svc.get_available_slots(date(2026, 4, 15), [], now=now)
        assert slots == []

    def test_busy_interval_removes_overlapping_slots(
        self, svc: AvailabilityService
    ) -> None:
        now = datetime(2026, 4, 14, 0, 0, tzinfo=TZ)
        busy = [_busy(11, 13)]  # blocks 11:00–13:00
        slots = svc.get_available_slots(date(2026, 4, 14), busy, now=now)
        start_hours = {s.start.hour for s in slots}
        assert 11 not in start_hours
        assert 12 not in start_hours
        assert 10 in start_hours
        assert 13 in start_hours

    def test_busy_exactly_adjacent_does_not_block_slot(
        self, svc: AvailabilityService
    ) -> None:
        now = datetime(2026, 4, 14, 0, 0, tzinfo=TZ)
        # busy 11:00–12:00 is exactly the 11:00 slot — blocked
        # busy 12:00–13:00 is adjacent to 11:00 slot end — 12:00 slot blocked
        busy = [_busy(11, 12)]
        slots = svc.get_available_slots(date(2026, 4, 14), busy, now=now)
        start_hours = {s.start.hour for s in slots}
        assert 11 not in start_hours  # blocked
        assert 12 in start_hours      # adjacent, not blocked
        assert 10 in start_hours

    def test_multiple_busy_intervals(self, svc: AvailabilityService) -> None:
        now = datetime(2026, 4, 14, 0, 0, tzinfo=TZ)
        busy = [_busy(9, 10), _busy(14, 16)]
        slots = svc.get_available_slots(date(2026, 4, 14), busy, now=now)
        start_hours = {s.start.hour for s in slots}
        assert 9 not in start_hours
        assert 14 not in start_hours
        assert 15 not in start_hours
        assert 10 in start_hours
        assert 13 in start_hours
        assert 16 in start_hours


# ---------------------------------------------------------------------------
# AvailabilityService — get_available_slots_for_range
# ---------------------------------------------------------------------------


class TestGetAvailableSlotsForRange:
    def test_range_skips_non_working_days(self, svc: AvailabilityService) -> None:
        now = datetime(2026, 4, 14, 0, 0, tzinfo=TZ)
        # Mon 13 Apr – Sun 19 Apr; Sunday should be absent
        result = svc.get_available_slots_for_range(
            date(2026, 4, 13), date(2026, 4, 19), [], now=now
        )
        days = {ds.date for ds in result}
        assert date(2026, 4, 19) not in days  # Sunday excluded
        assert date(2026, 4, 18) in days       # Saturday included

    def test_range_returns_correct_number_of_working_days(
        self, svc: AvailabilityService
    ) -> None:
        # now is before the range so all slots pass min_notice
        now = datetime(2026, 4, 12, 0, 0, tzinfo=TZ)  # Sunday before the range
        # Mon 13 Apr – Sat 18 Apr → 6 working days (Mon–Sat)
        result = svc.get_available_slots_for_range(
            date(2026, 4, 13), date(2026, 4, 18), [], now=now
        )
        assert len(result) == 6

    def test_range_with_all_days_beyond_horizon_returns_empty(self) -> None:
        settings = _make_settings(booking_horizon_days=7, min_notice_hours=0)
        r = BookingRulesService(settings)
        svc = AvailabilityService(r, settings)
        now = datetime(2026, 4, 14, 0, 0, tzinfo=TZ)
        result = svc.get_available_slots_for_range(
            date(2026, 5, 1), date(2026, 5, 7), [], now=now
        )
        assert result == []
