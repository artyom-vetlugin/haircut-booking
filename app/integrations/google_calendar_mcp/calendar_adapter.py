"""Abstract calendar adapter interface.

The rest of the application depends only on this interface, not on any
specific calendar implementation. The Google Calendar MCP adapter will
implement this contract; a fake in-memory adapter is used for tests.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from app.schemas.availability import BusyInterval


@dataclass
class CalendarEvent:
    event_id: str
    start_at: datetime
    end_at: datetime
    title: str
    description: str | None = None


class CalendarAdapter(ABC):
    """Interface for all calendar operations used by the appointment service."""

    @abstractmethod
    async def create_event(
        self,
        start_at: datetime,
        end_at: datetime,
        title: str,
        description: str | None = None,
    ) -> CalendarEvent:
        """Create a calendar event and return it with the provider-assigned event_id."""

    @abstractmethod
    async def update_event(
        self,
        event_id: str,
        start_at: datetime,
        end_at: datetime,
        title: str | None = None,
        description: str | None = None,
    ) -> CalendarEvent:
        """Update the time (and optionally title/description) of an existing event."""

    @abstractmethod
    async def delete_event(self, event_id: str) -> None:
        """Permanently remove a calendar event."""

    @abstractmethod
    async def list_events(
        self, start: datetime, end: datetime
    ) -> list[CalendarEvent]:
        """Return all calendar events whose start falls within [start, end)."""

    @abstractmethod
    async def get_busy_intervals(
        self, start: datetime, end: datetime
    ) -> list[BusyInterval]:
        """Return busy intervals within the given time range."""
