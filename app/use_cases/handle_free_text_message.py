"""Handle free-text Telegram messages via the Claude agent loop.

This use case is the fallback path for messages that don't match any
button text.  It guards against interrupting an in-progress button-driven
flow and delegates everything else to AgentService.
"""

from __future__ import annotations

import logging

from app.core import states
from app.integrations.anthropic.agent_service import AgentService
from app.use_cases.deps import HandlerServices

logger = logging.getLogger(__name__)

# States where the user is mid-flow; free text should not trigger the agent.
_IN_FLOW_STATES = {
    states.BOOKING_REQUEST_PHONE,
    states.BOOKING_SELECT_DATE,
    states.BOOKING_SELECT_SLOT,
    states.BOOKING_CONFIRM,
    states.RESCHEDULE_SELECT_DATE,
    states.RESCHEDULE_SELECT_SLOT,
    states.RESCHEDULE_CONFIRM,
    states.CANCEL_CONFIRM,
}

_IN_FLOW_REPLY = (
    "Вы в процессе оформления записи. "
    "Завершите его с помощью кнопок или нажмите «Назад» для возврата в меню."
)


class HandleFreeTextMessageUseCase:
    """Route a free-text message through Claude and return a Russian reply."""

    def __init__(self, agent_service: AgentService | None = None) -> None:
        self._agent = agent_service or AgentService()

    async def execute(
        self,
        telegram_user_id: int,
        user_text: str,
        services: HandlerServices,
    ) -> tuple[str, bool]:
        """Return ``(reply, in_flow)`` for *user_text*.

        *in_flow* is True when the user is mid-button-flow and the reply is
        the standard "finish with buttons or press Back" message.
        If the user is mid-flow, prompt them to complete it or go back.
        Otherwise, delegate to the Claude agent tool-use loop.
        """
        try:
            bot_session = await services.session_repo.get_by_telegram_user_id(telegram_user_id)
            if bot_session is not None and bot_session.current_state in _IN_FLOW_STATES:
                return _IN_FLOW_REPLY, True
        except Exception:
            logger.exception(
                "Failed to read bot session for user %s; proceeding to agent",
                telegram_user_id,
            )

        session = await services.session_repo.get_by_telegram_user_id(telegram_user_id)
        history = (session.conversation_history if session is not None else None) or []

        reply, new_history = await self._agent.handle_message(
            telegram_user_id, user_text, services, history=history
        )

        try:
            await services.session_repo.save_conversation_history(telegram_user_id, new_history)
        except Exception:
            logger.exception(
                "Failed to save conversation history for user %s", telegram_user_id
            )

        return reply, False
