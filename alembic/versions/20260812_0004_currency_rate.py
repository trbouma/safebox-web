"""Add last-known-good currency rate cache.

Revision ID: 20260812_0004
Revises: 20260808_0003
Create Date: 2026-08-12
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260812_0004"
down_revision = "20260808_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "currency_rate",
        sa.Column("currency_code", sa.String(), nullable=False),
        sa.Column("fiat_per_btc", sa.Float(), nullable=False),
        sa.Column("currency_symbol", sa.String(), nullable=False),
        sa.Column("currency_description", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("currency_code"),
    )


def downgrade() -> None:
    op.drop_table("currency_rate")
