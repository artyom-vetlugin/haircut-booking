import logging

logger = logging.getLogger(__name__)


class StartUseCase:
    """Handles the /start command.

    Currently a stub — will upsert the Client record and initialise the
    BotSession once the service layer is in place.
    """

    async def execute(self, telegram_user_id: int) -> None:
        logger.debug("StartUseCase called for user %s", telegram_user_id)
