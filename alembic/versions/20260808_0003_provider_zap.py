"""Add provider identity and NIP-57 zap state.

Revision ID: 20260808_0003
Revises: 20260804_0002
Create Date: 2026-08-08
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260808_0003"
down_revision = "20260804_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_identity",
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("nostr_pubkey", sa.String(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )
    op.create_table(
        "provider_zap",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("payment_id", sa.String(), nullable=False),
        sa.Column("request_event_id", sa.String(), nullable=False),
        sa.Column("request_json", sa.String(), nullable=False),
        sa.Column("receipt_relays_json", sa.String(), nullable=False),
        sa.Column("receipt_event_id", sa.String(), nullable=True),
        sa.Column("receipt_json", sa.String(), nullable=True),
        sa.Column("receipt_error", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("payment_id", name="uq_provider_zap_payment_id"),
        sa.UniqueConstraint(
            "request_event_id", name="uq_provider_zap_request_event_id"
        ),
    )
    op.create_index(
        "ix_provider_zap_payment_id", "provider_zap", ["payment_id"], unique=False
    )
    op.create_index(
        "ix_provider_zap_request_event_id",
        "provider_zap",
        ["request_event_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_provider_zap_request_event_id", table_name="provider_zap")
    op.drop_index("ix_provider_zap_payment_id", table_name="provider_zap")
    op.drop_table("provider_zap")
    op.drop_table("provider_identity")
