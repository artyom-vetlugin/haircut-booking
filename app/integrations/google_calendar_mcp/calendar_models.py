from __future__ import annotations

"""Internal Pydantic models representing raw Google Calendar API shapes.

These models mirror the JSON structures returned by the MCP server tools and
are used only inside this integration folder to deserialize MCP responses.

Nothing outside this package should import from here.
Use CalendarEvent and BusyInterval from calendar_adapter / availability instead.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict


class MCPEventDateTime(BaseModel):
    """Google Calendar dateTime or date field."""

    # ISO 8601 with UTC offset — absent for all-day events.
    dateTime: Optional[str] = None
    # YYYY-MM-DD — set for all-day events only.
    date: Optional[str] = None
    timeZone: Optional[str] = None


class MCPEvent(BaseModel):
    """A Google Calendar event as returned by the MCP server.

    The server returns StructuredGoogleEvent which adds ``calendarId`` and
    ``accountId`` fields on top of the standard Google Calendar shape.
    ``extra="ignore"`` lets those pass through without validation errors.
    """

    model_config = ConfigDict(extra="ignore")

    id: str
    summary: str
    description: Optional[str] = None
    start: MCPEventDateTime
    end: MCPEventDateTime
    # confirmed | tentative | cancelled
    status: str = "confirmed"


class MCPFreeBusyPeriod(BaseModel):
    """A single busy period from the Google Calendar freebusy API."""

    start: str  # ISO 8601
    end: str    # ISO 8601


class MCPFreeBusyCalendar(BaseModel):
    """Freebusy data for one calendar returned inside a freebusy response."""

    busy: list[MCPFreeBusyPeriod] = []


class MCPFreeBusyResponse(BaseModel):
    """Top-level freebusy response payload.

    Shape::

        {
          "calendars": {
            "<calendar-id>": { "busy": [{"start": "...", "end": "..."}] }
          }
        }
    """

    calendars: dict[str, MCPFreeBusyCalendar] = {}
