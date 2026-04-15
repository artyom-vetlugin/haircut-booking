"""Unit tests for calendar_mapper.py pure conversion functions.

These functions are stateless and side-effect-free — no mocks or I/O needed.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.integrations.google_calendar_mcp.calendar_mapper import (
    _parse_iso,
    mcp_busy_period_to_busy_interval,
    mcp_event_to_calendar_event,
)
from app.integrations.google_calendar_mcp.calendar_models import (
    MCPEvent,
    MCPEventDateTime,
    MCPFreeBusyPeriod,
)


# ---------------------------------------------------------------------------
# _parse_iso
# ---------------------------------------------------------------------------


class TestParseIso:
    def test_parses_utc_offset_string(self) -> None:
        dt = _parse_iso("2026-04-20T10:00:00+00:00")
        assert dt.hour == 10
        assert dt.tzinfo is not None

    def test_parses_positive_offset(self) -> None:
        dt = _parse_iso("2026-04-20T15:00:00+05:00")
        assert dt.hour == 15
        assert dt.utcoffset() == timedelta(hours=5)

    def test_attaches_utc_when_no_timezone(self) -> None:
        """Naive ISO string gets UTC attached — defensive guard for bad payloads."""
        dt = _parse_iso("2026-04-20T10:00:00")
        assert dt.tzinfo is not None
        assert dt.tzinfo == ZoneInfo("UTC")

    def test_preserves_minutes_and_seconds(self) -> None:
        dt = _parse_iso("2026-04-20T10:30:45+00:00")
        assert dt.minute == 30
        assert dt.second == 45

    def test_parses_negative_offset(self) -> None:
        dt = _parse_iso("2026-04-20T08:00:00-03:00")
        assert dt.hour == 8
        assert dt.utcoffset() == timedelta(hours=-3)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mcp_event(
    event_id: str = "evt-001",
    summary: str = "Стрижка",
    start: str = "2026-04-20T10:00:00+05:00",
    end: str = "2026-04-20T11:00:00+05:00",
    description: str | None = None,
) -> MCPEvent:
    return MCPEvent(
        id=event_id,
        summary=summary,
        description=description,
        start=MCPEventDateTime(dateTime=start),
        end=MCPEventDateTime(dateTime=end),
    )


# ---------------------------------------------------------------------------
# mcp_event_to_calendar_event
# ---------------------------------------------------------------------------


class TestMcpEventToCalendarEvent:
    def test_maps_event_id(self) -> None:
        event = mcp_event_to_calendar_event(_mcp_event(event_id="my-id"))
        assert event.event_id == "my-id"

    def test_maps_title(self) -> None:
        event = mcp_event_to_calendar_event(_mcp_event(summary="Haircut"))
        assert event.title == "Haircut"

    def test_maps_start_and_end_hours(self) -> None:
        event = mcp_event_to_calendar_event(
            _mcp_event(
                start="2026-04-20T10:00:00+05:00",
                end="2026-04-20T11:00:00+05:00",
            )
        )
        assert event.start_at.hour == 10
        assert event.end_at.hour == 11

    def test_maps_description(self) -> None:
        event = mcp_event_to_calendar_event(_mcp_event(description="Test desc"))
        assert event.description == "Test desc"

    def test_none_description_preserved(self) -> None:
        event = mcp_event_to_calendar_event(_mcp_event(description=None))
        assert event.description is None

    def test_start_and_end_are_timezone_aware(self) -> None:
        event = mcp_event_to_calendar_event(_mcp_event())
        assert event.start_at.tzinfo is not None
        assert event.end_at.tzinfo is not None

    def test_raises_for_all_day_event_missing_datetime(self) -> None:
        """All-day event (date only, no dateTime) must raise ValueError."""
        all_day = MCPEvent(
            id="holiday",
            summary="Public Holiday",
            start=MCPEventDateTime(date="2026-04-20"),
            end=MCPEventDateTime(date="2026-04-21"),
        )
        with pytest.raises(ValueError, match="all-day events are not supported"):
            mcp_event_to_calendar_event(all_day)

    def test_raises_when_end_datetime_missing(self) -> None:
        """Event with a dateTime start but date-only end must raise ValueError."""
        partial = MCPEvent(
            id="partial",
            summary="Odd",
            start=MCPEventDateTime(dateTime="2026-04-20T10:00:00+05:00"),
            end=MCPEventDateTime(date="2026-04-20"),
        )
        with pytest.raises(ValueError):
            mcp_event_to_calendar_event(partial)

    def test_raises_when_start_datetime_missing(self) -> None:
        partial = MCPEvent(
            id="partial2",
            summary="Odd",
            start=MCPEventDateTime(date="2026-04-20"),
            end=MCPEventDateTime(dateTime="2026-04-20T11:00:00+05:00"),
        )
        with pytest.raises(ValueError):
            mcp_event_to_calendar_event(partial)

    def test_utc_string_is_parsed_correctly(self) -> None:
        event = mcp_event_to_calendar_event(
            _mcp_event(
                start="2026-04-20T10:00:00+00:00",
                end="2026-04-20T11:00:00+00:00",
            )
        )
        assert event.start_at.utcoffset() == timedelta(0)

    def test_extra_fields_in_mcp_event_are_ignored(self) -> None:
        """MCPEvent has extra='ignore', so unknown fields must not cause errors."""
        raw = {
            "id": "evt-x",
            "summary": "Стрижка",
            "start": {"dateTime": "2026-04-20T10:00:00+05:00"},
            "end": {"dateTime": "2026-04-20T11:00:00+05:00"},
            "calendarId": "primary",
            "accountId": "user@gmail.com",
        }
        mcp = MCPEvent(**raw)
        event = mcp_event_to_calendar_event(mcp)
        assert event.event_id == "evt-x"


# ---------------------------------------------------------------------------
# mcp_busy_period_to_busy_interval
# ---------------------------------------------------------------------------


class TestMcpBusyPeriodToBusyInterval:
    def test_maps_start_and_end_hours(self) -> None:
        period = MCPFreeBusyPeriod(
            start="2026-04-20T09:00:00+00:00",
            end="2026-04-20T10:00:00+00:00",
        )
        interval = mcp_busy_period_to_busy_interval(period)
        assert interval.start.hour == 9
        assert interval.end.hour == 10

    def test_result_is_timezone_aware(self) -> None:
        period = MCPFreeBusyPeriod(
            start="2026-04-20T09:00:00+00:00",
            end="2026-04-20T10:00:00+00:00",
        )
        interval = mcp_busy_period_to_busy_interval(period)
        assert interval.start.tzinfo is not None
        assert interval.end.tzinfo is not None

    def test_naive_string_gets_utc_attached(self) -> None:
        """Defensive: naive ISO string in a busy period gets UTC."""
        period = MCPFreeBusyPeriod(
            start="2026-04-20T09:00:00",
            end="2026-04-20T10:00:00",
        )
        interval = mcp_busy_period_to_busy_interval(period)
        assert interval.start.tzinfo is not None

    def test_offset_preserved(self) -> None:
        period = MCPFreeBusyPeriod(
            start="2026-04-20T14:00:00+05:00",
            end="2026-04-20T15:00:00+05:00",
        )
        interval = mcp_busy_period_to_busy_interval(period)
        assert interval.start.utcoffset() == timedelta(hours=5)
        assert interval.end.utcoffset() == timedelta(hours=5)
