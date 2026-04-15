"""Pure conversion functions between MCP/Google Calendar shapes and internal models.

All functions are stateless and side-effect-free, which makes them easy to unit
test without any I/O.  Nothing outside this package should need to import from here.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.integrations.google_calendar_mcp.calendar_adapter import CalendarEvent
from app.integrations.google_calendar_mcp.calendar_models import (
    MCPEvent,
    MCPFreeBusyPeriod,
)
from app.schemas.availability import BusyInterval


def _parse_iso(value: str) -> datetime:
    """Parse an ISO 8601 string into a timezone-aware datetime.

    Attaches UTC if the string carries no timezone information — this should
    never happen with real Google Calendar responses but prevents silent bugs.
    """
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt


def mcp_event_to_calendar_event(event: MCPEvent) -> CalendarEvent:
    """Convert an MCPEvent into the application's CalendarEvent.

    Raises:
        ValueError: if the event is an all-day event (no dateTime field),
            because all-day events cannot be used as appointment slots.
    """
    start_str = event.start.dateTime
    end_str = event.end.dateTime

    if not start_str or not end_str:
        raise ValueError(
            f"Calendar event {event.id!r} has no dateTime — "
            "all-day events are not supported as appointment slots."
        )

    return CalendarEvent(
        event_id=event.id,
        start_at=_parse_iso(start_str),
        end_at=_parse_iso(end_str),
        title=event.summary,
        description=event.description,
    )


def mcp_busy_period_to_busy_interval(period: MCPFreeBusyPeriod) -> BusyInterval:
    """Convert a single MCPFreeBusyPeriod into a BusyInterval."""
    return BusyInterval(
        start=_parse_iso(period.start),
        end=_parse_iso(period.end),
    )
