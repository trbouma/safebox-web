"""Create the durable provider payment queue.

Revision ID: 20260804_0002
Revises: 20260804_0001
Create Date: 2026-08-04
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260804_0002"
down_revision = "20260804_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_payment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("payment_id", sa.String(), nullable=False),
        sa.Column("claimed_handle", sa.String(), nullable=False),
        sa.Column("recipient_npub", sa.String(), nullable=False),
        sa.Column("recipient_relay", sa.String(), nullable=False),
        sa.Column("amount_msat", sa.Integer(), nullable=False),
        sa.Column("amount_sat", sa.Integer(), nullable=False),
        sa.Column("comment", sa.String(), nullable=True),
        sa.Column("lnurl_metadata", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("mint", sa.String(), nullable=False),
        sa.Column("mint_quote", sa.String(), nullable=True),
        sa.Column("invoice", sa.String(), nullable=True),
        sa.Column("delivery_event_id", sa.String(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("next_check_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("payment_id", name="uq_provider_payment_payment_id"),
        sa.UniqueConstraint("mint_quote", name="uq_provider_payment_mint_quote"),
    )
    op.create_index(
        "ix_provider_payment_payment_id",
        "provider_payment",
        ["payment_id"],
        unique=False,
    )
    op.create_index(
        "ix_provider_payment_claimed_handle",
        "provider_payment",
        ["claimed_handle"],
        unique=False,
    )
    op.create_index(
        "ix_provider_payment_status",
        "provider_payment",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_provider_payment_next_check_at",
        "provider_payment",
        ["next_check_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_provider_payment_next_check_at", table_name="provider_payment")
    op.drop_index("ix_provider_payment_status", table_name="provider_payment")
    op.drop_index("ix_provider_payment_claimed_handle", table_name="provider_payment")
    op.drop_index("ix_provider_payment_payment_id", table_name="provider_payment")
    op.drop_table("provider_payment")
