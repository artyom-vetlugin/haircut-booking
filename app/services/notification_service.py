"""NotificationService — sends Telegram notifications to the master.

Failures are logged but never re-raised so notification errors
cannot corrupt booking transactions.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_MONTHS_RU = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def _fmt_dt(dt: datetime, tz: ZoneInfo) -> str:
    local = dt.astimezone(tz)
    month = _MONTHS_RU[local.month - 1]
    return f"{local.day} {month} в {local.strftime('%H:%M')}"


def _client_ref(actor_id: str) -> str:
    """Return an HTML Telegram link when actor_id is a numeric user ID."""
    if actor_id.isdigit():
        return f'<a href="tg://user?id={actor_id}">Клиент</a>'
    return actor_id


class NotificationService:
    """Sends booking event notifications to the master via Telegram."""

    def __init__(
        self,
        bot_client: Any,
        master_chat_id: int,
        timezone: ZoneInfo,
    ) -> None:
        self._bot = bot_client
        self._master_chat_id = master_chat_id
        self._tz = timezone

    async def notify_booking_created(
        self,
        start_at: datetime,
        actor_id: str,
    ) -> None:
        text = (
            "📋 <b>Новая запись</b>\n"
            f"{_client_ref(actor_id)}\n"
            f"Время: {_fmt_dt(start_at, self._tz)}"
        )
        await self._send(text)

    async def notify_booking_rescheduled(
        self,
        old_start_at: datetime,
        new_start_at: datetime,
        actor_id: str,
    ) -> None:
        text = (
            "🔄 <b>Перенос записи</b>\n"
            f"{_client_ref(actor_id)}\n"
            f"Было: {_fmt_dt(old_start_at, self._tz)}\n"
            f"Стало: {_fmt_dt(new_start_at, self._tz)}"
        )
        await self._send(text)

    async def notify_booking_cancelled(
        self,
        start_at: datetime,
        actor_id: str,
    ) -> None:
        text = (
            "❌ <b>Отмена записи</b>\n"
            f"{_client_ref(actor_id)}\n"
            f"Время: {_fmt_dt(start_at, self._tz)}"
        )
        await self._send(text)

    async def _send(self, text: str) -> None:
        try:
            await self._bot.send_message(
                chat_id=self._master_chat_id,
                text=text,
                parse_mode="HTML",
            )
        except Exception:
            logger.exception(
                "Failed to send master notification to chat_id=%s",
                self._master_chat_id,
            )
