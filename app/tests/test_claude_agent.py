"""Tests for the Claude agent integration layer.

All external dependencies (Anthropic API, DB, calendar) are mocked —
no real API calls or database connections are required.

Test coverage:
- AgentService: end-turn, tool-use loop, API error, max-iteration fallback
- ToolExecutor: known tool dispatch, unknown tool
- BookingTools: each tool with happy path and error conditions
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.core.config import Settings
from app.core.exceptions import (
    BookingConflictError,
    NoAppointmentError,
    SlotUnavailableError,
    TooManyAppointmentsError,
)
from app.db.models import Appointment, AppointmentStatus
from app.integrations.anthropic.agent_service import AgentService, _FALLBACK
from app.integrations.anthropic.claude_client import ClaudeClient
from app.integrations.google_calendar_mcp.calendar_adapter import CalendarEvent
from app.schemas.availability import BusyInterval, DaySlots, TimeSlot
from app.services.appointment_service import AppointmentService
from app.services.availability_service import AvailabilityService
from app.services.booking_rules_service import BookingRulesService
from app.tools.booking_tools import ToolContext, create_booking  # noqa: F401
from app.tools.booking_tools import (
    cancel_appointment,
    get_available_slots,
    get_my_appointment,
    reschedule_appointment,
)
from app.tools.tool_executor import execute_tool
from app.use_cases.deps import HandlerServices
from app.use_cases.handle_free_text_message import (
    HandleFreeTextMessageUseCase,
    _IN_FLOW_REPLY,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TZ = ZoneInfo("Asia/Almaty")
FIXED_NOW = datetime(2026, 4, 20, 8, 0, tzinfo=TZ)
VALID_SLOT = datetime(2026, 4, 21, 10, 0, tzinfo=TZ)
TG_USER_ID = 42


# ---------------------------------------------------------------------------
# Fake Claude response helpers
# ---------------------------------------------------------------------------


class _TextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class _ToolUseBlock:
    type = "tool_use"

    def __init__(self, name: str, tool_id: str, input: dict[str, Any]) -> None:
        self.name = name
        self.id = tool_id
        self.input = input


class _FakeMessage:
    def __init__(self, content: list[Any], stop_reason: str = "end_turn") -> None:
        self.content = content
        self.stop_reason = stop_reason


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _make_settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://test:test@localhost/test",
        telegram_bot_token="0:test",
        telegram_master_chat_id=12345,
    )


def _make_rules() -> BookingRulesService:
    return BookingRulesService(_make_settings())


def _make_appointment(
    start_at: datetime = VALID_SLOT,
    google_event_id: str = "evt-1",
) -> Appointment:
    appt = MagicMock(spec=Appointment)
    appt.id = uuid.uuid4()
    appt.google_event_id = google_event_id
    appt.start_at = start_at
    appt.end_at = start_at + timedelta(hours=1)
    appt.status = AppointmentStatus.confirmed
    return appt


def _make_client(tg_id: int = TG_USER_ID) -> MagicMock:
    client = MagicMock()
    client.id = uuid.uuid4()
    client.telegram_user_id = tg_id
    return client


def _make_tool_context(
    client: MagicMock | None = None,
    active_appt: Appointment | None = None,
    slots: list[TimeSlot] | None = None,
) -> ToolContext:
    rules = _make_rules()

    client_repo = MagicMock()
    client_repo.get_by_telegram_user_id = AsyncMock(return_value=client or _make_client())

    appt_svc = MagicMock(spec=AppointmentService)
    appt_svc.get_future_appointment_for_client = AsyncMock(return_value=active_appt)
    appt_svc.create_booking = AsyncMock(return_value=_make_appointment())
    appt_svc.cancel_booking = AsyncMock(return_value=_make_appointment())
    appt_svc.reschedule_booking = AsyncMock(return_value=_make_appointment())

    availability = MagicMock(spec=AvailabilityService)
    _slots = slots or [TimeSlot(start=VALID_SLOT, end=VALID_SLOT + timedelta(hours=1))]
    availability.get_available_slots = MagicMock(return_value=_slots)
    availability.get_available_slots_for_range = MagicMock(
        return_value=[DaySlots(date=VALID_SLOT.date(), slots=_slots)]
    )

    calendar = MagicMock()
    calendar.get_busy_intervals = AsyncMock(return_value=[])

    return ToolContext(
        telegram_user_id=TG_USER_ID,
        appointment_service=appt_svc,
        availability=availability,
        client_repo=client_repo,
        calendar=calendar,
        rules=rules,
    )


def _make_handler_services(ctx: ToolContext) -> HandlerServices:
    return HandlerServices(
        appointment_service=ctx.appointment_service,
        availability=ctx.availability,
        client_repo=ctx.client_repo,
        session_repo=MagicMock(),
        rules=ctx.rules,
        calendar=ctx.calendar,
    )


# ---------------------------------------------------------------------------
# AgentService
# ---------------------------------------------------------------------------


class TestAgentService:
    @pytest.mark.asyncio
    async def test_returns_text_on_end_turn(self):
        mock_client = MagicMock(spec=ClaudeClient)
        mock_client.complete = AsyncMock(
            return_value=_FakeMessage(
                content=[_TextBlock("Привет! Чем могу помочь?")],
                stop_reason="end_turn",
            )
        )
        svc = AgentService(client=mock_client)
        ctx = _make_tool_context()
        services = _make_handler_services(ctx)

        result = await svc.handle_message(TG_USER_ID, "привет", services)

        assert result == "Привет! Чем могу помочь?"
        mock_client.complete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_tool_use_loop_then_end_turn(self):
        """Claude calls get_my_appointment tool, receives result, then replies."""
        mock_client = MagicMock(spec=ClaudeClient)
        tool_block = _ToolUseBlock("get_my_appointment", "tu-1", {})

        mock_client.complete = AsyncMock(
            side_effect=[
                # First call: Claude requests a tool
                _FakeMessage(content=[tool_block], stop_reason="tool_use"),
                # Second call: Claude returns final answer
                _FakeMessage(
                    content=[_TextBlock("Ваша запись: 21.04.2026 в 10:00.")],
                    stop_reason="end_turn",
                ),
            ]
        )

        ctx = _make_tool_context(active_appt=_make_appointment())
        services = _make_handler_services(ctx)
        svc = AgentService(client=mock_client)

        result = await svc.handle_message(TG_USER_ID, "когда моя запись?", services)

        assert "10:00" in result or "21.04" in result
        assert mock_client.complete.await_count == 2

    @pytest.mark.asyncio
    async def test_returns_fallback_on_api_error(self):
        mock_client = MagicMock(spec=ClaudeClient)
        mock_client.complete = AsyncMock(side_effect=RuntimeError("API down"))

        svc = AgentService(client=mock_client)
        ctx = _make_tool_context()
        services = _make_handler_services(ctx)

        result = await svc.handle_message(TG_USER_ID, "запишите меня", services)

        assert result == _FALLBACK

    @pytest.mark.asyncio
    async def test_returns_fallback_after_max_iterations(self):
        """If Claude keeps requesting tools without stopping, fallback is returned."""
        tool_block = _ToolUseBlock("get_available_slots", "tu-1", {})
        mock_client = MagicMock(spec=ClaudeClient)
        # Always returns a tool_use response — never end_turn
        mock_client.complete = AsyncMock(
            return_value=_FakeMessage(content=[tool_block], stop_reason="tool_use")
        )

        svc = AgentService(client=mock_client)
        ctx = _make_tool_context()
        services = _make_handler_services(ctx)

        result = await svc.handle_message(TG_USER_ID, "запишите меня", services)

        assert result == _FALLBACK

    @pytest.mark.asyncio
    async def test_returns_fallback_when_end_turn_has_no_text(self):
        mock_client = MagicMock(spec=ClaudeClient)
        mock_client.complete = AsyncMock(
            return_value=_FakeMessage(content=[], stop_reason="end_turn")
        )
        svc = AgentService(client=mock_client)
        services = _make_handler_services(_make_tool_context())

        result = await svc.handle_message(TG_USER_ID, "?", services)

        assert result == _FALLBACK


# ---------------------------------------------------------------------------
# ToolExecutor
# ---------------------------------------------------------------------------


class TestToolExecutor:
    @pytest.mark.asyncio
    async def test_dispatches_known_tool(self):
        ctx = _make_tool_context(active_appt=None)
        result = await execute_tool("get_my_appointment", {}, ctx)
        # No active appointment → returns "нет активных записей"
        assert "нет" in result.lower()

    @pytest.mark.asyncio
    async def test_returns_error_string_for_unknown_tool(self):
        ctx = _make_tool_context()
        result = await execute_tool("nonexistent_tool", {}, ctx)
        assert "не найден" in result


# ---------------------------------------------------------------------------
# BookingTools: get_available_slots
# ---------------------------------------------------------------------------


class TestGetAvailableSlots:
    @pytest.mark.asyncio
    async def test_returns_slots_for_range_when_no_date(self):
        ctx = _make_tool_context()
        result = await get_available_slots({}, ctx)
        assert "10:00" in result

    @pytest.mark.asyncio
    async def test_returns_slots_for_specific_date(self):
        ctx = _make_tool_context()
        result = await get_available_slots({"date": VALID_SLOT.date().isoformat()}, ctx)
        assert "10:00" in result

    @pytest.mark.asyncio
    async def test_returns_no_slots_message_when_empty(self):
        ctx = _make_tool_context(slots=[])
        ctx.availability.get_available_slots = MagicMock(return_value=[])
        ctx.availability.get_available_slots_for_range = MagicMock(return_value=[])
        result = await get_available_slots({}, ctx)
        assert "нет" in result.lower()

    @pytest.mark.asyncio
    async def test_returns_error_on_invalid_date_format(self):
        ctx = _make_tool_context()
        result = await get_available_slots({"date": "not-a-date"}, ctx)
        assert "некорректный" in result.lower()


# ---------------------------------------------------------------------------
# BookingTools: get_my_appointment
# ---------------------------------------------------------------------------


class TestGetMyAppointment:
    @pytest.mark.asyncio
    async def test_returns_appointment_info(self):
        ctx = _make_tool_context(active_appt=_make_appointment())
        result = await get_my_appointment({}, ctx)
        assert "21.04.2026" in result
        assert "10:00" in result

    @pytest.mark.asyncio
    async def test_returns_no_appointment_message(self):
        ctx = _make_tool_context(active_appt=None)
        result = await get_my_appointment({}, ctx)
        assert "нет" in result.lower()

    @pytest.mark.asyncio
    async def test_returns_error_when_client_not_found(self):
        ctx = _make_tool_context()
        ctx.client_repo.get_by_telegram_user_id = AsyncMock(return_value=None)
        result = await get_my_appointment({}, ctx)
        assert "не найден" in result


# ---------------------------------------------------------------------------
# BookingTools: create_booking
# ---------------------------------------------------------------------------


class TestCreateBooking:
    @pytest.mark.asyncio
    async def test_creates_booking_successfully(self):
        ctx = _make_tool_context()
        result = await create_booking({"slot_start": VALID_SLOT.isoformat()}, ctx)
        assert "создана" in result
        assert "21.04.2026" in result

    @pytest.mark.asyncio
    async def test_returns_error_on_too_many_appointments(self):
        ctx = _make_tool_context()
        ctx.appointment_service.create_booking = AsyncMock(
            side_effect=TooManyAppointmentsError("already booked")
        )
        result = await create_booking({"slot_start": VALID_SLOT.isoformat()}, ctx)
        assert "уже есть" in result

    @pytest.mark.asyncio
    async def test_returns_error_on_slot_unavailable(self):
        ctx = _make_tool_context()
        ctx.appointment_service.create_booking = AsyncMock(
            side_effect=SlotUnavailableError("outside hours")
        )
        result = await create_booking({"slot_start": VALID_SLOT.isoformat()}, ctx)
        assert "недоступен" in result

    @pytest.mark.asyncio
    async def test_returns_error_on_booking_conflict(self):
        ctx = _make_tool_context()
        ctx.appointment_service.create_booking = AsyncMock(
            side_effect=BookingConflictError("overlap")
        )
        result = await create_booking({"slot_start": VALID_SLOT.isoformat()}, ctx)
        assert "уже есть запись" in result

    @pytest.mark.asyncio
    async def test_returns_error_on_invalid_slot_format(self):
        ctx = _make_tool_context()
        result = await create_booking({"slot_start": "not-a-datetime"}, ctx)
        assert "некорректный" in result.lower()

    @pytest.mark.asyncio
    async def test_returns_error_when_client_not_found(self):
        ctx = _make_tool_context()
        ctx.client_repo.get_by_telegram_user_id = AsyncMock(return_value=None)
        result = await create_booking({"slot_start": VALID_SLOT.isoformat()}, ctx)
        assert "не найден" in result


# ---------------------------------------------------------------------------
# BookingTools: cancel_appointment
# ---------------------------------------------------------------------------


class TestCancelAppointment:
    @pytest.mark.asyncio
    async def test_cancels_successfully(self):
        ctx = _make_tool_context()
        result = await cancel_appointment({}, ctx)
        assert "отменена" in result

    @pytest.mark.asyncio
    async def test_returns_error_when_no_appointment(self):
        ctx = _make_tool_context()
        ctx.appointment_service.cancel_booking = AsyncMock(
            side_effect=NoAppointmentError("none")
        )
        result = await cancel_appointment({}, ctx)
        assert "нет" in result.lower()

    @pytest.mark.asyncio
    async def test_passes_reason_to_service(self):
        ctx = _make_tool_context()
        await cancel_appointment({"reason": "передумал"}, ctx)
        ctx.appointment_service.cancel_booking.assert_awaited_once()
        kwargs = ctx.appointment_service.cancel_booking.call_args.kwargs
        assert kwargs.get("reason") == "передумал"


# ---------------------------------------------------------------------------
# BookingTools: reschedule_appointment
# ---------------------------------------------------------------------------


class TestRescheduleAppointment:
    @pytest.mark.asyncio
    async def test_reschedules_successfully(self):
        new_slot = datetime(2026, 4, 22, 11, 0, tzinfo=TZ)
        ctx = _make_tool_context()
        ctx.appointment_service.reschedule_booking = AsyncMock(
            return_value=_make_appointment(start_at=new_slot)
        )
        result = await reschedule_appointment({"new_slot_start": new_slot.isoformat()}, ctx)
        assert "перенесена" in result
        assert "22.04.2026" in result

    @pytest.mark.asyncio
    async def test_returns_error_when_no_appointment(self):
        ctx = _make_tool_context()
        ctx.appointment_service.reschedule_booking = AsyncMock(
            side_effect=NoAppointmentError("none")
        )
        new_slot = datetime(2026, 4, 22, 11, 0, tzinfo=TZ)
        result = await reschedule_appointment({"new_slot_start": new_slot.isoformat()}, ctx)
        assert "нет" in result.lower()

    @pytest.mark.asyncio
    async def test_returns_error_on_invalid_slot_format(self):
        ctx = _make_tool_context()
        result = await reschedule_appointment({"new_slot_start": "bad-format"}, ctx)
        assert "некорректный" in result.lower()

    @pytest.mark.asyncio
    async def test_returns_error_on_booking_conflict(self):
        ctx = _make_tool_context()
        ctx.appointment_service.reschedule_booking = AsyncMock(
            side_effect=BookingConflictError("overlap")
        )
        new_slot = datetime(2026, 4, 22, 11, 0, tzinfo=TZ)
        result = await reschedule_appointment({"new_slot_start": new_slot.isoformat()}, ctx)
        assert "уже есть запись" in result


# ---------------------------------------------------------------------------
# HandleFreeTextMessageUseCase
# ---------------------------------------------------------------------------


def _make_bot_session(state: str) -> MagicMock:
    s = MagicMock()
    s.current_state = state
    s.conversation_history = []
    return s


class TestHandleFreeTextMessageUseCase:
    @pytest.mark.asyncio
    async def test_delegates_to_agent_when_idle(self):
        from app.core import states

        mock_agent = MagicMock(spec=AgentService)
        mock_agent.handle_message = AsyncMock(return_value=("Ответ от Claude", []))

        ctx = _make_tool_context()
        services = _make_handler_services(ctx)
        services.session_repo.get_by_telegram_user_id = AsyncMock(
            return_value=_make_bot_session(states.IDLE)
        )

        uc = HandleFreeTextMessageUseCase(agent_service=mock_agent)
        reply, in_flow = await uc.execute(TG_USER_ID, "запишите меня", services)

        assert reply == "Ответ от Claude"
        assert in_flow is False
        mock_agent.handle_message.assert_awaited_once_with(TG_USER_ID, "запишите меня", services, history=[])

    @pytest.mark.asyncio
    async def test_returns_in_flow_reply_when_booking_in_progress(self):
        from app.core import states

        mock_agent = MagicMock(spec=AgentService)
        mock_agent.handle_message = AsyncMock(return_value="should not be called")

        ctx = _make_tool_context()
        services = _make_handler_services(ctx)
        services.session_repo.get_by_telegram_user_id = AsyncMock(
            return_value=_make_bot_session(states.BOOKING_SELECT_DATE)
        )

        uc = HandleFreeTextMessageUseCase(agent_service=mock_agent)
        reply, in_flow = await uc.execute(TG_USER_ID, "стоп", services)

        assert reply == _IN_FLOW_REPLY
        assert in_flow is True
        mock_agent.handle_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_in_flow_reply_when_cancel_confirm(self):
        from app.core import states

        mock_agent = MagicMock(spec=AgentService)
        mock_agent.handle_message = AsyncMock()

        ctx = _make_tool_context()
        services = _make_handler_services(ctx)
        services.session_repo.get_by_telegram_user_id = AsyncMock(
            return_value=_make_bot_session(states.CANCEL_CONFIRM)
        )

        uc = HandleFreeTextMessageUseCase(agent_service=mock_agent)
        reply, in_flow = await uc.execute(TG_USER_ID, "стоп", services)

        assert reply == _IN_FLOW_REPLY
        assert in_flow is True

    @pytest.mark.asyncio
    async def test_delegates_to_agent_when_no_session(self):
        mock_agent = MagicMock(spec=AgentService)
        mock_agent.handle_message = AsyncMock(return_value=("Привет", []))

        ctx = _make_tool_context()
        services = _make_handler_services(ctx)
        services.session_repo.get_by_telegram_user_id = AsyncMock(return_value=None)

        uc = HandleFreeTextMessageUseCase(agent_service=mock_agent)
        reply, in_flow = await uc.execute(TG_USER_ID, "привет", services)

        assert reply == "Привет"
        assert in_flow is False
        mock_agent.handle_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_proceeds_to_agent_when_session_read_fails(self):
        """DB error reading session must not block the agent call."""
        mock_agent = MagicMock(spec=AgentService)
        mock_agent.handle_message = AsyncMock(return_value=("Ответ", []))

        ctx = _make_tool_context()
        services = _make_handler_services(ctx)
        # First call (state check) raises; second call (history fetch) returns None
        services.session_repo.get_by_telegram_user_id = AsyncMock(
            side_effect=[RuntimeError("db error"), None]
        )

        uc = HandleFreeTextMessageUseCase(agent_service=mock_agent)
        reply, in_flow = await uc.execute(TG_USER_ID, "что-то", services)

        assert reply == "Ответ"
        assert in_flow is False
        mock_agent.handle_message.assert_awaited_once()
