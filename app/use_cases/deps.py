"""Service factory for Telegram handlers.

PTB handlers don't participate in FastAPI's dependency injection, so
services are constructed manually with an explicit database session.
"""

from __future__ import annotations

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


@dataclass
class HandlerServices:
    appointment_service: AppointmentService
    availability: AvailabilityService
    client_repo: ClientRepository
    session_repo: BotSessionRepository
    rules: BookingRulesService
    calendar: CalendarAdapter


def make_services(session: AsyncSession) -> HandlerServices:
    rules = BookingRulesService(settings)
    availability = AvailabilityService(rules, settings)
    calendar: CalendarAdapter = StubCalendarAdapter()
    appt_repo = AppointmentRepository(session)
    audit_repo = AuditLogRepository(session)
    client_repo = ClientRepository(session)
    session_repo = BotSessionRepository(session)
    notification = NotificationService(bot_client, settings.telegram_master_chat_id, rules.timezone)
    appt_svc = AppointmentService(calendar, rules, appt_repo, audit_repo, notification)
    return HandlerServices(
        appointment_service=appt_svc,
        availability=availability,
        client_repo=client_repo,
        session_repo=session_repo,
        rules=rules,
        calendar=calendar,
    )
