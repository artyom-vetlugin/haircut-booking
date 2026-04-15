"""End-to-end style tests for the Telegram booking state machine.

Exercises the complete multi-step callback chains that are NOT covered by
test_booking_flows.py:

  Book:       book_date → book_confirm (+ all error variants)
  Reschedule: res_date → res_slot → res_confirm (+ all error variants)

All DB and service interactions are replaced with mocks.
No real database, calendar, or Telegram connection is used.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.core import states
from app.core.exceptions import (
    BookingConflictError,
    CalendarSyncError,
    NoAppointmentError,
    SlotUnavailableError,
    TooManyAppointmentsError,
)
from app.integrations.telegram import messages as msg
from app.schemas.availability import TimeSlot

TZ = ZoneInfo("Asia/Almaty")


# ---------------------------------------------------------------------------
# Shared test utilities (mirrors the pattern in test_booking_flows.py)
# ---------------------------------------------------------------------------


def _make_tg_user(user_id: int = 100) -> MagicMock:
    user = MagicMock()
    user.id = user_id
    user.first_name = "Тест"
    user.last_name = None
    user.username = "test_user"
    return user


def _make_callback_query(user_id: int = 100, data: str = "") -> MagicMock:
    query = AsyncMock()
    query.from_user = _make_tg_user(user_id)
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    return query


def _make_callback_update(user_id: int = 100, data: str = "") -> MagicMock:
    update = MagicMock()
    update.callback_query = _make_callback_query(user_id, data)
    return update


def _make_slot(hour: int = 10, day: int = 21) -> TimeSlot:
    return TimeSlot(
        start=datetime(2026, 4, day, hour, tzinfo=TZ),
        end=datetime(2026, 4, day, hour + 1, tzinfo=TZ),
    )


def _make_svc(
    slots: list | None = None,
    bot_session_state: str | None = None,
    bot_session_draft: dict | None = None,
) -> MagicMock:
    """Return a mock HandlerServices with minimal wiring for callback tests."""
    svc = MagicMock()
    client = MagicMock()
    client.id = uuid.uuid4()

    svc.client_repo.get_by_telegram_user_id = AsyncMock(return_value=client)
    svc.client_repo.create = AsyncMock(return_value=client)

    svc.appointment_service.create_booking = AsyncMock()
    svc.appointment_service.reschedule_booking = AsyncMock()
    svc.appointment_service.cancel_booking = AsyncMock()

    svc.calendar.get_busy_intervals = AsyncMock(return_value=[])
    svc.availability.get_available_slots = MagicMock(
        return_value=slots if slots is not None else []
    )

    if bot_session_state is not None:
        session_obj = MagicMock()
        session_obj.current_state = bot_session_state
        session_obj.draft_payload = bot_session_draft or {}
        svc.session_repo.get_by_telegram_user_id = AsyncMock(return_value=session_obj)
    else:
        svc.session_repo.get_by_telegram_user_id = AsyncMock(return_value=None)

    svc.session_repo.upsert = AsyncMock()
    return svc


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    def begin(self):
        return self


def _patches(svc: MagicMock, *, mock_reset_repo: bool = False):
    """
    Return the context-manager patches needed for handler tests.

    When *mock_reset_repo* is True, also patch BotSessionRepository so that
    error-path calls to _reset_user_state() don't fail on a bare FakeSession.
    """
    plist = [
        patch(
            "app.integrations.telegram.handlers.AsyncSessionLocal",
            return_value=_FakeSession(),
        ),
        patch(
            "app.integrations.telegram.handlers.make_services",
            return_value=svc,
        ),
    ]
    if mock_reset_repo:
        mock_repo = MagicMock()
        mock_repo.upsert = AsyncMock()
        plist.append(
            patch(
                "app.integrations.telegram.handlers.BotSessionRepository",
                return_value=mock_repo,
            )
        )
    return plist


# ---------------------------------------------------------------------------
# book_date callback — step 2 of the book flow
# ---------------------------------------------------------------------------


class TestBookDateCallback:
    @pytest.mark.asyncio
    async def test_shows_slot_picker_when_slots_available(self) -> None:
        from app.integrations.telegram.handlers import handle_callback

        svc = _make_svc(
            slots=[_make_slot(10), _make_slot(11)],
            bot_session_state=states.BOOKING_SELECT_DATE,
        )
        update = _make_callback_update(data="book_date:2026-04-21")

        with _patches(svc)[0], _patches(svc)[1]:
            await handle_callback(update, MagicMock())

        update.callback_query.edit_message_text.assert_called_once()
        markup = update.callback_query.edit_message_text.call_args[1]["reply_markup"]
        assert markup is not None

    @pytest.mark.asyncio
    async def test_advances_state_to_booking_select_slot(self) -> None:
        from app.integrations.telegram.handlers import handle_callback

        svc = _make_svc(
            slots=[_make_slot(10)],
            bot_session_state=states.BOOKING_SELECT_DATE,
        )
        update = _make_callback_update(data="book_date:2026-04-21")

        with _patches(svc)[0], _patches(svc)[1]:
            await handle_callback(update, MagicMock())

        upsert_args = svc.session_repo.upsert.call_args[0]
        assert upsert_args[1] == states.BOOKING_SELECT_SLOT
        assert upsert_args[2]["date"] == "2026-04-21"

    @pytest.mark.asyncio
    async def test_shows_no_slots_message_when_date_is_fully_booked(self) -> None:
        from app.integrations.telegram.handlers import handle_callback

        svc = _make_svc(slots=[], bot_session_state=states.BOOKING_SELECT_DATE)
        update = _make_callback_update(data="book_date:2026-04-21")

        with _patches(svc)[0], _patches(svc)[1]:
            await handle_callback(update, MagicMock())

        text = update.callback_query.edit_message_text.call_args[0][0]
        assert msg.NO_SLOTS_AVAILABLE in text

    @pytest.mark.asyncio
    async def test_shows_flow_expired_when_state_is_not_select_date(self) -> None:
        from app.integrations.telegram.handlers import handle_callback

        svc = _make_svc(bot_session_state=states.IDLE)
        update = _make_callback_update(data="book_date:2026-04-21")

        with _patches(svc)[0], _patches(svc)[1]:
            await handle_callback(update, MagicMock())

        text = update.callback_query.edit_message_text.call_args[0][0]
        assert msg.FLOW_EXPIRED in text
        svc.session_repo.upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_shows_flow_expired_when_no_session(self) -> None:
        from app.integrations.telegram.handlers import handle_callback

        svc = _make_svc(bot_session_state=None)
        update = _make_callback_update(data="book_date:2026-04-21")

        with _patches(svc)[0], _patches(svc)[1]:
            await handle_callback(update, MagicMock())

        text = update.callback_query.edit_message_text.call_args[0][0]
        assert msg.FLOW_EXPIRED in text

    @pytest.mark.asyncio
    async def test_slot_keyboard_uses_book_slot_prefix(self) -> None:
        from app.integrations.telegram.handlers import handle_callback

        svc = _make_svc(
            slots=[_make_slot(14)],
            bot_session_state=states.BOOKING_SELECT_DATE,
        )
        update = _make_callback_update(data="book_date:2026-04-21")

        with _patches(svc)[0], _patches(svc)[1]:
            await handle_callback(update, MagicMock())

        markup = update.callback_query.edit_message_text.call_args[1]["reply_markup"]
        all_cbs = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        assert any(cb.startswith("book_slot:") for cb in all_cbs)


# ---------------------------------------------------------------------------
# book_confirm callback — step 4: final booking confirmation
# ---------------------------------------------------------------------------


class TestBookConfirmCallback:
    def _booked_appt(self) -> MagicMock:
        appt = MagicMock()
        appt.start_at = datetime(2026, 4, 21, 10, tzinfo=TZ)
        return appt

    @pytest.mark.asyncio
    async def test_creates_booking_and_shows_success_message(self) -> None:
        from app.integrations.telegram.handlers import handle_callback

        appt = self._booked_appt()
        svc = _make_svc(
            bot_session_state=states.BOOKING_CONFIRM,
            bot_session_draft={"slot_start": "2026-04-21T10:00:00+05:00"},
        )
        svc.appointment_service.create_booking = AsyncMock(return_value=appt)
        update = _make_callback_update(data="book_confirm")

        with _patches(svc)[0], _patches(svc)[1]:
            await handle_callback(update, MagicMock())

        svc.appointment_service.create_booking.assert_awaited_once()
        text = update.callback_query.edit_message_text.call_args[0][0]
        # Success message contains the formatted date and time
        assert "10:00" in text

    @pytest.mark.asyncio
    async def test_resets_state_to_idle_on_success(self) -> None:
        from app.integrations.telegram.handlers import handle_callback

        appt = self._booked_appt()
        svc = _make_svc(
            bot_session_state=states.BOOKING_CONFIRM,
            bot_session_draft={"slot_start": "2026-04-21T10:00:00+05:00"},
        )
        svc.appointment_service.create_booking = AsyncMock(return_value=appt)
        update = _make_callback_update(data="book_confirm")

        with _patches(svc)[0], _patches(svc)[1]:
            await handle_callback(update, MagicMock())

        # Last upsert call must set IDLE
        last_upsert = svc.session_repo.upsert.call_args_list[-1]
        assert last_upsert[0][1] == states.IDLE

    @pytest.mark.asyncio
    async def test_too_many_appointments_shows_already_booked_message(self) -> None:
        from app.integrations.telegram.handlers import handle_callback

        svc = _make_svc(
            bot_session_state=states.BOOKING_CONFIRM,
            bot_session_draft={"slot_start": "2026-04-21T10:00:00+05:00"},
        )
        svc.appointment_service.create_booking = AsyncMock(
            side_effect=TooManyAppointmentsError("already has booking")
        )
        update = _make_callback_update(data="book_confirm")

        with _patches(svc, mock_reset_repo=True)[0], _patches(svc, mock_reset_repo=True)[1], _patches(svc, mock_reset_repo=True)[2]:
            await handle_callback(update, MagicMock())

        text = update.callback_query.edit_message_text.call_args[0][0]
        assert msg.ALREADY_BOOKED in text

    @pytest.mark.asyncio
    async def test_booking_conflict_shows_conflict_message(self) -> None:
        from app.integrations.telegram.handlers import handle_callback

        svc = _make_svc(
            bot_session_state=states.BOOKING_CONFIRM,
            bot_session_draft={"slot_start": "2026-04-21T10:00:00+05:00"},
        )
        svc.appointment_service.create_booking = AsyncMock(
            side_effect=BookingConflictError("conflict")
        )
        update = _make_callback_update(data="book_confirm")

        with _patches(svc, mock_reset_repo=True)[0], _patches(svc, mock_reset_repo=True)[1], _patches(svc, mock_reset_repo=True)[2]:
            await handle_callback(update, MagicMock())

        text = update.callback_query.edit_message_text.call_args[0][0]
        assert msg.BOOKING_CONFLICT_MSG in text

    @pytest.mark.asyncio
    async def test_slot_unavailable_shows_slot_gone_message(self) -> None:
        from app.integrations.telegram.handlers import handle_callback

        svc = _make_svc(
            bot_session_state=states.BOOKING_CONFIRM,
            bot_session_draft={"slot_start": "2026-04-21T10:00:00+05:00"},
        )
        svc.appointment_service.create_booking = AsyncMock(
            side_effect=SlotUnavailableError("slot gone")
        )
        update = _make_callback_update(data="book_confirm")

        with _patches(svc, mock_reset_repo=True)[0], _patches(svc, mock_reset_repo=True)[1], _patches(svc, mock_reset_repo=True)[2]:
            await handle_callback(update, MagicMock())

        text = update.callback_query.edit_message_text.call_args[0][0]
        assert msg.SLOT_NO_LONGER_AVAILABLE in text

    @pytest.mark.asyncio
    async def test_calendar_error_shows_safe_calendar_message(self) -> None:
        from app.integrations.telegram.handlers import handle_callback

        svc = _make_svc(
            bot_session_state=states.BOOKING_CONFIRM,
            bot_session_draft={"slot_start": "2026-04-21T10:00:00+05:00"},
        )
        svc.appointment_service.create_booking = AsyncMock(
            side_effect=CalendarSyncError("google down")
        )
        update = _make_callback_update(data="book_confirm")

        with _patches(svc, mock_reset_repo=True)[0], _patches(svc, mock_reset_repo=True)[1], _patches(svc, mock_reset_repo=True)[2]:
            await handle_callback(update, MagicMock())

        text = update.callback_query.edit_message_text.call_args[0][0]
        assert msg.CALENDAR_ERROR in text

    @pytest.mark.asyncio
    async def test_flow_expired_when_state_is_not_booking_confirm(self) -> None:
        from app.integrations.telegram.handlers import handle_callback

        svc = _make_svc(bot_session_state=states.IDLE)
        update = _make_callback_update(data="book_confirm")

        with _patches(svc)[0], _patches(svc)[1]:
            await handle_callback(update, MagicMock())

        text = update.callback_query.edit_message_text.call_args[0][0]
        assert msg.FLOW_EXPIRED in text
        svc.appointment_service.create_booking.assert_not_called()

    @pytest.mark.asyncio
    async def test_flow_expired_when_slot_start_missing_from_draft(self) -> None:
        from app.integrations.telegram.handlers import handle_callback

        svc = _make_svc(
            bot_session_state=states.BOOKING_CONFIRM,
            bot_session_draft={},  # no slot_start
        )
        update = _make_callback_update(data="book_confirm")

        with _patches(svc)[0], _patches(svc)[1]:
            await handle_callback(update, MagicMock())

        text = update.callback_query.edit_message_text.call_args[0][0]
        assert msg.FLOW_EXPIRED in text
        svc.appointment_service.create_booking.assert_not_called()


# ---------------------------------------------------------------------------
# res_date callback — step 2 of the reschedule flow
# ---------------------------------------------------------------------------


class TestResDateCallback:
    @pytest.mark.asyncio
    async def test_shows_slot_picker_and_advances_state(self) -> None:
        from app.integrations.telegram.handlers import handle_callback

        svc = _make_svc(
            slots=[_make_slot(14)],
            bot_session_state=states.RESCHEDULE_SELECT_DATE,
        )
        update = _make_callback_update(data="res_date:2026-04-22")

        with _patches(svc)[0], _patches(svc)[1]:
            await handle_callback(update, MagicMock())

        upsert_args = svc.session_repo.upsert.call_args[0]
        assert upsert_args[1] == states.RESCHEDULE_SELECT_SLOT
        assert upsert_args[2]["date"] == "2026-04-22"

    @pytest.mark.asyncio
    async def test_slot_keyboard_uses_res_slot_prefix(self) -> None:
        from app.integrations.telegram.handlers import handle_callback

        svc = _make_svc(
            slots=[_make_slot(14)],
            bot_session_state=states.RESCHEDULE_SELECT_DATE,
        )
        update = _make_callback_update(data="res_date:2026-04-22")

        with _patches(svc)[0], _patches(svc)[1]:
            await handle_callback(update, MagicMock())

        markup = update.callback_query.edit_message_text.call_args[1]["reply_markup"]
        all_cbs = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        assert any(cb.startswith("res_slot:") for cb in all_cbs)

    @pytest.mark.asyncio
    async def test_shows_no_slots_when_date_fully_booked(self) -> None:
        from app.integrations.telegram.handlers import handle_callback

        svc = _make_svc(slots=[], bot_session_state=states.RESCHEDULE_SELECT_DATE)
        update = _make_callback_update(data="res_date:2026-04-22")

        with _patches(svc)[0], _patches(svc)[1]:
            await handle_callback(update, MagicMock())

        text = update.callback_query.edit_message_text.call_args[0][0]
        assert msg.NO_SLOTS_AVAILABLE in text

    @pytest.mark.asyncio
    async def test_flow_expired_when_state_is_not_select_date(self) -> None:
        from app.integrations.telegram.handlers import handle_callback

        svc = _make_svc(bot_session_state=states.BOOKING_SELECT_DATE)
        update = _make_callback_update(data="res_date:2026-04-22")

        with _patches(svc)[0], _patches(svc)[1]:
            await handle_callback(update, MagicMock())

        text = update.callback_query.edit_message_text.call_args[0][0]
        assert msg.FLOW_EXPIRED in text


# ---------------------------------------------------------------------------
# res_slot callback — step 3 of the reschedule flow
# ---------------------------------------------------------------------------


class TestResSlotCallback:
    @pytest.mark.asyncio
    async def test_advances_state_to_reschedule_confirm(self) -> None:
        from app.integrations.telegram.handlers import handle_callback

        slot_iso = "2026-04-22T14:00:00+05:00"
        svc = _make_svc(
            bot_session_state=states.RESCHEDULE_SELECT_SLOT,
            bot_session_draft={"date": "2026-04-22"},
        )
        update = _make_callback_update(data=f"res_slot:{slot_iso}")

        with _patches(svc)[0], _patches(svc)[1]:
            await handle_callback(update, MagicMock())

        upsert_args = svc.session_repo.upsert.call_args[0]
        assert upsert_args[1] == states.RESCHEDULE_CONFIRM
        assert upsert_args[2]["slot_start"] == slot_iso

    @pytest.mark.asyncio
    async def test_confirmation_message_contains_selected_time(self) -> None:
        from app.integrations.telegram.handlers import handle_callback

        slot_iso = "2026-04-22T14:00:00+05:00"
        svc = _make_svc(
            bot_session_state=states.RESCHEDULE_SELECT_SLOT,
            bot_session_draft={"date": "2026-04-22"},
        )
        update = _make_callback_update(data=f"res_slot:{slot_iso}")

        with _patches(svc)[0], _patches(svc)[1]:
            await handle_callback(update, MagicMock())

        text = update.callback_query.edit_message_text.call_args[0][0]
        assert "14:00" in text

    @pytest.mark.asyncio
    async def test_shows_flow_expired_when_wrong_state(self) -> None:
        from app.integrations.telegram.handlers import handle_callback

        slot_iso = "2026-04-22T14:00:00+05:00"
        svc = _make_svc(bot_session_state=states.RESCHEDULE_SELECT_DATE)
        update = _make_callback_update(data=f"res_slot:{slot_iso}")

        with _patches(svc)[0], _patches(svc)[1]:
            await handle_callback(update, MagicMock())

        text = update.callback_query.edit_message_text.call_args[0][0]
        assert msg.FLOW_EXPIRED in text

    @pytest.mark.asyncio
    async def test_confirm_keyboard_has_res_confirm_button(self) -> None:
        from app.integrations.telegram.handlers import handle_callback

        slot_iso = "2026-04-22T14:00:00+05:00"
        svc = _make_svc(
            bot_session_state=states.RESCHEDULE_SELECT_SLOT,
            bot_session_draft={"date": "2026-04-22"},
        )
        update = _make_callback_update(data=f"res_slot:{slot_iso}")

        with _patches(svc)[0], _patches(svc)[1]:
            await handle_callback(update, MagicMock())

        markup = update.callback_query.edit_message_text.call_args[1]["reply_markup"]
        all_cbs = [btn.callback_data for row in markup.inline_keyboard for btn in row]
        assert "res_confirm" in all_cbs


# ---------------------------------------------------------------------------
# res_confirm callback — step 4: final reschedule confirmation
# ---------------------------------------------------------------------------


class TestResConfirmCallback:
    def _rescheduled_appt(self) -> MagicMock:
        appt = MagicMock()
        appt.start_at = datetime(2026, 4, 22, 14, tzinfo=TZ)
        return appt

    @pytest.mark.asyncio
    async def test_reschedules_booking_and_shows_success_message(self) -> None:
        from app.integrations.telegram.handlers import handle_callback

        appt = self._rescheduled_appt()
        svc = _make_svc(
            bot_session_state=states.RESCHEDULE_CONFIRM,
            bot_session_draft={"slot_start": "2026-04-22T14:00:00+05:00"},
        )
        svc.appointment_service.reschedule_booking = AsyncMock(return_value=appt)
        update = _make_callback_update(data="res_confirm")

        with _patches(svc)[0], _patches(svc)[1]:
            await handle_callback(update, MagicMock())

        svc.appointment_service.reschedule_booking.assert_awaited_once()
        text = update.callback_query.edit_message_text.call_args[0][0]
        assert "14:00" in text

    @pytest.mark.asyncio
    async def test_resets_state_to_idle_on_success(self) -> None:
        from app.integrations.telegram.handlers import handle_callback

        appt = self._rescheduled_appt()
        svc = _make_svc(
            bot_session_state=states.RESCHEDULE_CONFIRM,
            bot_session_draft={"slot_start": "2026-04-22T14:00:00+05:00"},
        )
        svc.appointment_service.reschedule_booking = AsyncMock(return_value=appt)
        update = _make_callback_update(data="res_confirm")

        with _patches(svc)[0], _patches(svc)[1]:
            await handle_callback(update, MagicMock())

        last_upsert = svc.session_repo.upsert.call_args_list[-1]
        assert last_upsert[0][1] == states.IDLE

    @pytest.mark.asyncio
    async def test_no_appointment_error_shows_no_appointment_message(self) -> None:
        from app.integrations.telegram.handlers import handle_callback

        svc = _make_svc(
            bot_session_state=states.RESCHEDULE_CONFIRM,
            bot_session_draft={"slot_start": "2026-04-22T14:00:00+05:00"},
        )
        svc.appointment_service.reschedule_booking = AsyncMock(
            side_effect=NoAppointmentError("no appt")
        )
        update = _make_callback_update(data="res_confirm")

        with _patches(svc, mock_reset_repo=True)[0], _patches(svc, mock_reset_repo=True)[1], _patches(svc, mock_reset_repo=True)[2]:
            await handle_callback(update, MagicMock())

        text = update.callback_query.edit_message_text.call_args[0][0]
        assert msg.NO_APPOINTMENT in text

    @pytest.mark.asyncio
    async def test_booking_conflict_shows_conflict_message(self) -> None:
        from app.integrations.telegram.handlers import handle_callback

        svc = _make_svc(
            bot_session_state=states.RESCHEDULE_CONFIRM,
            bot_session_draft={"slot_start": "2026-04-22T14:00:00+05:00"},
        )
        svc.appointment_service.reschedule_booking = AsyncMock(
            side_effect=BookingConflictError("conflict")
        )
        update = _make_callback_update(data="res_confirm")

        with _patches(svc, mock_reset_repo=True)[0], _patches(svc, mock_reset_repo=True)[1], _patches(svc, mock_reset_repo=True)[2]:
            await handle_callback(update, MagicMock())

        text = update.callback_query.edit_message_text.call_args[0][0]
        assert msg.BOOKING_CONFLICT_MSG in text

    @pytest.mark.asyncio
    async def test_slot_unavailable_shows_slot_gone_message(self) -> None:
        from app.integrations.telegram.handlers import handle_callback

        svc = _make_svc(
            bot_session_state=states.RESCHEDULE_CONFIRM,
            bot_session_draft={"slot_start": "2026-04-22T14:00:00+05:00"},
        )
        svc.appointment_service.reschedule_booking = AsyncMock(
            side_effect=SlotUnavailableError("too late")
        )
        update = _make_callback_update(data="res_confirm")

        with _patches(svc, mock_reset_repo=True)[0], _patches(svc, mock_reset_repo=True)[1], _patches(svc, mock_reset_repo=True)[2]:
            await handle_callback(update, MagicMock())

        text = update.callback_query.edit_message_text.call_args[0][0]
        assert msg.SLOT_NO_LONGER_AVAILABLE in text

    @pytest.mark.asyncio
    async def test_calendar_error_shows_safe_message(self) -> None:
        from app.integrations.telegram.handlers import handle_callback

        svc = _make_svc(
            bot_session_state=states.RESCHEDULE_CONFIRM,
            bot_session_draft={"slot_start": "2026-04-22T14:00:00+05:00"},
        )
        svc.appointment_service.reschedule_booking = AsyncMock(
            side_effect=CalendarSyncError("google down")
        )
        update = _make_callback_update(data="res_confirm")

        with _patches(svc, mock_reset_repo=True)[0], _patches(svc, mock_reset_repo=True)[1], _patches(svc, mock_reset_repo=True)[2]:
            await handle_callback(update, MagicMock())

        text = update.callback_query.edit_message_text.call_args[0][0]
        assert msg.CALENDAR_ERROR in text

    @pytest.mark.asyncio
    async def test_flow_expired_when_state_is_not_reschedule_confirm(self) -> None:
        from app.integrations.telegram.handlers import handle_callback

        svc = _make_svc(bot_session_state=states.IDLE)
        update = _make_callback_update(data="res_confirm")

        with _patches(svc)[0], _patches(svc)[1]:
            await handle_callback(update, MagicMock())

        text = update.callback_query.edit_message_text.call_args[0][0]
        assert msg.FLOW_EXPIRED in text
        svc.appointment_service.reschedule_booking.assert_not_called()

    @pytest.mark.asyncio
    async def test_flow_expired_when_slot_missing_from_draft(self) -> None:
        from app.integrations.telegram.handlers import handle_callback

        svc = _make_svc(
            bot_session_state=states.RESCHEDULE_CONFIRM,
            bot_session_draft={},  # no slot_start
        )
        update = _make_callback_update(data="res_confirm")

        with _patches(svc)[0], _patches(svc)[1]:
            await handle_callback(update, MagicMock())

        text = update.callback_query.edit_message_text.call_args[0][0]
        assert msg.FLOW_EXPIRED in text
        svc.appointment_service.reschedule_booking.assert_not_called()
