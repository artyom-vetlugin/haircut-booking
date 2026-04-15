"""Low-level async client for the Google Calendar MCP server.

Manages the MCP server subprocess and session lifecycle, and exposes typed
methods for each Google Calendar tool.  Higher layers (GoogleCalendarMCPAdapter)
use this client and never deal with raw MCP protocol details directly.

Usage
-----
The client must be started before use and stopped on shutdown::

    client = GoogleCalendarMCPClient.from_settings(settings)
    await client.start()
    ...
    await client.stop()

In a FastAPI app, wire ``start`` / ``stop`` into the ``lifespan`` handler alongside
the Telegram bot client::

    async def lifespan(app: FastAPI):
        await bot_client.initialize()
        await mcp_client.start()
        yield
        await bot_client.shutdown()
        await mcp_client.stop()
"""

from __future__ import annotations

import json
import logging
from contextlib import AsyncExitStack
from datetime import datetime
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.core.config import Settings
from app.integrations.google_calendar_mcp.calendar_models import (
    MCPEvent,
    MCPFreeBusyResponse,
)

logger = logging.getLogger(__name__)

# TODO: These tool names must exactly match the names registered by your MCP server.
# If you switch to a different Google Calendar MCP server implementation, update
# these constants to match its tool registration.
_TOOL_LIST_EVENTS = "list_events"
_TOOL_CREATE_EVENT = "create_event"
_TOOL_UPDATE_EVENT = "update_event"
_TOOL_DELETE_EVENT = "delete_event"
_TOOL_FREEBUSY = "freebusy"


class CalendarMCPError(Exception):
    """Raised when the MCP server returns a tool-level error response."""


