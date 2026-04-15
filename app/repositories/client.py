from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Client


class ClientRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, client_id: uuid.UUID) -> Client | None:
        result = await self._session.execute(
            select(Client).where(Client.id == client_id)
        )
        return result.scalar_one_or_none()

    async def get_by_telegram_user_id(self, telegram_user_id: int) -> Client | None:
        result = await self._session.execute(
            select(Client).where(Client.telegram_user_id == telegram_user_id)
        )
        return result.scalar_one_or_none()

    async def create(self, **kwargs: Any) -> Client:
        client = Client(**kwargs)
        self._session.add(client)
        await self._session.flush()
        await self._session.refresh(client)
        return client

    async def update(self, client: Client, **kwargs: Any) -> Client:
        for key, value in kwargs.items():
            setattr(client, key, value)
        await self._session.flush()
        await self._session.refresh(client)
        return client
