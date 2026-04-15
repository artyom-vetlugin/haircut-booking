"""Tests for the Telegram booking flow: keyboards, formatters, stub adapter,
and handler state transitions.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.core import states
from app.integrations.google_calendar_mcp.stub_adapter import StubCalendarAdapter
from app.integrations.telegram.keyboards import (
    confirm_keyboard,
    dates_keyboard,
    format_date_ru,
    format_time,
    slots_keyboard,
)
from app.schemas.availability import BusyInterval, DaySlots, TimeSlot

TZ = ZoneInfo("Asia/Almaty")


# ── Formatting helpers ────────────────────────────────────────────────────────


class TestFormatDateRu:
    def test_monday(self) -> None:
        assert format_date_ru(date(2026, 4, 13)) == "Пн 13 апр"

    def test_wednesday(self) -> None:
        assert format_date_ru(date(2026, 4, 15)) == "Ср 15 апр"

    def test_saturday(self) -> None:
        assert format_date_ru(date(2026, 4, 18)) == "Сб 18 апр"

    def test_december(self) -> None:
        assert format_date_ru(date(2026, 12, 1)) == "Вт 1 дек"


class TestFormatTime:
    def test_hour_and_minute(self) -> None:
        dt = datetime(2026, 4, 20, 10, 30, tzinfo=TZ)
        assert format_time(dt) == "10:30"

    def test_zero_minutes(self) -> None:
        dt = datetime(2026, 4, 20, 9, 0, tzinfo=TZ)
        assert format_time(dt) == "09:00"


# ── Keyboard builders ─────────────────────────────────────────────────────────


def _make_day_slots(d: date, hour: int = 10) -> DaySlots:
    slot = TimeSlot(
        start=datetime(d.year, d.month, d.day, hour, tzinfo=TZ),
        end=datetime(d.year, d.month, d.day, hour + 1, tzinfo=TZ),
    )
    return DaySlots(date=d, slots=[slot])


def _all_callbacks(kb) -> list[str]:
    return [btn.callback_data for row in kb.inline_keyboard for btn in row]


class TestDatesKeyboard:
    def test_single_date_has_correct_callback(self) -> None:
        ds = _make_day_slots(date(2026, 4, 20))
        kb = dates_keyboard([ds], "book_date")
        assert "book_date:2026-04-20" in _all_callbacks(kb)

    def test_always_has_cancel_button(self) -> None:
        kb = dates_keyboard([], "book_date")
        assert "flow_back" in _all_callbacks(kb)

    def test_two_dates_per_row(self) -> None:
        day_slots = [_make_day_slots(date(2026, 4, 20 + i)) for i in range(4)]
        kb = dates_keyboard(day_slots, "book_date")
        # First 4 date buttons in 2 rows of 2
        assert len(kb.inline_keyboard[0]) == 2
        assert len(kb.inline_keyboard[1]) == 2

    def test_reschedule_prefix(self) -> None:
        ds = _make_day_slots(date(2026, 4, 22))
        kb = dates_keyboard([ds], "res_date")
        assert "res_date:2026-04-22" in _all_callbacks(kb)


class TestSlotsKeyboard:
    def test_slot_callbacks_contain_iso(self) -> None:
        slots = [
            TimeSlot(
                start=datetime(2026, 4, 20, 10, tzinfo=TZ),
                end=datetime(2026, 4, 20, 11, tzinfo=TZ),
            ),
            TimeSlot(
                start=datetime(2026, 4, 20, 11, tzinfo=TZ),
                end=datetime(2026, 4, 20, 12, tzinfo=TZ),
            ),
        ]
        kb = slots_keyboard(slots, "book_slot")
        cbs = _all_callbacks(kb)
        assert any("book_slot:" in cb for cb in cbs)
        assert any("T10:00:00" in cb for cb in cbs)

    def test_back_button_present(self) -> None:
        kb = slots_keyboard([], "book_slot")
        assert "flow_back" in _all_callbacks(kb)

    def test_three_slots_per_row(self) -> None:
        slots = [
            TimeSlot(
                start=datetime(2026, 4, 20, 9 + i, tzinfo=TZ),
                end=datetime(2026, 4, 20, 10 + i, tzinfo=TZ),
            )
            for i in range(6)
        ]
        kb = slots_keyboard(slots, "book_slot")
        assert len(kb.inline_keyboard[0]) == 3
        assert len(kb.inline_keyboard[1]) == 3


class TestConfirmKeyboard:
    def test_has_confirm_callback(self) -> None:
        kb = confirm_keyboard("book_confirm")
        assert "book_confirm" in _all_callbacks(kb)

    def test_has_default_back_callback(self) -> None:
        kb = confirm_keyboard("book_confirm")
        assert "flow_back" in _all_callbacks(kb)

    def test_custom_back_callback(self) -> None:
        kb = confirm_keyboard("cancel_confirm", back_cb="cancel_back")
        assert "cancel_back" in _all_callbacks(kb)


# ── StubCalendarAdapter ───────────────────────────────────────────────────────


class TestStubCalendarAdapter:
    @pytest.mark.asyncio
    async def test_create_returns_event_with_uuid(self) -> None:
        adapter = StubCalendarAdapter()
        start = datetime(2026, 4, 20, 10, tzinfo=TZ)
        end = datetime(2026, 4, 20, 11, tzinfo=TZ)
        event = await adapter.create_event(start, end, "Стрижка")
        assert event.event_id != ""
        # Should be a valid UUID
        uuid.UUID(event.event_id)
        assert event.start_at == start
        assert event.end_at == end

    @pytest.mark.asyncio
    async def test_create_returns_different_ids_each_call(self) -> None:
        adapter = StubCalendarAdapter()
        start = datetime(2026, 4, 20, 10, tzinfo=TZ)
        end = datetime(2026, 4, 20, 11, tzinfo=TZ)
        e1 = await adapter.create_event(start, end, "Стрижка")
        e2 = await adapter.create_event(start, end, "Стрижка")
        assert e1.event_id != e2.event_id

    @pytest.mark.asyncio
    async def test_update_preserves_event_id(self) -> None:
        adapter = StubCalendarAdapter()
        start = datetime(2026, 4, 20, 10, tzinfo=TZ)
        end = datetime(2026, 4, 20, 11, tzinfo=TZ)
        event = await adapter.update_event("existing-id", start, end)
        assert event.event_id == "existing-id"

    @pytest.mark.asyncio
    async def test_delete_is_noop(self) -> None:
        adapter = StubCalendarAdapter()
        await adapter.delete_event("any-id")  # should not raise

    @pytest.mark.asyncio
    async def test_get_busy_intervals_returns_empty(self) -> None:
        adapter = StubCalendarAdapter()
        start = datetime(2026, 4, 20, 0, tzinfo=TZ)
        end = datetime(2026, 4, 20, 23, tzinfo=TZ)
        intervals = await adapter.get_busy_intervals(start, end)
        assert intervals == []


# ── Handler state-transition tests ───────────────────────────────────────────
#
# These tests call handler functions directly, mocking the DB layer so no
# real database connection is needed.


def _make_tg_user(user_id: int = 100, first_name: str = "Иван") -> MagicMock:
    user = MagicMock()
    user.id = user_id
    user.first_name = first_name
    user.last_name = None
    user.username = "ivan"
    return user


def _make_update_message(user_id: int = 100) -> MagicMock:
    update = MagicMock()
    update.effective_user = _make_tg_user(user_id)
    update.message = AsyncMock()
    update.message.reply_text = AsyncMock()
    return update


def _make_callback_query_update(user_id: int = 100, data: str = "") -> MagicMock:
    update = MagicMock()
    query = AsyncMock()
    query.from_user = _make_tg_user(user_id)
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update.callback_query = query
    return update


def _make_mock_svc(
    client_id: uuid.UUID | None = None,
    existing_appt=None,
    day_slots=None,
    bot_session_state: str | None = None,
    bot_session_draft: dict | None = None,
) -> MagicMock:
    """Build a mock HandlerServices with sensible defaults."""
    svc = MagicMock()
    cid = client_id or uuid.uuid4()

    client = MagicMock()
    client.id = cid

    svc.client_repo.get_by_telegram_user_id = AsyncMock(return_value=client)
    svc.client_repo.create = AsyncMock(return_value=client)

    svc.appointment_service.get_future_appointment_for_client = AsyncMock(
        return_value=existing_appt
    )
    svc.appointment_service.create_booking = AsyncMock()
    svc.appointment_service.reschedule_booking = AsyncMock()
    svc.appointment_service.cancel_booking = AsyncMock()

    svc.calendar.get_busy_intervals = AsyncMock(return_value=[])

    svc.availability.get_available_slots_for_range = MagicMock(
        return_value=day_slots if day_slots is not None else []
    )
    svc.availability.get_available_slots = MagicMock(return_value=[])

    if bot_session_state is not None:
        session_obj = MagicMock()
        session_obj.current_state = bot_session_state
        session_obj.draft_payload = bot_session_draft or {}
        svc.session_repo.get_by_telegram_user_id = AsyncMock(return_value=session_obj)
    else:
        svc.session_repo.get_by_telegram_user_id = AsyncMock(return_value=None)
    svc.session_repo.upsert = AsyncMock()

    return svc


def _patch_db(svc: MagicMock):
    """Return a context-manager patch that injects *svc* via make_services."""

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def begin(self):
            return self

    return patch(
        "app.integrations.telegram.handlers.AsyncSessionLocal",
        return_value=FakeSession(),
    ), patch(
        "app.integrations.telegram.handlers.make_services",
        return_value=svc,
    )


class TestHandleMyAppointment:
    @pytest.mark.asyncio
    async def test_no_appointment_shows_correct_message(self) -> None:
        from app.integrations.telegram.handlers import handle_my_appointment
        from app.integrations.telegram import messages as msg

        update = _make_update_message()
        svc = _make_mock_svc(existing_appt=None)
        db_patch, svc_patch = _patch_db(svc)
        with db_patch, svc_patch:
            await handle_my_appointment(update, MagicMock())

        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        assert msg.NO_APPOINTMENT in text

    @pytest.mark.asyncio
    async def test_existing_appointment_shows_formatted_info(self) -> None:
        from app.integrations.telegram.handlers import handle_my_appointment
        from app.integrations.telegram import messages as msg

        appt = MagicMock()
        appt.start_at = datetime(2026, 4, 20, 10, tzinfo=TZ)

        update = _make_update_message()
        svc = _make_mock_svc(existing_appt=appt)
        db_patch, svc_patch = _patch_db(svc)
        with db_patch, svc_patch:
            await handle_my_appointment(update, MagicMock())

        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        assert "10:00" in text
        assert "апр" in text


class TestHandleBook:
    @pytest.mark.asyncio
    async def test_already_has_appointment_shows_warning(self) -> None:
        from app.integrations.telegram.handlers import handle_book
        from app.integrations.telegram import messages as msg

        appt = MagicMock()
        appt.start_at = datetime(2026, 4, 25, 14, tzinfo=TZ)

        update = _make_update_message()
        svc = _make_mock_svc(existing_appt=appt)
        db_patch, svc_patch = _patch_db(svc)
        with db_patch, svc_patch:
            await handle_book(update, MagicMock())

        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        # Should mention the existing appointment
        assert "14:00" in text

    @pytest.mark.asyncio
    async def test_no_slots_shows_no_slots_message(self) -> None:
        from app.integrations.telegram.handlers import handle_book
        from app.integrations.telegram import messages as msg

        update = _make_update_message()
        svc = _make_mock_svc(existing_appt=None, day_slots=[])
        db_patch, svc_patch = _patch_db(svc)
        with db_patch, svc_patch:
            await handle_book(update, MagicMock())

        update.message.reply_text.assert_called_once()
        text = update.message.reply_text.call_args[0][0]
        assert msg.NO_SLOTS_AVAILABLE in text

    @pytest.mark.asyncio
    async def test_available_slots_shows_date_keyboard(self) -> None:
        from app.integrations.telegram.handlers import handle_book

        day_slots = [_make_day_slots(date(2026, 4, 21))]
        update = _make_update_message()
        svc = _make_mock_svc(existing_appt=None, day_slots=day_slots)
        db_patch, svc_patch = _patch_db(svc)
        with db_patch, svc_patch:
            await handle_book(update, MagicMock())

        update.message.reply_text.assert_called_once()
        call_kwargs = update.message.reply_text.call_args
        # The reply_markup should be an InlineKeyboardMarkup
        markup = call_kwargs[1]["reply_markup"]
        assert markup is not None
        all_cbs = _all_callbacks(markup)
        assert "book_date:2026-04-21" in all_cbs


class TestHandleCancelAppointment:
    @pytest.mark.asyncio
    async def test_no_appointment_shows_no_appointment_message(self) -> None:
        from app.integrations.telegram.handlers import handle_cancel_appointment
        from app.integrations.telegram import messages as msg

        update = _make_update_message()
        svc = _make_mock_svc(existing_appt=None)
        db_patch, svc_patch = _patch_db(svc)
        with db_patch, svc_patch:
            await handle_cancel_appointment(update, MagicMock())

        text = update.message.reply_text.call_args[0][0]
        assert msg.NO_APPOINTMENT in text

    @pytest.mark.asyncio
    async def test_has_appointment_shows_confirm_keyboard(self) -> None:
        from app.integrations.telegram.handlers import handle_cancel_appointment

        appt = MagicMock()
        appt.start_at = datetime(2026, 4, 22, 11, tzinfo=TZ)

        update = _make_update_message()
        svc = _make_mock_svc(existing_appt=appt)
        db_patch, svc_patch = _patch_db(svc)
        with db_patch, svc_patch:
            await handle_cancel_appointment(update, MagicMock())

        call_kwargs = update.message.reply_text.call_args
        markup = call_kwargs[1]["reply_markup"]
        assert "cancel_confirm" in _all_callbacks(markup)
        assert svc.session_repo.upsert.call_args[0][1] == states.CANCEL_CONFIRM


class TestHandleReschedule:
    @pytest.mark.asyncio
    async def test_no_appointment_shows_no_appointment_message(self) -> None:
        from app.integrations.telegram.handlers import handle_reschedule
        from app.integrations.telegram import messages as msg

        update = _make_update_message()
        svc = _make_mock_svc(existing_appt=None)
        db_patch, svc_patch = _patch_db(svc)
        with db_patch, svc_patch:
            await handle_reschedule(update, MagicMock())

        text = update.message.reply_text.call_args[0][0]
        assert msg.NO_APPOINTMENT in text

    @pytest.mark.asyncio
    async def test_has_appointment_shows_reschedule_prompt_with_dates(self) -> None:
        from app.integrations.telegram.handlers import handle_reschedule

        appt = MagicMock()
        appt.start_at = datetime(2026, 4, 22, 11, tzinfo=TZ)
        day_slots = [_make_day_slots(date(2026, 4, 25))]

        update = _make_update_message()
        svc = _make_mock_svc(existing_appt=appt, day_slots=day_slots)
        db_patch, svc_patch = _patch_db(svc)
        with db_patch, svc_patch:
            await handle_reschedule(update, MagicMock())

        call_kwargs = update.message.reply_text.call_args
        markup = call_kwargs[1]["reply_markup"]
        assert "res_date:2026-04-25" in _all_callbacks(markup)


class TestCallbackDispatch:
    @pytest.mark.asyncio
    async def test_flow_back_resets_to_idle(self) -> None:
        from app.integrations.telegram.handlers import handle_callback

        update = _make_callback_query_update(data="flow_back")
        svc = _make_mock_svc()
        db_patch, svc_patch = _patch_db(svc)
        with db_patch, svc_patch:
            await handle_callback(update, MagicMock())

        update.callback_query.answer.assert_called_once()
        # State should be reset to IDLE
        svc.session_repo.upsert.assert_called_once()
        assert svc.session_repo.upsert.call_args[0][1] == states.IDLE

    @pytest.mark.asyncio
    async def test_book_slot_updates_state_to_confirm(self) -> None:
        from app.integrations.telegram.handlers import handle_callback

        slot_iso = "2026-04-20T10:00:00+05:00"
        update = _make_callback_query_update(data=f"book_slot:{slot_iso}")
        svc = _make_mock_svc(
            bot_session_state=states.BOOKING_SELECT_SLOT,
            bot_session_draft={"date": "2026-04-20"},
        )
        db_patch, svc_patch = _patch_db(svc)
        with db_patch, svc_patch:
            await handle_callback(update, MagicMock())

        # Should have saved BOOKING_CONFIRM state with slot_start in draft
        upsert_args = svc.session_repo.upsert.call_args[0]
        assert upsert_args[1] == states.BOOKING_CONFIRM
        assert upsert_args[2]["slot_start"] == slot_iso

        # Confirmation message should contain the time
        update.callback_query.edit_message_text.assert_called_once()
        text = update.callback_query.edit_message_text.call_args[0][0]
        assert "10:00" in text

    @pytest.mark.asyncio
    async def test_cancel_confirm_calls_cancel_service(self) -> None:
        from app.integrations.telegram.handlers import handle_callback
        from app.integrations.telegram import messages as msg

        update = _make_callback_query_update(data="cancel_confirm")
        svc = _make_mock_svc(bot_session_state=states.CANCEL_CONFIRM)
        db_patch, svc_patch = _patch_db(svc)
        with db_patch, svc_patch:
            await handle_callback(update, MagicMock())

        svc.appointment_service.cancel_booking.assert_called_once()
        update.callback_query.edit_message_text.assert_called_once()
        text = update.callback_query.edit_message_text.call_args[0][0]
        assert msg.CANCEL_SUCCESS in text

    @pytest.mark.asyncio
    async def test_cancel_confirm_flow_expired_when_wrong_state(self) -> None:
        from app.integrations.telegram.handlers import handle_callback
        from app.integrations.telegram import messages as msg

        update = _make_callback_query_update(data="cancel_confirm")
        # Session is in IDLE, not CANCEL_CONFIRM
        svc = _make_mock_svc(bot_session_state=states.IDLE)
        db_patch, svc_patch = _patch_db(svc)
        with db_patch, svc_patch:
            await handle_callback(update, MagicMock())

        text = update.callback_query.edit_message_text.call_args[0][0]
        assert msg.FLOW_EXPIRED in text
        svc.appointment_service.cancel_booking.assert_not_called()
