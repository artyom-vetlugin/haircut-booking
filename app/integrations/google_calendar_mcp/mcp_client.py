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

import asyncio
import json
import logging
from contextlib import AsyncExitStack
from datetime import datetime, timezone
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.core.config import Settings
from app.integrations.google_calendar_mcp.calendar_models import (
    MCPEvent,
    MCPFreeBusyResponse,
)

logger = logging.getLogger(__name__)

_CALL_TOOL_MAX_ATTEMPTS = 3
_CALL_TOOL_BASE_DELAY = 1.0  # seconds; doubled on each retry

# Tool names registered by @cocal/google-calendar-mcp.
_TOOL_LIST_EVENTS = "list-events"
_TOOL_CREATE_EVENT = "create-event"
_TOOL_UPDATE_EVENT = "update-event"
_TOOL_DELETE_EVENT = "delete-event"
_TOOL_FREEBUSY = "get-freebusy"


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
        account: str = "normal",
    ) -> None:
        self._calendar_id = calendar_id
        self._account = account
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

        TODO: Before using the real adapter:
        1. Create OAuth 2.0 "Desktop app" credentials in Google Cloud Console and
           download the JSON file (gcp-oauth.keys.json).
        2. Run the one-time auth flow to generate tokens::

               GOOGLE_OAUTH_CREDENTIALS=/path/to/gcp-oauth.keys.json \\
                   npx @cocal/google-calendar-mcp auth

        3. Set GOOGLE_OAUTH_CREDENTIALS in .env to that absolute path.
        4. Uncomment the env var line in ``server_env`` below.

        See https://github.com/nspady/google-calendar-mcp for full setup steps.
        """
        server_env: dict[str, str] = {}
        if settings.google_oauth_credentials_path:
            server_env["GOOGLE_OAUTH_CREDENTIALS"] = settings.google_oauth_credentials_path
        return cls(
            calendar_id=settings.google_calendar_id,
            server_command="npx",
            server_args=["-y", "@cocal/google-calendar-mcp"],
            server_env=server_env,
            account=settings.google_calendar_account,
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

        Retries up to _CALL_TOOL_MAX_ATTEMPTS times on transient transport errors.
        CalendarMCPError (tool-level application errors) are never retried.

        Raises:
            CalendarMCPError: if the MCP server reports a tool-level error.
        """
        session = self._require_session()
        last_exc: Exception | None = None
        delay = _CALL_TOOL_BASE_DELAY

        for attempt in range(1, _CALL_TOOL_MAX_ATTEMPTS + 1):
            try:
                logger.debug("MCP tool call: %s (attempt %d/%d)", name, attempt, _CALL_TOOL_MAX_ATTEMPTS)
                result = await session.call_tool(name, arguments)

                if result.isError:
                    details = result.content[0].text if result.content else "(no details)"  # type: ignore[union-attr]
                    raise CalendarMCPError(f"MCP tool {name!r} returned an error: {details}")

                if not result.content:
                    return None

                raw: str = result.content[0].text  # type: ignore[union-attr]
                return json.loads(raw)

            except CalendarMCPError:
                # Tool-level application error — retrying won't help.
                raise
            except Exception as exc:
                last_exc = exc
                if attempt < _CALL_TOOL_MAX_ATTEMPTS:
                    logger.warning(
                        "MCP tool call %s failed (attempt %d/%d), retrying in %.1fs: %s",
                        name, attempt, _CALL_TOOL_MAX_ATTEMPTS, delay, exc,
                    )
                    await asyncio.sleep(delay)
                    delay *= 2

        raise last_exc  # type: ignore[misc]

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
            },
        )
        items: list[dict] = payload.get("events", []) if payload else []
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
                "account": self._account,
                "summary": title,
                "description": description or "",
                "start": start_at.isoformat(),
                "end": end_at.isoformat(),
            },
        )
        return MCPEvent.model_validate(payload["event"])

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
            "start": start_at.isoformat(),
            "end": end_at.isoformat(),
        }
        if title is not None:
            body["summary"] = title
        if description is not None:
            body["description"] = description

        payload = await self._call_tool(
            _TOOL_UPDATE_EVENT,
            {
                "calendarId": self._calendar_id,
                "account": self._account,
                "eventId": event_id,
                **body,
            },
        )
        return MCPEvent.model_validate(payload["event"])

    async def delete_event(self, event_id: str) -> None:
        """Permanently delete a calendar event."""
        await self._call_tool(
            _TOOL_DELETE_EVENT,
            {
                "calendarId": self._calendar_id,
                "account": self._account,
                "eventId": event_id,
            },
        )

    async def get_freebusy(
        self,
        time_min: datetime,
        time_max: datetime,
    ) -> MCPFreeBusyResponse:
        """Query the freebusy endpoint for the appointment calendar."""
        def _to_utc_naive(dt: datetime) -> str:
            """Convert to UTC and strip offset so the MCP tool accepts it."""
            if dt.tzinfo is not None:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt.strftime("%Y-%m-%dT%H:%M:%S")

        payload = await self._call_tool(
            _TOOL_FREEBUSY,
            {
                "calendars": [{"id": self._calendar_id}],
                "timeMin": _to_utc_naive(time_min),
                "timeMax": _to_utc_naive(time_max),
            },
        )
        return MCPFreeBusyResponse.model_validate(payload or {})
