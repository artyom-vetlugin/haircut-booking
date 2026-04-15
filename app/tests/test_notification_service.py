"""Unit tests for NotificationService."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.services.notification_service import NotificationService, _client_ref, _fmt_dt

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


class TestClientRef:
    def test_numeric_id_produces_html_link(self):
        ref = _client_ref("12345678")
        assert "tg://user?id=12345678" in ref
        assert "<a href=" in ref

    def test_non_numeric_id_returned_as_is(self):
        assert _client_ref("system") == "system"


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
