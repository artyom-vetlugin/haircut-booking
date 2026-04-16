"""MasterCancelFlowUseCase — two-step flow for cancelling an appointment.

Flow steps:
    1. on_appointment_selected — master chose an appointment from the list →
                                  store appointment ID, advance to MASTER_CANCEL_CONFIRM
    2. on_confirm              — master confirmed → cancel via service, advance to IDLE
"""

from __future__ import annotations

import uuid
from datetime import datetime

from app.core import states
from app.core.exceptions import FlowExpiredError
from app.db.models import Appointment
from app.use_cases.deps import HandlerServices


class MasterCancelFlowUseCase:
    async def on_appointment_selected(
        self,
        master_id: int,
        appointment_id: str,
        svc: HandlerServices,
    ) -> Appointment:
        """Store the appointment ID, advance to MASTER_CANCEL_CONFIRM.

        Returns the Appointment so the handler can display its details.
        Raises FlowExpiredError if state does not match.
        """
        bot_session = await svc.session_repo.get_by_telegram_user_id(master_id)
        if bot_session is None or bot_session.current_state != states.MASTER_CANCEL_SELECT_APPT:
            raise FlowExpiredError()

        appt = await svc.appointment_service.get_by_id(
            uuid.UUID(appointment_id), load_client=True
        )
        if appt is None:
            raise FlowExpiredError()

        await svc.session_repo.upsert(
            master_id,
            states.MASTER_CANCEL_CONFIRM,
            {"appointment_id": appointment_id},
        )
        return appt

    async def on_confirm(
        self,
        master_id: int,
        svc: HandlerServices,
        now: datetime,
    ) -> Appointment:
        """Cancel the appointment and return to IDLE.

        Returns the cancelled Appointment.
        Raises FlowExpiredError, NoAppointmentError, CalendarSyncError.
        """
        bot_session = await svc.session_repo.get_by_telegram_user_id(master_id)
        if bot_session is None or bot_session.current_state != states.MASTER_CANCEL_CONFIRM:
            raise FlowExpiredError()

        payload = bot_session.draft_payload or {}
        appt_id_str: str | None = payload.get("appointment_id")
        if not appt_id_str:
            raise FlowExpiredError()

        appointment_id = uuid.UUID(appt_id_str)
        appt = await svc.appointment_service.cancel_appointment_by_id(
            appointment_id,
            actor_type="master",
            actor_id=f"master:{master_id}",
            now=now,
        )

        await svc.session_repo.upsert(master_id, states.IDLE, {})
        return appt
