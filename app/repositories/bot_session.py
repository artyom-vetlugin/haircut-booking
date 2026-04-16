from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BotSession


class BotSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_telegram_user_id(self, telegram_user_id: int) -> BotSession | None:
        result = await self._session.execute(
            select(BotSession).where(BotSession.telegram_user_id == telegram_user_id)
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        telegram_user_id: int,
        current_state: str,
        draft_payload: dict[str, Any],
    ) -> BotSession:
        """Create or replace the session state for a Telegram user."""
        existing = await self.get_by_telegram_user_id(telegram_user_id)
        if existing is not None:
            existing.current_state = current_state
            existing.draft_payload = draft_payload
            await self._session.flush()
            await self._session.refresh(existing)
            return existing
        session = BotSession(
            id=uuid.uuid4(),
            telegram_user_id=telegram_user_id,
            current_state=current_state,
            draft_payload=draft_payload,
        )
        self._session.add(session)
        await self._session.flush()
        await self._session.refresh(session)
        return session

    async def save_conversation_history(
        self, telegram_user_id: int, history: list[dict]
    ) -> None:
        existing = await self.get_by_telegram_user_id(telegram_user_id)
        if existing is not None:
            existing.conversation_history = history
            await self._session.flush()

    async def delete_by_telegram_user_id(self, telegram_user_id: int) -> None:
        existing = await self.get_by_telegram_user_id(telegram_user_id)
        if existing is not None:
            await self._session.delete(existing)
            await self._session.flush()
