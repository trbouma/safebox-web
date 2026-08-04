"""Create the minimal Acorn NIP-05 handle directory.

Revision ID: 20260804_0001
Revises:
Create Date: 2026-08-04
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260804_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "claimed_handle",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("claimed_handle", sa.String(), nullable=False),
        sa.Column("npub", sa.String(), nullable=False),
        sa.Column("home_relay", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("claimed_handle", name="uq_claimed_handle_name"),
        sa.UniqueConstraint("npub", name="uq_claimed_handle_npub"),
    )


def downgrade() -> None:
    op.drop_table("claimed_handle")
