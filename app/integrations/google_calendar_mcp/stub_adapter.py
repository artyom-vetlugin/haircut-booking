"""Stub calendar adapter for local development and testing.

Assigns random UUIDs as event IDs and reports no busy intervals.
Replace with the real Google Calendar MCP adapter in production.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from app.integrations.google_calendar_mcp.calendar_adapter import (
    CalendarAdapter,
    CalendarEvent,
)
from app.schemas.availability import BusyInterval


class StubCalendarAdapter(CalendarAdapter):
    """No-op adapter — all calendar writes succeed; no busy intervals are returned."""

    async def create_event(
        self,
        start_at: datetime,
        end_at: datetime,
        title: str,
        description: str | None = None,
    ) -> CalendarEvent:
        return CalendarEvent(
            event_id=str(uuid.uuid4()),
            start_at=start_at,
            end_at=end_at,
            title=title,
            description=description,
        )

    async def update_event(
        self,
        event_id: str,
        start_at: datetime,
        end_at: datetime,
        title: str | None = None,
        description: str | None = None,
    ) -> CalendarEvent:
        return CalendarEvent(
            event_id=event_id,
            start_at=start_at,
            end_at=end_at,
            title=title or "Стрижка",
            description=description,
        )

    async def delete_event(self, event_id: str) -> None:
        pass

    async def get_busy_intervals(
        self, start: datetime, end: datetime
    ) -> list[BusyInterval]:
        return []
