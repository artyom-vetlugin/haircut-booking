"""Routes Claude tool_use blocks to the corresponding implementation."""

from __future__ import annotations

import logging
from typing import Any

from app.tools.booking_tools import (
    ToolContext,
    cancel_appointment,
    create_booking,
    get_available_slots,
    get_my_appointment,
    reschedule_appointment,
)

logger = logging.getLogger(__name__)

_TOOL_MAP = {
    "get_available_slots": get_available_slots,
    "get_my_appointment": get_my_appointment,
    "create_booking": create_booking,
    "cancel_appointment": cancel_appointment,
    "reschedule_appointment": reschedule_appointment,
}


async def execute_tool(name: str, inp: dict[str, Any], ctx: ToolContext) -> str:
    """Dispatch a Claude tool call to the corresponding implementation.

    Returns a plain-text result string to be passed back to Claude as a
    tool_result block. Never raises — unknown tools return an error string.
    """
    fn = _TOOL_MAP.get(name)
    if fn is None:
        logger.warning("Unknown tool requested by Claude: %s", name)
        return f"Инструмент '{name}' не найден."
    return await fn(inp, ctx)