class GoogleCalendarMCPClient:
    """Manages a persistent stdio connection to the Google Calendar MCP server.

    The server process is spawned on ``start()`` and terminated on ``stop()``.
    A single ``ClientSession`` is reused for all tool calls within the lifetime
    of the process.
    """

    def __init__(
        self,
        calendar_id: str,
        server_command: str,
        server_args: list[str],
        server_env: dict[str, str],
    ) -> None:
        self._calendar_id = calendar_id
        self._server_params = StdioServerParameters(
            command=server_command,
            args=server_args,
            env=server_env,
        )
        self._session: ClientSession | None = None
        self._exit_stack: AsyncExitStack | None = None

    @property
    def calendar_id(self) -> str:
        return self._calendar_id

    @classmethod
    def from_settings(cls, settings: Settings) -> "GoogleCalendarMCPClient":
        """Construct the client from application settings.

        TODO: Add the following fields to ``Settings`` and ``.env`` once you
        have completed the Google OAuth2 consent flow and obtained credentials.
        See https://developers.google.com/calendar/api/guides/auth for the setup
        steps::

            google_client_id: str = ""
            google_client_secret: str = ""
            google_refresh_token: str = ""

        Then uncomment the corresponding lines in ``server_env`` below.
        """
        server_env: dict[str, str] = {
            # TODO: populate from settings once OAuth credentials are configured
            # "GOOGLE_CLIENT_ID": settings.google_client_id,
            # "GOOGLE_CLIENT_SECRET": settings.google_client_secret,
            # "GOOGLE_REFRESH_TOKEN": settings.google_refresh_token,
        }
        return cls(
            calendar_id=settings.google_calendar_id,
            # TODO: update command/args to match your MCP server installation.
            # For the npm-based server:
            #   command="npx", args=["-y", "@anthropic-ai/mcp-server-google-calendar"]
            # For a local Python script:
            #   command="python", args=["-m", "your_mcp_server_module"]
            server_command="npx",
            server_args=["-y", "@anthropic-ai/mcp-server-google-calendar"],
            server_env=server_env,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the MCP server subprocess and initialize the session.

        Must be called once before any tool calls.  Calling again without
        ``stop()`` in between will raise a ``RuntimeError``.
        """
        if self._exit_stack is not None:
            raise RuntimeError("GoogleCalendarMCPClient is already started.")

        self._exit_stack = AsyncExitStack()
        read, write = await self._exit_stack.enter_async_context(
            stdio_client(self._server_params)
        )
        self._session = await self._exit_stack.enter_async_context(
            ClientSession(read, write)
        )
        await self._session.initialize()
        logger.info("Google Calendar MCP session initialized (calendar=%s)", self._calendar_id)

    async def stop(self) -> None:
        """Shut down the MCP server subprocess and release all resources."""
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
            self._exit_stack = None
            self._session = None
            logger.info("Google Calendar MCP session closed")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _require_session(self) -> ClientSession:
        if self._session is None:
            raise RuntimeError(
                "GoogleCalendarMCPClient is not started. "
                "Call ``await client.start()`` before making tool calls."
            )
        return self._session

    async def _call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Call an MCP tool and return the parsed JSON payload.

        Raises:
            CalendarMCPError: if the MCP server reports a tool-level error.
        """
        session = self._require_session()
        logger.debug("MCP tool call: %s %s", name, arguments)

        result = await session.call_tool(name, arguments)

        if result.isError:
            details = result.content[0].text if result.content else "(no details)"  # type: ignore[union-attr]
            raise CalendarMCPError(f"MCP tool {name!r} returned an error: {details}")

        if not result.content:
            return None

        raw: str = result.content[0].text  # type: ignore[union-attr]
        return json.loads(raw)

    # ------------------------------------------------------------------
    # Calendar tool wrappers
    # ------------------------------------------------------------------

    async def list_events(
        self,
        time_min: datetime,
        time_max: datetime,
    ) -> list[MCPEvent]:
        """List all events in ``[time_min, time_max)`` from the appointment calendar."""
        payload = await self._call_tool(
            _TOOL_LIST_EVENTS,
            {
                "calendarId": self._calendar_id,
                "timeMin": time_min.isoformat(),
                "timeMax": time_max.isoformat(),
                "singleEvents": True,
                "orderBy": "startTime",
            },
        )
        items: list[dict] = payload.get("items", []) if payload else []
        return [MCPEvent.model_validate(item) for item in items]

    async def create_event(
        self,
        start_at: datetime,
        end_at: datetime,
        title: str,
        description: str | None,
        timezone: str,
    ) -> MCPEvent:
        """Create a new calendar event and return the server-assigned MCPEvent."""
        payload = await self._call_tool(
            _TOOL_CREATE_EVENT,
            {
                "calendarId": self._calendar_id,
                "summary": title,
                "description": description,
                "start": {"dateTime": start_at.isoformat(), "timeZone": timezone},
                "end": {"dateTime": end_at.isoformat(), "timeZone": timezone},
            },
        )
        return MCPEvent.model_validate(payload)

    async def update_event(
        self,
        event_id: str,
        start_at: datetime,
        end_at: datetime,
        title: str | None,
        description: str | None,
        timezone: str,
    ) -> MCPEvent:
        """Update the time (and optionally title/description) of an existing event."""
        body: dict[str, Any] = {
            "start": {"dateTime": start_at.isoformat(), "timeZone": timezone},
            "end": {"dateTime": end_at.isoformat(), "timeZone": timezone},
        }
        if title is not None:
            body["summary"] = title
        if description is not None:
            body["description"] = description

        payload = await self._call_tool(
            _TOOL_UPDATE_EVENT,
            {
                "calendarId": self._calendar_id,
                "eventId": event_id,
                **body,
            },
        )
        return MCPEvent.model_validate(payload)

    async def delete_event(self, event_id: str) -> None:
        """Permanently delete a calendar event."""
        await self._call_tool(
            _TOOL_DELETE_EVENT,
            {
                "calendarId": self._calendar_id,
                "eventId": event_id,
            },
        )

    async def get_freebusy(
        self,
        time_min: datetime,
        time_max: datetime,
    ) -> MCPFreeBusyResponse:
        """Query the freebusy endpoint for the appointment calendar."""
        payload = await self._call_tool(
            _TOOL_FREEBUSY,
            {
                "timeMin": time_min.isoformat(),
                "timeMax": time_max.isoformat(),
                "items": [{"id": self._calendar_id}],
            },
        )
        return MCPFreeBusyResponse.model_validate(payload or {})
