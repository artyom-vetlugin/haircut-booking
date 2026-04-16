"""Claude-callable tool implementations.

Each function receives a tool-input dict and a ToolContext, calls the
appropriate service-layer method, and returns a plain-text result string
that is fed back to Claude as a tool_result block.

Only service methods are called here — never repositories or adapters directly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from app.core.exceptions import (
    BookingConflictError,
    CalendarSyncError,
    NoAppointmentError,
    SlotUnavailableError,
    TooManyAppointmentsError,
)
from app.integrations.google_calendar_mcp.calendar_adapter import CalendarAdapter
from app.repositories.client import ClientRepository
from app.schemas.availability import BusyInterval
from app.services.appointment_service import AppointmentService
from app.services.availability_service import AvailabilityService
from app.services.booking_rules_service import BookingRulesService
from app.use_cases.deps import build_client_label, build_event_description

logger = logging.getLogger(__name__)

_HORIZON_DAYS = 7


@dataclass
class ToolContext:
    telegram_user_id: int
    appointment_service: AppointmentService
    availability: AvailabilityService
    client_repo: ClientRepository
    calendar: CalendarAdapter
    rules: BookingRulesService


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


async def get_available_slots(inp: dict[str, Any], ctx: ToolContext) -> str:
    tz = ctx.rules.timezone
    now = datetime.now(tz=tz)

    if inp.get("date"):
        try:
            target = date.fromisoformat(inp["date"])
        except ValueError:
            return "Некорректный формат даты. Используйте YYYY-MM-DD."

        day_start = datetime(target.year, target.month, target.day, tzinfo=tz)
        day_end = day_start + timedelta(days=1)
        cal_busy = await ctx.calendar.get_busy_intervals(day_start, day_end)
        db_appts = await ctx.appointment_service.get_active_appointments_in_range(day_start, day_end)
        db_busy = [BusyInterval(start=a.start_at, end=a.end_at) for a in db_appts]
        busy = cal_busy + db_busy
        slots = ctx.availability.get_available_slots(target, busy, now)

        if not slots:
            return f"На {inp['date']} нет свободных слотов."

        times = ", ".join(s.start.astimezone(tz).strftime("%H:%M") for s in slots)
        return f"Свободные слоты на {inp['date']}: {times}."

    else:
        today = now.date()
        horizon = today + timedelta(days=_HORIZON_DAYS)
        range_start = now
        range_end = datetime(horizon.year, horizon.month, horizon.day, 23, 59, 59, tzinfo=tz)
        cal_busy = await ctx.calendar.get_busy_intervals(range_start, range_end)
        db_appts = await ctx.appointment_service.get_active_appointments_in_range(range_start, range_end)
        db_busy = [BusyInterval(start=a.start_at, end=a.end_at) for a in db_appts]
        busy = cal_busy + db_busy
        day_slots = ctx.availability.get_available_slots_for_range(today, horizon, busy, now)

        if not day_slots:
            return "На ближайшую неделю нет свободных слотов."

        lines = ["Свободные слоты на ближайшую неделю:"]
        for ds in day_slots:
            times = ", ".join(s.start.astimezone(tz).strftime("%H:%M") for s in ds.slots)
            lines.append(f"  {ds.date.strftime('%d.%m.%Y')}: {times}")
        return "\n".join(lines)


async def get_my_appointment(inp: dict[str, Any], ctx: ToolContext) -> str:
    client = await ctx.client_repo.get_by_telegram_user_id(ctx.telegram_user_id)
    if client is None:
        return "Клиент не найден. Отправьте /start чтобы зарегистрироваться."

    tz = ctx.rules.timezone
    now = datetime.now(tz=tz)
    appt = await ctx.appointment_service.get_future_appointment_for_client(client.id, now=now)

    if appt is None:
        return "У вас нет активных записей."

    local = appt.start_at.astimezone(tz)
    return f"Ваша запись: {local.strftime('%d.%m.%Y в %H:%M')}."


async def create_booking(inp: dict[str, Any], ctx: ToolContext) -> str:
    client = await ctx.client_repo.get_by_telegram_user_id(ctx.telegram_user_id)
    if client is None:
        return "Клиент не найден. Отправьте /start чтобы зарегистрироваться."

    try:
        slot_start = datetime.fromisoformat(inp["slot_start"])
    except (KeyError, ValueError):
        return "Некорректный формат времени. Используйте ISO 8601 с часовым поясом."

    tz = ctx.rules.timezone
    now = datetime.now(tz=tz)

    try:
        appt = await ctx.appointment_service.create_booking(
            client.id,
            slot_start,
            actor_id=str(ctx.telegram_user_id),
            client_label=build_client_label(client),
            event_description=build_event_description(client),
            now=now,
        )
        local = appt.start_at.astimezone(tz)
        return f"Запись создана: {local.strftime('%d.%m.%Y в %H:%M')}."
    except TooManyAppointmentsError:
        return "У вас уже есть активная запись. Сначала отмените или перенесите её."
    except SlotUnavailableError as exc:
        return f"Этот слот недоступен: {exc}"
    except BookingConflictError:
        return "На это время уже есть запись. Выберите другое время."
    except CalendarSyncError:
        return "Не удалось синхронизировать с календарём. Попробуйте ещё раз."
    except Exception:
        logger.exception("Unexpected error in create_booking tool for user %s", ctx.telegram_user_id)
        return "Произошла непредвиденная ошибка. Попробуйте ещё раз."


async def cancel_appointment(inp: dict[str, Any], ctx: ToolContext) -> str:
    client = await ctx.client_repo.get_by_telegram_user_id(ctx.telegram_user_id)
    if client is None:
        return "Клиент не найден. Отправьте /start чтобы зарегистрироваться."

    tz = ctx.rules.timezone
    now = datetime.now(tz=tz)
    reason: str | None = inp.get("reason")

    try:
        await ctx.appointment_service.cancel_booking(
            client.id,
            reason=reason,
            actor_id=str(ctx.telegram_user_id),
            now=now,
        )
        return "Запись успешно отменена."
    except NoAppointmentError:
        return "У вас нет активных записей для отмены."
    except CalendarSyncError:
        return "Не удалось синхронизировать с календарём. Попробуйте ещё раз."
    except Exception:
        logger.exception("Unexpected error in cancel_appointment tool for user %s", ctx.telegram_user_id)
        return "Произошла непредвиденная ошибка. Попробуйте ещё раз."


async def reschedule_appointment(inp: dict[str, Any], ctx: ToolContext) -> str:
    client = await ctx.client_repo.get_by_telegram_user_id(ctx.telegram_user_id)
    if client is None:
        return "Клиент не найден. Отправьте /start чтобы зарегистрироваться."

    try:
        new_slot_start = datetime.fromisoformat(inp["new_slot_start"])
    except (KeyError, ValueError):
        return "Некорректный формат времени. Используйте ISO 8601 с часовым поясом."

    tz = ctx.rules.timezone
    now = datetime.now(tz=tz)

    try:
        appt = await ctx.appointment_service.reschedule_booking(
            client.id,
            new_slot_start,
            actor_id=str(ctx.telegram_user_id),
            now=now,
        )
        local = appt.start_at.astimezone(tz)
        return f"Запись перенесена на {local.strftime('%d.%m.%Y в %H:%M')}."
    except NoAppointmentError:
        return "У вас нет активных записей для переноса."
    except SlotUnavailableError as exc:
        return f"Этот слот недоступен: {exc}"
    except BookingConflictError:
        return "На это время уже есть запись. Выберите другое время."
    except CalendarSyncError:
        return "Не удалось синхронизировать с календарём. Попробуйте ещё раз."
    except Exception:
        logger.exception("Unexpected error in reschedule_appointment tool for user %s", ctx.telegram_user_id)
        return "Произошла непредвиденная ошибка. Попробуйте ещё раз."
