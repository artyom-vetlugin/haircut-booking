"""add conversation_history to bot_sessions

Revision ID: a1b2c3d4e5f6
Revises: 3f8a2c1b0d9e
Create Date: 2026-04-16 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "3f8a2c1b0d9e"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "bot_sessions",
        sa.Column(
            "conversation_history",
            postgresql.JSON(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("bot_sessions", "conversation_history")
