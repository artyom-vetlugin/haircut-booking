from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.states import IDLE
from app.repositories.bot_session import BotSessionRepository
from app.repositories.client import ClientRepository

logger = logging.getLogger(__name__)


class StartUseCase:
    """Handles /start — upserts the Client record and initialises the BotSession."""

    async def execute(
        self,
        session: AsyncSession,
        telegram_user_id: int,
        first_name: str | None = None,
        last_name: str | None = None,
        username: str | None = None,
    ) -> None:
        client_repo = ClientRepository(session)
        session_repo = BotSessionRepository(session)

        client = await client_repo.get_by_telegram_user_id(telegram_user_id)
        if client is None:
            await client_repo.create(
                telegram_user_id=telegram_user_id,
                first_name=first_name,
                last_name=last_name,
                telegram_username=username,
            )
            logger.info("Created new client for telegram_user_id=%s", telegram_user_id)

        await session_repo.upsert(telegram_user_id, IDLE, {})
        logger.debug("BotSession initialised for telegram_user_id=%s", telegram_user_id)
