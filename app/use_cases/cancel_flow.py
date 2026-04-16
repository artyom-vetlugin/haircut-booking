"""CancelFlowUseCase — orchestrates the appointment cancellation confirmation step.

Flow steps:
    1. on_confirm — user confirmed cancellation → cancel appointment, advance to IDLE
"""

from __future__ import annotations

from datetime import datetime

from app.core import states
from app.core.exceptions import FlowExpiredError
from app.use_cases.deps import HandlerServices, get_or_create_client


class CancelFlowUseCase:
    async def on_confirm(
        self,
        user_id: int,
        first_name: str | None,
        last_name: str | None,
        username: str | None,
        svc: HandlerServices,
        now: datetime,
    ) -> None:
        """Validate CANCEL_CONFIRM state, cancel booking, advance to IDLE.

        Raises FlowExpiredError, NoAppointmentError, CalendarSyncError.
        """
        bot_session = await svc.session_repo.get_by_telegram_user_id(user_id)
        if bot_session is None or bot_session.current_state != states.CANCEL_CONFIRM:
            raise FlowExpiredError()

        client = await get_or_create_client(svc, user_id, first_name, last_name, username)
        await svc.appointment_service.cancel_booking(
            client.id, actor_id=str(user_id),
            client_name=client.first_name,
            client_phone=client.phone_number,
            now=now,
        )
        await svc.session_repo.upsert(user_id, states.IDLE, {})
