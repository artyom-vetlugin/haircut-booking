"""Tests for StartUseCase.

Covers the two client-upsert branches (create vs. skip) and the invariant that
BotSession is always reset to IDLE regardless of whether a new client was created.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.states import IDLE
from app.use_cases.start import StartUseCase


def _make_repos(existing_client=None):
    """Return (mock_client_repo, mock_session_repo) wired with sensible defaults."""
    client_repo = MagicMock()
    client_repo.get_by_telegram_user_id = AsyncMock(return_value=existing_client)
    client_repo.create = AsyncMock(return_value=MagicMock())

    session_repo = MagicMock()
    session_repo.upsert = AsyncMock()

    return client_repo, session_repo


def _patch_repos(client_repo, session_repo):
    return (
        patch("app.use_cases.start.ClientRepository", return_value=client_repo),
        patch("app.use_cases.start.BotSessionRepository", return_value=session_repo),
    )


class TestStartUseCaseCreateClient:
    @pytest.mark.asyncio
    async def test_creates_client_when_not_exists(self) -> None:
        client_repo, session_repo = _make_repos(existing_client=None)
        cr_patch, sr_patch = _patch_repos(client_repo, session_repo)

        with cr_patch, sr_patch:
            await StartUseCase().execute(MagicMock(), telegram_user_id=123)

        client_repo.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_create_when_client_already_exists(self) -> None:
        client_repo, session_repo = _make_repos(existing_client=MagicMock())
        cr_patch, sr_patch = _patch_repos(client_repo, session_repo)

        with cr_patch, sr_patch:
            await StartUseCase().execute(MagicMock(), telegram_user_id=123)

        client_repo.create.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_passes_user_info_to_create(self) -> None:
        client_repo, session_repo = _make_repos(existing_client=None)
        cr_patch, sr_patch = _patch_repos(client_repo, session_repo)

        with cr_patch, sr_patch:
            await StartUseCase().execute(
                MagicMock(),
                telegram_user_id=789,
                first_name="Мария",
                last_name="Иванова",
                username="maria_iv",
            )

        kwargs = client_repo.create.call_args.kwargs
        assert kwargs["telegram_user_id"] == 789
        assert kwargs["first_name"] == "Мария"
        assert kwargs["last_name"] == "Иванова"
        assert kwargs["telegram_username"] == "maria_iv"

    @pytest.mark.asyncio
    async def test_none_optional_fields_passed_to_create(self) -> None:
        """first_name / last_name / username may be None — must not be omitted."""
        client_repo, session_repo = _make_repos(existing_client=None)
        cr_patch, sr_patch = _patch_repos(client_repo, session_repo)

        with cr_patch, sr_patch:
            await StartUseCase().execute(MagicMock(), telegram_user_id=1)

        kwargs = client_repo.create.call_args.kwargs
        assert kwargs["first_name"] is None
        assert kwargs["last_name"] is None
        assert kwargs["telegram_username"] is None


class TestStartUseCaseSession:
    @pytest.mark.asyncio
    async def test_always_upserts_session_to_idle_for_new_client(self) -> None:
        client_repo, session_repo = _make_repos(existing_client=None)
        cr_patch, sr_patch = _patch_repos(client_repo, session_repo)

        with cr_patch, sr_patch:
            await StartUseCase().execute(MagicMock(), telegram_user_id=456)

        session_repo.upsert.assert_awaited_once_with(456, IDLE, {})

    @pytest.mark.asyncio
    async def test_always_upserts_session_to_idle_for_returning_client(self) -> None:
        client_repo, session_repo = _make_repos(existing_client=MagicMock())
        cr_patch, sr_patch = _patch_repos(client_repo, session_repo)

        with cr_patch, sr_patch:
            await StartUseCase().execute(MagicMock(), telegram_user_id=456)

        session_repo.upsert.assert_awaited_once_with(456, IDLE, {})

    @pytest.mark.asyncio
    async def test_session_upsert_is_called_exactly_once(self) -> None:
        client_repo, session_repo = _make_repos(existing_client=None)
        cr_patch, sr_patch = _patch_repos(client_repo, session_repo)

        with cr_patch, sr_patch:
            await StartUseCase().execute(MagicMock(), telegram_user_id=100)

        assert session_repo.upsert.await_count == 1
