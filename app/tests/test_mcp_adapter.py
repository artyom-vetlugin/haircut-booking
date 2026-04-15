"""Tests for GoogleCalendarMCPAdapter.

Covers error-translation paths (CalendarMCPError / ValueError → CalendarSyncError),
all-day event skipping in list_events, and empty-result edge cases.
The MCP subprocess client is always replaced with a lightweight MagicMock.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

# The mcp package is a runtime dependency not installed in the test venv.
# Stub it out before any import chain reaches mcp_client.py.
if "mcp" not in sys.modules:
    sys.modules["mcp"] = MagicMock()
    sys.modules["mcp.client"] = MagicMock()
    sys.modules["mcp.client.stdio"] = MagicMock()

from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from app.core.exceptions import CalendarSyncError
from app.integrations.google_calendar_mcp.calendar_models import (
    MCPEvent,
    MCPEventDateTime,
    MCPFreeBusyCalendar,
    MCPFreeBusyPeriod,
    MCPFreeBusyResponse,
)
from app.integrations.google_calendar_mcp.mcp_adapter import GoogleCalendarMCPAdapter
from app.integrations.google_calendar_mcp.mcp_client import (
    CalendarMCPError,
    GoogleCalendarMCPClient,
)

TZ = ZoneInfo("Asia/Almaty")
TZ_STR = "Asia/Almaty"
CALENDAR_ID = "primary@example.com"

START = datetime(2026, 4, 20, 9, 0, tzinfo=TZ)
END = datetime(2026, 4, 20, 10, 0, tzinfo=TZ)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(calendar_id: str = CALENDAR_ID) -> MagicMock:
    client = MagicMock(spec=GoogleCalendarMCPClient)
    client.calendar_id = calendar_id
    return client


def _timed_event(
    event_id: str = "evt-1",
    start: str = "2026-04-20T09:00:00+05:00",
    end: str = "2026-04-20T10:00:00+05:00",
    summary: str = "Стрижка",
) -> MCPEvent:
    return MCPEvent(
        id=event_id,
        summary=summary,
        start=MCPEventDateTime(dateTime=start),
        end=MCPEventDateTime(dateTime=end),
    )


def _all_day_event(event_id: str = "holiday") -> MCPEvent:
    return MCPEvent(
        id=event_id,
        summary="Public Holiday",
        start=MCPEventDateTime(date="2026-04-20"),
        end=MCPEventDateTime(date="2026-04-21"),
    )


def _freebusy_response(
    calendar_id: str = CALENDAR_ID,
    periods: list[tuple[str, str]] | None = None,
) -> MCPFreeBusyResponse:
    periods = periods or []
    return MCPFreeBusyResponse(
        calendars={
            calendar_id: MCPFreeBusyCalendar(
                busy=[MCPFreeBusyPeriod(start=s, end=e) for s, e in periods]
            )
        }
    )


# ---------------------------------------------------------------------------
# list_events
# ---------------------------------------------------------------------------


class TestListEvents:
    @pytest.mark.asyncio
    async def test_returns_mapped_timed_events(self) -> None:
        client = _make_client()
        client.list_events = AsyncMock(return_value=[_timed_event(event_id="evt-1")])
        adapter = GoogleCalendarMCPAdapter(client, TZ_STR)

        events = await adapter.list_events(START, END)

        assert len(events) == 1
        assert events[0].event_id == "evt-1"

    @pytest.mark.asyncio
    async def test_skips_all_day_events_silently(self) -> None:
        """All-day events must be filtered out without raising."""
        client = _make_client()
        client.list_events = AsyncMock(
            return_value=[_timed_event(event_id="timed"), _all_day_event()]
        )
        adapter = GoogleCalendarMCPAdapter(client, TZ_STR)

        events = await adapter.list_events(START, END)

        assert len(events) == 1
        assert events[0].event_id == "timed"

    @pytest.mark.asyncio
    async def test_all_all_day_events_returns_empty_list(self) -> None:
        client = _make_client()
        client.list_events = AsyncMock(
            return_value=[_all_day_event("h1"), _all_day_event("h2")]
        )
        adapter = GoogleCalendarMCPAdapter(client, TZ_STR)

        events = await adapter.list_events(START, END)

        assert events == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_events(self) -> None:
        client = _make_client()
        client.list_events = AsyncMock(return_value=[])
        adapter = GoogleCalendarMCPAdapter(client, TZ_STR)

        events = await adapter.list_events(START, END)

        assert events == []

    @pytest.mark.asyncio
    async def test_raises_calendar_sync_error_on_mcp_error(self) -> None:
        client = _make_client()
        client.list_events = AsyncMock(side_effect=CalendarMCPError("timeout"))
        adapter = GoogleCalendarMCPAdapter(client, TZ_STR)

        with pytest.raises(CalendarSyncError):
            await adapter.list_events(START, END)

    @pytest.mark.asyncio
    async def test_returns_multiple_mapped_events(self) -> None:
        client = _make_client()
        client.list_events = AsyncMock(
            return_value=[
                _timed_event("e1"),
                _timed_event("e2"),
                _timed_event("e3"),
            ]
        )
        adapter = GoogleCalendarMCPAdapter(client, TZ_STR)

        events = await adapter.list_events(START, END)

        assert len(events) == 3
        assert {e.event_id for e in events} == {"e1", "e2", "e3"}


# ---------------------------------------------------------------------------
# get_busy_intervals
# ---------------------------------------------------------------------------


class TestGetBusyIntervals:
    @pytest.mark.asyncio
    async def test_returns_busy_intervals(self) -> None:
        client = _make_client()
        client.get_freebusy = AsyncMock(
            return_value=_freebusy_response(
                periods=[("2026-04-20T09:00:00+00:00", "2026-04-20T10:00:00+00:00")]
            )
        )
        adapter = GoogleCalendarMCPAdapter(client, TZ_STR)

        intervals = await adapter.get_busy_intervals(START, END)

        assert len(intervals) == 1
        assert intervals[0].start.hour == 9

    @pytest.mark.asyncio
    async def test_returns_multiple_busy_intervals(self) -> None:
        client = _make_client()
        client.get_freebusy = AsyncMock(
            return_value=_freebusy_response(
                periods=[
                    ("2026-04-20T09:00:00+00:00", "2026-04-20T10:00:00+00:00"),
                    ("2026-04-20T14:00:00+00:00", "2026-04-20T15:00:00+00:00"),
                ]
            )
        )
        adapter = GoogleCalendarMCPAdapter(client, TZ_STR)

        intervals = await adapter.get_busy_intervals(START, END)

        assert len(intervals) == 2

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_busy_periods(self) -> None:
        client = _make_client()
        client.get_freebusy = AsyncMock(
            return_value=_freebusy_response(periods=[])
        )
        adapter = GoogleCalendarMCPAdapter(client, TZ_STR)

        intervals = await adapter.get_busy_intervals(START, END)

        assert intervals == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_calendar_not_in_response(self) -> None:
        """Adapter must return [] if its calendar ID is absent from the response."""
        client = _make_client(calendar_id=CALENDAR_ID)
        client.get_freebusy = AsyncMock(
            return_value=MCPFreeBusyResponse(
                calendars={"other@example.com": MCPFreeBusyCalendar()}
            )
        )
        adapter = GoogleCalendarMCPAdapter(client, TZ_STR)

        intervals = await adapter.get_busy_intervals(START, END)

        assert intervals == []

    @pytest.mark.asyncio
    async def test_raises_calendar_sync_error_on_mcp_error(self) -> None:
        client = _make_client()
        client.get_freebusy = AsyncMock(side_effect=CalendarMCPError("auth failed"))
        adapter = GoogleCalendarMCPAdapter(client, TZ_STR)

        with pytest.raises(CalendarSyncError):
            await adapter.get_busy_intervals(START, END)


# ---------------------------------------------------------------------------
# create_event
# ---------------------------------------------------------------------------


class TestCreateEvent:
    @pytest.mark.asyncio
    async def test_returns_mapped_calendar_event(self) -> None:
        client = _make_client()
        client.create_event = AsyncMock(return_value=_timed_event(event_id="new-evt"))
        adapter = GoogleCalendarMCPAdapter(client, TZ_STR)

        event = await adapter.create_event(START, END, "Стрижка")

        assert event.event_id == "new-evt"

    @pytest.mark.asyncio
    async def test_passes_timezone_to_client(self) -> None:
        client = _make_client()
        client.create_event = AsyncMock(return_value=_timed_event())
        adapter = GoogleCalendarMCPAdapter(client, TZ_STR)

        await adapter.create_event(START, END, "Стрижка", description="desc")

        client.create_event.assert_awaited_once_with(
            start_at=START,
            end_at=END,
            title="Стрижка",
            description="desc",
            timezone=TZ_STR,
        )

    @pytest.mark.asyncio
    async def test_raises_calendar_sync_error_on_mcp_error(self) -> None:
        client = _make_client()
        client.create_event = AsyncMock(side_effect=CalendarMCPError("quota exceeded"))
        adapter = GoogleCalendarMCPAdapter(client, TZ_STR)

        with pytest.raises(CalendarSyncError):
            await adapter.create_event(START, END, "Стрижка")

    @pytest.mark.asyncio
    async def test_raises_calendar_sync_error_when_mapper_raises_value_error(self) -> None:
        """If the MCP server returns an all-day event for create, ValueError → CalendarSyncError."""
        client = _make_client()
        client.create_event = AsyncMock(return_value=_all_day_event())
        adapter = GoogleCalendarMCPAdapter(client, TZ_STR)

        with pytest.raises(CalendarSyncError):
            await adapter.create_event(START, END, "Стрижка")


# ---------------------------------------------------------------------------
# update_event
# ---------------------------------------------------------------------------


class TestUpdateEvent:
    @pytest.mark.asyncio
    async def test_returns_updated_event(self) -> None:
        new_end = END + timedelta(hours=1)
        client = _make_client()
        client.update_event = AsyncMock(return_value=_timed_event(event_id="evt-1"))
        adapter = GoogleCalendarMCPAdapter(client, TZ_STR)

        event = await adapter.update_event("evt-1", START, new_end)

        assert event.event_id == "evt-1"

    @pytest.mark.asyncio
    async def test_passes_event_id_to_client(self) -> None:
        client = _make_client()
        client.update_event = AsyncMock(return_value=_timed_event(event_id="evt-x"))
        adapter = GoogleCalendarMCPAdapter(client, TZ_STR)

        await adapter.update_event("evt-x", START, END, title="Updated")

        client.update_event.assert_awaited_once_with(
            event_id="evt-x",
            start_at=START,
            end_at=END,
            title="Updated",
            description=None,
            timezone=TZ_STR,
        )

    @pytest.mark.asyncio
    async def test_raises_calendar_sync_error_on_mcp_error(self) -> None:
        client = _make_client()
        client.update_event = AsyncMock(side_effect=CalendarMCPError("not found"))
        adapter = GoogleCalendarMCPAdapter(client, TZ_STR)

        with pytest.raises(CalendarSyncError):
            await adapter.update_event("evt-1", START, END)

    @pytest.mark.asyncio
    async def test_raises_calendar_sync_error_when_mapper_raises_value_error(self) -> None:
        client = _make_client()
        client.update_event = AsyncMock(return_value=_all_day_event())
        adapter = GoogleCalendarMCPAdapter(client, TZ_STR)

        with pytest.raises(CalendarSyncError):
            await adapter.update_event("evt-1", START, END)


# ---------------------------------------------------------------------------
# delete_event
# ---------------------------------------------------------------------------


class TestDeleteEvent:
    @pytest.mark.asyncio
    async def test_delegates_to_client(self) -> None:
        client = _make_client()
        client.delete_event = AsyncMock()
        adapter = GoogleCalendarMCPAdapter(client, TZ_STR)

        await adapter.delete_event("evt-1")

        client.delete_event.assert_awaited_once_with("evt-1")

    @pytest.mark.asyncio
    async def test_raises_calendar_sync_error_on_mcp_error(self) -> None:
        client = _make_client()
        client.delete_event = AsyncMock(side_effect=CalendarMCPError("event not found"))
        adapter = GoogleCalendarMCPAdapter(client, TZ_STR)

        with pytest.raises(CalendarSyncError):
            await adapter.delete_event("evt-1")
