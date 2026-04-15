"""Shared pytest fixtures.

Environment variables are set at module level so they are present before
pydantic-settings instantiates Settings() during test collection.
"""

from __future__ import annotations

import os

# Provide minimal required config values for the test environment.
# setdefault means a real .env file (if present) still takes precedence.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "0:test-token")
os.environ.setdefault("TELEGRAM_MASTER_CHAT_ID", "12345")

from unittest.mock import AsyncMock  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def mock_telegram_bot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace bot_client async methods with no-op mocks for every test.

    Prevents tests from requiring a real Telegram token or network access
    while still exercising all application code paths.
    """
    from app.integrations.telegram.client import bot_client

    monkeypatch.setattr(bot_client, "initialize", AsyncMock())
    monkeypatch.setattr(bot_client, "shutdown", AsyncMock())
    monkeypatch.setattr(bot_client, "process_update", AsyncMock())
