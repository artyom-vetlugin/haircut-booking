"""Concrete CalendarAdapter implementation backed by the Google Calendar MCP server.

This is the production adapter.  To activate it, replace ``StubCalendarAdapter``
with ``GoogleCalendarMCPAdapter`` in ``app/use_cases/deps.py``::

    from app.integrations.google_calendar_mcp.mcp_adapter import GoogleCalendarMCPAdapter
    from app.integrations.google_calendar_mcp.mcp_client import GoogleCalendarMCPClient

    mcp_client = GoogleCalendarMCPClient.from_settings(settings)
    calendar = GoogleCalendarMCPAdapter(mcp_client, timezone=settings.app_timezone)

The MCP client must be started in the FastAPI lifespan before this adapter is used.
All MCP-specific types are confined to this integration folder; the rest of the
application only sees ``CalendarEvent`` and ``BusyInterval``.
"""

from __future__ import annotations

import logging
from datetime import datetime

from app.core.exceptions import CalendarSyncError
from app.integrations.google_calendar_mcp.calendar_adapter import (
    CalendarAdapter,
    CalendarEvent,
)
from app.integrations.google_calendar_mcp.calendar_mapper import (
    mcp_busy_period_to_busy_interval,
    mcp_event_to_calendar_event,
)
from app.integrations.google_calendar_mcp.mcp_client import (
    CalendarMCPError,
    GoogleCalendarMCPClient,
)
from app.schemas.availability import BusyInterval

logger = logging.getLogger(__name__)


class GoogleCalendarMCPAdapter(CalendarAdapter):
    """CalendarAdapter backed by the Google Calendar MCP server.

    Translates ``CalendarMCPError`` and mapper ``ValueError`` into
    ``CalendarSyncError`` so that callers never see MCP internals.
    """

    def __init__(self, client: GoogleCalendarMCPClient, timezone: str) -> None:
        self._client = client
        self._timezone = timezone

    async def list_events(self, start: datetime, end: datetime) -> list[CalendarEvent]:
        """List all non-all-day events in the given range."""
        try:
            mcp_events = await self._client.list_events(time_min=start, time_max=end)
            events: list[CalendarEvent] = []
            for mcp_event in mcp_events:
                try:
                    events.append(mcp_event_to_calendar_event(mcp_event))
                except ValueError as exc:
                    # All-day events cannot be appointment slots — skip silently.
                    logger.debug("Skipping non-timed event %s: %s", mcp_event.id, exc)
            return events
        except CalendarMCPError as exc:
            logger.error("Failed to list calendar events: %s", exc)
            raise CalendarSyncError(str(exc)) from exc

    async def get_busy_intervals(
        self, start: datetime, end: datetime
    ) -> list[BusyInterval]:
        try:
            freebusy = await self._client.get_freebusy(time_min=start, time_max=end)
            calendar_data = freebusy.calendars.get(self._client.calendar_id)
            if calendar_data is None:
                return []
            return [
                mcp_busy_period_to_busy_interval(period)
                for period in calendar_data.busy
            ]
        except (CalendarMCPError, ValueError) as exc:
            logger.error("Failed to fetch busy intervals: %s", exc)
            raise CalendarSyncError(str(exc)) from exc

    async def create_event(
        self,
        start_at: datetime,
        end_at: datetime,
        title: str,
        description: str | None = None,
    ) -> CalendarEvent:
        try:
            mcp_event = await self._client.create_event(
                start_at=start_at,
                end_at=end_at,
                title=title,
                description=description,
                timezone=self._timezone,
            )
            return mcp_event_to_calendar_event(mcp_event)
        except (CalendarMCPError, ValueError) as exc:
            logger.error("Failed to create calendar event: %s", exc)
            raise CalendarSyncError(str(exc)) from exc

    async def update_event(
        self,
        event_id: str,
        start_at: datetime,
        end_at: datetime,
        title: str | None = None,
        description: str | None = None,
    ) -> CalendarEvent:
        try:
            mcp_event = await self._client.update_event(
                event_id=event_id,
                start_at=start_at,
                end_at=end_at,
                title=title,
                description=description,
                timezone=self._timezone,
            )
            return mcp_event_to_calendar_event(mcp_event)
        except (CalendarMCPError, ValueError) as exc:
            logger.error("Failed to update calendar event %s: %s", event_id, exc)
            raise CalendarSyncError(str(exc)) from exc

    async def delete_event(self, event_id: str) -> None:
        try:
            await self._client.delete_event(event_id)
        except CalendarMCPError as exc:
            logger.error("Failed to delete calendar event %s: %s", event_id, exc)
            raise CalendarSyncError(str(exc)) from exc
