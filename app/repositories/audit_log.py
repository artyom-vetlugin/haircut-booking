from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AuditLog


class AuditLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, **kwargs: Any) -> AuditLog:
        log = AuditLog(**kwargs)
        self._session.add(log)
        await self._session.flush()
        await self._session.refresh(log)
        return log

    async def list_by_appointment_id(self, appointment_id: uuid.UUID) -> list[AuditLog]:
        result = await self._session.execute(
            select(AuditLog)
            .where(AuditLog.appointment_id == appointment_id)
            .order_by(AuditLog.created_at.asc())
        )
        return list(result.scalars().all())
