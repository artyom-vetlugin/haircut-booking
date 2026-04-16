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


def _client_line(actor_id: str, client_name: str | None, client_phone: str | None) -> str:
    """Return a formatted client line with name, phone, and optional Telegram link."""
    name = client_name or "Клиент"
    if actor_id.isdigit():
        label = f'<a href="tg://user?id={actor_id}">{name}</a>'
    else:
        label = name
    if client_phone:
        return f"{label}\nТел: {client_phone}"
    return label


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
        client_name: str | None = None,
        client_phone: str | None = None,
    ) -> None:
        text = (
            "📋 <b>Новая запись</b>\n"
            f"{_client_line(actor_id, client_name, client_phone)}\n"
            f"Время: {_fmt_dt(start_at, self._tz)}"
        )
        await self._send(text)

    async def notify_booking_rescheduled(
        self,
        old_start_at: datetime,
        new_start_at: datetime,
        actor_id: str,
        client_name: str | None = None,
        client_phone: str | None = None,
    ) -> None:
        text = (
            "🔄 <b>Перенос записи</b>\n"
            f"{_client_line(actor_id, client_name, client_phone)}\n"
            f"Было: {_fmt_dt(old_start_at, self._tz)}\n"
            f"Стало: {_fmt_dt(new_start_at, self._tz)}"
        )
        await self._send(text)

    async def notify_booking_cancelled(
        self,
        start_at: datetime,
        actor_id: str,
        client_name: str | None = None,
        client_phone: str | None = None,
    ) -> None:
        text = (
            "❌ <b>Отмена записи</b>\n"
            f"{_client_line(actor_id, client_name, client_phone)}\n"
            f"Время: {_fmt_dt(start_at, self._tz)}"
        )
        await self._send(text)

    async def notify_client_booking_cancelled(
        self,
        client_telegram_id: int | None,
        start_at: datetime,
    ) -> None:
        if client_telegram_id is None:
            return
        from app.integrations.telegram.messages import CLIENT_APPOINTMENT_CANCELLED_BY_MASTER
        text = CLIENT_APPOINTMENT_CANCELLED_BY_MASTER.format(dt=_fmt_dt(start_at, self._tz))
        await self._send_to(client_telegram_id, text)

    async def notify_client_booking_rescheduled(
        self,
        client_telegram_id: int | None,
        old_start_at: datetime,
        new_start_at: datetime,
    ) -> None:
        if client_telegram_id is None:
            return
        from app.integrations.telegram.messages import CLIENT_APPOINTMENT_RESCHEDULED_BY_MASTER
        text = CLIENT_APPOINTMENT_RESCHEDULED_BY_MASTER.format(
            old_dt=_fmt_dt(old_start_at, self._tz),
            new_dt=_fmt_dt(new_start_at, self._tz),
        )
        await self._send_to(client_telegram_id, text)

    async def _send_to(self, chat_id: int, text: str) -> None:
        try:
            await self._bot.send_message(
                chat_id=chat_id,
                text=text,
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("Failed to send notification to chat_id=%s", chat_id)

    async def _send(self, text: str) -> None:
        await self._send_to(self._master_chat_id, text)
