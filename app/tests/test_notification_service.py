"""Unit tests for NotificationService."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.services.notification_service import NotificationService, _client_line, _fmt_dt

TZ = ZoneInfo("Asia/Almaty")
SLOT = datetime(2026, 4, 21, 10, 0, tzinfo=TZ)
OLD_SLOT = datetime(2026, 4, 20, 14, 0, tzinfo=TZ)
MASTER_CHAT_ID = 99999


def _make_service() -> tuple[NotificationService, AsyncMock]:
    bot = MagicMock()
    bot.send_message = AsyncMock()
    svc = NotificationService(bot_client=bot, master_chat_id=MASTER_CHAT_ID, timezone=TZ)
    return svc, bot.send_message


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


class TestFmtDt:
    def test_formats_russian_date(self):
        result = _fmt_dt(SLOT, TZ)
        assert "апреля" in result
        assert "10:00" in result
        assert "21" in result

    def test_formats_day_and_time(self):
        dt = datetime(2026, 12, 31, 9, 30, tzinfo=TZ)
        result = _fmt_dt(dt, TZ)
        assert "декабря" in result
        assert "09:30" in result


class TestClientLine:
    def test_numeric_id_produces_html_link(self):
        line = _client_line("12345678", "Иван", None)
        assert "tg://user?id=12345678" in line
        assert "Иван" in line

    def test_shows_name_and_phone(self):
        line = _client_line("12345678", "Мария", "+7 999 123 45 67")
        assert "Мария" in line
        assert "+7 999 123 45 67" in line

    def test_fallback_name_when_none(self):
        line = _client_line("12345678", None, None)
        assert "Клиент" in line

    def test_non_numeric_actor_id(self):
        line = _client_line("master:99", "Иван", None)
        assert "<a href=" not in line
        assert "Иван" in line


# ---------------------------------------------------------------------------
# notify_booking_created
# ---------------------------------------------------------------------------


class TestNotifyBookingCreated:
    @pytest.mark.asyncio
    async def test_sends_message_to_master(self):
        svc, send_message = _make_service()
        await svc.notify_booking_created(start_at=SLOT, actor_id="42")

        send_message.assert_awaited_once()
        kwargs = send_message.call_args.kwargs
        assert kwargs["chat_id"] == MASTER_CHAT_ID
        assert "Новая запись" in kwargs["text"]
        assert "апреля" in kwargs["text"]
        assert "10:00" in kwargs["text"]
        assert kwargs["parse_mode"] == "HTML"

    @pytest.mark.asyncio
    async def test_includes_name_and_phone(self):
        svc, send_message = _make_service()
        await svc.notify_booking_created(
            start_at=SLOT, actor_id="42",
            client_name="Иван", client_phone="+7 999 000 00 00",
        )
        text = send_message.call_args.kwargs["text"]
        assert "Иван" in text
        assert "+7 999 000 00 00" in text

    @pytest.mark.asyncio
    async def test_swallows_send_failure(self):
        svc, send_message = _make_service()
        send_message.side_effect = RuntimeError("telegram down")

        # Must not raise
        await svc.notify_booking_created(start_at=SLOT, actor_id="42")


# ---------------------------------------------------------------------------
# notify_booking_rescheduled
# ---------------------------------------------------------------------------


class TestNotifyBookingRescheduled:
    @pytest.mark.asyncio
    async def test_sends_message_with_old_and_new_times(self):
        svc, send_message = _make_service()
        await svc.notify_booking_rescheduled(
            old_start_at=OLD_SLOT, new_start_at=SLOT, actor_id="42"
        )

        send_message.assert_awaited_once()
        text = send_message.call_args.kwargs["text"]
        assert "Перенос" in text
        assert "14:00" in text   # old slot time
        assert "10:00" in text   # new slot time

    @pytest.mark.asyncio
    async def test_swallows_send_failure(self):
        svc, send_message = _make_service()
        send_message.side_effect = RuntimeError("telegram down")

        await svc.notify_booking_rescheduled(
            old_start_at=OLD_SLOT, new_start_at=SLOT, actor_id="42"
        )


# ---------------------------------------------------------------------------
# notify_booking_cancelled
# ---------------------------------------------------------------------------


class TestNotifyBookingCancelled:
    @pytest.mark.asyncio
    async def test_sends_cancellation_message(self):
        svc, send_message = _make_service()
        await svc.notify_booking_cancelled(start_at=SLOT, actor_id="42")

        send_message.assert_awaited_once()
        text = send_message.call_args.kwargs["text"]
        assert "Отмена" in text
        assert "10:00" in text

    @pytest.mark.asyncio
    async def test_swallows_send_failure(self):
        svc, send_message = _make_service()
        send_message.side_effect = RuntimeError("telegram down")

        await svc.notify_booking_cancelled(start_at=SLOT, actor_id="42")


# ---------------------------------------------------------------------------
# notify_client_booking_cancelled
# ---------------------------------------------------------------------------


CLIENT_CHAT_ID = 777888


class TestNotifyClientBookingCancelled:
    @pytest.mark.asyncio
    async def test_sends_message_to_client(self):
        svc, send_message = _make_service()
        await svc.notify_client_booking_cancelled(
            client_telegram_id=CLIENT_CHAT_ID, start_at=SLOT
        )

        send_message.assert_awaited_once()
        kwargs = send_message.call_args.kwargs
        assert kwargs["chat_id"] == CLIENT_CHAT_ID
        assert "отменена мастером" in kwargs["text"]
        assert "10:00" in kwargs["text"]

    @pytest.mark.asyncio
    async def test_noop_when_telegram_id_is_none(self):
        svc, send_message = _make_service()
        await svc.notify_client_booking_cancelled(
            client_telegram_id=None, start_at=SLOT
        )

        send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_swallows_send_failure(self):
        svc, send_message = _make_service()
        send_message.side_effect = RuntimeError("telegram down")

        # Must not raise
        await svc.notify_client_booking_cancelled(
            client_telegram_id=CLIENT_CHAT_ID, start_at=SLOT
        )


# ---------------------------------------------------------------------------
# notify_client_booking_rescheduled
# ---------------------------------------------------------------------------


class TestNotifyClientBookingRescheduled:
    @pytest.mark.asyncio
    async def test_sends_message_to_client(self):
        svc, send_message = _make_service()
        await svc.notify_client_booking_rescheduled(
            client_telegram_id=CLIENT_CHAT_ID,
            old_start_at=OLD_SLOT,
            new_start_at=SLOT,
        )

        send_message.assert_awaited_once()
        kwargs = send_message.call_args.kwargs
        assert kwargs["chat_id"] == CLIENT_CHAT_ID
        assert "перенёс" in kwargs["text"]
        assert "14:00" in kwargs["text"]  # old slot
        assert "10:00" in kwargs["text"]  # new slot

    @pytest.mark.asyncio
    async def test_noop_when_telegram_id_is_none(self):
        svc, send_message = _make_service()
        await svc.notify_client_booking_rescheduled(
            client_telegram_id=None,
            old_start_at=OLD_SLOT,
            new_start_at=SLOT,
        )

        send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_swallows_send_failure(self):
        svc, send_message = _make_service()
        send_message.side_effect = RuntimeError("telegram down")

        await svc.notify_client_booking_rescheduled(
            client_telegram_id=CLIENT_CHAT_ID,
            old_start_at=OLD_SLOT,
            new_start_at=SLOT,
        )
