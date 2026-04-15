"""Thin async wrapper around the Anthropic SDK client."""

from __future__ import annotations

import logging
from typing import Any

import anthropic

from app.core.config import settings

logger = logging.getLogger(__name__)


class ClaudeClient:
    """Wraps anthropic.AsyncAnthropic for the agent loop."""

    def __init__(self) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._model = settings.anthropic_model

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
        max_tokens: int = 1024,
    ) -> anthropic.types.Message:
        return await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            tools=tools,  # type: ignore[arg-type]
            messages=messages,  # type: ignore[arg-type]
        )
