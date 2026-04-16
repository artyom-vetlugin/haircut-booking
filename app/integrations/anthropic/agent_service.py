"""AgentService — runs the Claude tool-use loop for free-text booking requests.

The loop:
  1. Send user message + tools to Claude.
  2. If Claude returns tool_use blocks, execute them and feed results back.
  3. Repeat until Claude returns end_turn or max iterations is reached.
  4. Return the final Russian text response.

Claude is the intent router only. Business rules are enforced by the service
layer; Claude cannot claim success before a tool confirms it.
"""

from __future__ import annotations

import logging
from typing import Any

from app.integrations.anthropic.claude_client import ClaudeClient
from app.integrations.anthropic.prompts import SYSTEM_PROMPT
from app.integrations.anthropic.tool_definitions import ALL_TOOLS
from app.tools.booking_tools import ToolContext
from app.tools.tool_executor import execute_tool
from app.use_cases.deps import HandlerServices

logger = logging.getLogger(__name__)

_MAX_ITERATIONS = 5
_MAX_HISTORY = 20  # max messages kept across turns (10 user+assistant pairs)
_FALLBACK = (
    "Извините, не удалось обработать ваш запрос. "
    "Пожалуйста, воспользуйтесь меню или попробуйте ещё раз."
)


class AgentService:
    """Orchestrates the Claude agentic loop for a single user message."""

    def __init__(self, client: ClaudeClient | None = None) -> None:
        self._client = client or ClaudeClient()

    async def handle_message(
        self,
        telegram_user_id: int,
        user_text: str,
        services: HandlerServices,
        history: list[dict[str, Any]] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Process a free-text user message and return (reply, updated_history).

        *history* contains prior user/assistant turns (plain text only, no tool
        call internals).  The updated history is returned so the caller can
        persist it.  The DB session embedded in *services* must remain open for
        the duration of this call, as tool implementations run DB queries.
        """
        ctx = ToolContext(
            telegram_user_id=telegram_user_id,
            appointment_service=services.appointment_service,
            availability=services.availability,
            client_repo=services.client_repo,
            calendar=services.calendar,
            rules=services.rules,
        )

        prior_history: list[dict[str, Any]] = list(history or [])
        messages: list[dict[str, Any]] = prior_history + [
            {"role": "user", "content": user_text},
        ]

        for iteration in range(_MAX_ITERATIONS):
            try:
                response = await self._client.complete(
                    messages=messages,
                    tools=ALL_TOOLS,
                    system=SYSTEM_PROMPT,
                )
            except Exception:
                logger.exception(
                    "Claude API error on iteration %d for user %s",
                    iteration,
                    telegram_user_id,
                )
                return _FALLBACK, prior_history

            text_parts: list[str] = []
            tool_calls: list[Any] = []
            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_calls.append(block)

            if response.stop_reason == "end_turn" or not tool_calls:
                reply = " ".join(text_parts).strip() or _FALLBACK
                new_history = prior_history + [
                    {"role": "user", "content": user_text},
                    {"role": "assistant", "content": reply},
                ]
                if len(new_history) > _MAX_HISTORY:
                    new_history = new_history[-_MAX_HISTORY:]
                return reply, new_history

            # Execute tool calls, then continue the loop
            messages.append({"role": "assistant", "content": response.content})
            tool_results: list[dict[str, Any]] = []
            for tc in tool_calls:
                result_text = await execute_tool(tc.name, dict(tc.input), ctx)
                logger.debug(
                    "Tool %s → %s (user=%s)", tc.name, result_text[:80], telegram_user_id
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tc.id,
                        "content": result_text,
                    }
                )
            messages.append({"role": "user", "content": tool_results})

        logger.warning(
            "Agent loop reached max iterations (%d) for user %s",
            _MAX_ITERATIONS,
            telegram_user_id,
        )
        return _FALLBACK, prior_history
