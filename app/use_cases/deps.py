"""Service factory for Telegram handlers.

PTB handlers don't participate in FastAPI's dependency injection, so
services are constructed manually with an explicit database session.

Calendar adapter lifecycle
--------------------------
The module-level ``_calendar_adapter`` defaults to ``StubCalendarAdapter``
(safe for tests and development without credentials).  Call
``initialize_calendar_adapter()`` early in the app lifespan to replace it
with ``GoogleCalendarMCPAdapter`` when ``google_calendar_id`` is configured.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.integrations.google_calendar_mcp.calendar_adapter import CalendarAdapter
from app.integrations.google_calendar_mcp.stub_adapter import StubCalendarAdapter
from app.integrations.telegram.client import bot_client
from app.repositories.appointment import AppointmentRepository
from app.repositories.audit_log import AuditLogRepository
from app.repositories.bot_session import BotSessionRepository
from app.repositories.client import ClientRepository
from app.services.appointment_service import AppointmentService
from app.services.availability_service import AvailabilityService
from app.services.booking_rules_service import BookingRulesService
from app.services.notification_service import NotificationService

logger = logging.getLogger(__name__)

# Default to stub so tests and dev environments work without credentials.
# Replaced at startup by initialize_calendar_adapter() when configured.
_calendar_adapter: CalendarAdapter = StubCalendarAdapter()
_mcp_client = None


def initialize_calendar_adapter() -> None:
    """Swap in the real GoogleCalendarMCPAdapter if google_calendar_id is set.

    Must be called during app startup (before requests arrive) so that
    make_services() uses the correct adapter and get_mcp_client() returns
    the client that the lifespan needs to start/stop.
    """
    global _calendar_adapter, _mcp_client

    if not settings.google_calendar_id:
        logger.info("google_calendar_id not set — using StubCalendarAdapter")
        return

    from app.integrations.google_calendar_mcp.mcp_adapter import GoogleCalendarMCPAdapter
    from app.integrations.google_calendar_mcp.mcp_client import GoogleCalendarMCPClient

    _mcp_client = GoogleCalendarMCPClient.from_settings(settings)
    _calendar_adapter = GoogleCalendarMCPAdapter(_mcp_client, timezone=settings.app_timezone)
    logger.info("Using GoogleCalendarMCPAdapter (calendar_id=%s)", settings.google_calendar_id)


def get_mcp_client():  # type: ignore[return]
    """Return the GoogleCalendarMCPClient singleton, or None if using stub adapter."""
    return _mcp_client


@dataclass
class HandlerServices:
    appointment_service: AppointmentService
    availability: AvailabilityService
    client_repo: ClientRepository
    session_repo: BotSessionRepository
    rules: BookingRulesService
    calendar: CalendarAdapter


def build_client_label(client: object) -> str:
    """Return the client's display name for use in calendar event titles."""
    name = " ".join(
        p for p in [getattr(client, "first_name", None), getattr(client, "last_name", None)]
        if isinstance(p, str) and p
    )
    if name:
        return name
    username = getattr(client, "telegram_username", None)
    if isinstance(username, str) and username:
        return f"@{username}"
    return f"tg:{getattr(client, 'telegram_user_id', '?')}"


def build_event_description(client: object) -> str | None:
    """Return a structured calendar event description with contact details."""
    lines: list[str] = []
    username = getattr(client, "telegram_username", None)
    tg_id = getattr(client, "telegram_user_id", None)
    if isinstance(username, str) and username:
        lines.append(f"Telegram: @{username}")
    if tg_id is not None:
        lines.append(f"Написать: tg://user?id={tg_id}")
    phone = getattr(client, "phone_number", None)
    if isinstance(phone, str) and phone:
        lines.append(f"Телефон: {phone}")
    return "\n".join(lines) if lines else None


async def get_or_create_client(
    svc: HandlerServices,
    telegram_user_id: int,
    first_name: str | None,
    last_name: str | None,
    username: str | None,
):
    """Return the Client for a Telegram user, creating one if not found."""
    client = await svc.client_repo.get_by_telegram_user_id(telegram_user_id)
    if client is None:
        client = await svc.client_repo.create(
            telegram_user_id=telegram_user_id,
            first_name=first_name,
            last_name=last_name,
            telegram_username=username,
        )
    return client


def make_services(session: AsyncSession) -> HandlerServices:
    rules = BookingRulesService(settings)
    availability = AvailabilityService(rules, settings)
    appt_repo = AppointmentRepository(session)
    audit_repo = AuditLogRepository(session)
    client_repo = ClientRepository(session)
    session_repo = BotSessionRepository(session)
    notification = NotificationService(bot_client, settings.telegram_master_chat_id, rules.timezone)
    appt_svc = AppointmentService(_calendar_adapter, rules, appt_repo, audit_repo, notification)
    return HandlerServices(
        appointment_service=appt_svc,
        availability=availability,
        client_repo=client_repo,
        session_repo=session_repo,
        rules=rules,
        calendar=_calendar_adapter,
    )
