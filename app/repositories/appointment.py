from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Appointment, AppointmentStatus


class AppointmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(
        self, appointment_id: uuid.UUID, *, load_client: bool = False
    ) -> Appointment | None:
        query = select(Appointment).where(Appointment.id == appointment_id)
        if load_client:
            query = query.options(selectinload(Appointment.client))
        result = await self._session.execute(query)
        return result.scalar_one_or_none()

    async def get_by_google_event_id(self, google_event_id: str) -> Appointment | None:
        result = await self._session.execute(
            select(Appointment).where(Appointment.google_event_id == google_event_id)
        )
        return result.scalar_one_or_none()

    async def get_active_by_client_id(
        self, client_id: uuid.UUID, now: datetime
    ) -> Appointment | None:
        """Return the single active future appointment for a client, if any."""
        result = await self._session.execute(
            select(Appointment).where(
                Appointment.client_id == client_id,
                Appointment.status != AppointmentStatus.cancelled,
                Appointment.start_at > now,
            )
        )
        return result.scalar_one_or_none()

    async def get_overlapping(
        self,
        start_at: datetime,
        end_at: datetime,
        exclude_id: Optional[uuid.UUID] = None,
    ) -> list[Appointment]:
        """Return active appointments whose time range overlaps [start_at, end_at)."""
        query = select(Appointment).where(
            Appointment.status != AppointmentStatus.cancelled,
            Appointment.start_at < end_at,
            Appointment.end_at > start_at,
        )
        if exclude_id is not None:
            query = query.where(Appointment.id != exclude_id)
        result = await self._session.execute(query)
        return list(result.scalars().all())

    async def get_active_in_range(
        self, start_at: datetime, end_at: datetime
    ) -> list[Appointment]:
        """Return all active (non-cancelled) appointments overlapping [start_at, end_at)."""
        result = await self._session.execute(
            select(Appointment).where(
                Appointment.status != AppointmentStatus.cancelled,
                Appointment.start_at < end_at,
                Appointment.end_at > start_at,
            )
        )
        return list(result.scalars().all())

    async def get_all_future(self, now: datetime) -> list[Appointment]:
        """All non-cancelled appointments starting after *now*, ordered by start_at.

        Eagerly loads the related Client so callers can render client names.
        """
        result = await self._session.execute(
            select(Appointment)
            .options(selectinload(Appointment.client))
            .where(
                Appointment.status != AppointmentStatus.cancelled,
                Appointment.start_at > now,
            )
            .order_by(Appointment.start_at)
        )
        return list(result.scalars().all())

    async def list_by_client_id(self, client_id: uuid.UUID) -> list[Appointment]:
        result = await self._session.execute(
            select(Appointment)
            .where(Appointment.client_id == client_id)
            .order_by(Appointment.start_at.desc())
        )
        return list(result.scalars().all())

    async def create(self, **kwargs: Any) -> Appointment:
        appointment = Appointment(**kwargs)
        self._session.add(appointment)
        await self._session.flush()
        await self._session.refresh(appointment)
        return appointment

    async def update(self, appointment: Appointment, **kwargs: Any) -> Appointment:
        for key, value in kwargs.items():
            setattr(appointment, key, value)
        await self._session.flush()
        await self._session.refresh(appointment)
        return appointment
