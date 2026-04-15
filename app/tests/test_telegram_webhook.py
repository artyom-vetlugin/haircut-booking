"""Tests for the Telegram webhook endpoint."""

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_webhook_no_secret_returns_ok() -> None:
    """Without a configured secret every POST is accepted."""
    with patch("app.integrations.telegram.webhook.settings") as mock_settings:
        mock_settings.telegram_webhook_secret = ""

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/webhook/telegram", json={"update_id": 1})

    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.asyncio
async def test_webhook_correct_secret_returns_ok() -> None:
    """Matching secret header is accepted."""
    with patch("app.integrations.telegram.webhook.settings") as mock_settings:
        mock_settings.telegram_webhook_secret = "s3cr3t"

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/webhook/telegram",
                json={"update_id": 2},
                headers={"X-Telegram-Bot-Api-Secret-Token": "s3cr3t"},
            )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_webhook_wrong_secret_returns_403() -> None:
    """Wrong secret header is rejected with 403."""
    with patch("app.integrations.telegram.webhook.settings") as mock_settings:
        mock_settings.telegram_webhook_secret = "correct"

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/webhook/telegram",
                json={"update_id": 3},
                headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
            )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_webhook_missing_secret_header_returns_403() -> None:
    """Missing secret header when secret is configured is rejected with 403."""
    with patch("app.integrations.telegram.webhook.settings") as mock_settings:
        mock_settings.telegram_webhook_secret = "required"

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/webhook/telegram", json={"update_id": 4})

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_webhook_calls_process_update() -> None:
    """Accepted update is forwarded to bot_client.process_update."""
    from app.integrations.telegram.client import bot_client

    with patch("app.integrations.telegram.webhook.settings") as mock_settings:
        mock_settings.telegram_webhook_secret = ""

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post("/webhook/telegram", json={"update_id": 5})

    bot_client.process_update.assert_awaited_once_with({"update_id": 5})  # type: ignore[attr-defined]
