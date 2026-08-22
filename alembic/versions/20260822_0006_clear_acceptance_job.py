"""Add non-secret Clear acceptance job coordination.

Revision ID: 20260822_0006
Revises: 20260813_0005
Create Date: 2026-08-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260822_0006"
down_revision = "20260813_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "clear_acceptance_job",
        sa.Column("npub", sa.String(), nullable=False),
        sa.Column("event_id", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("owner_token", sa.String(), nullable=False),
        sa.Column("phase", sa.String(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("mint", sa.String(), nullable=True),
        sa.Column("unit", sa.String(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("npub"),
    )
    op.create_index(
        "ix_clear_acceptance_job_status",
        "clear_acceptance_job",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_clear_acceptance_job_lease_expires_at",
        "clear_acceptance_job",
        ["lease_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_clear_acceptance_job_lease_expires_at",
        table_name="clear_acceptance_job",
    )
    op.drop_index(
        "ix_clear_acceptance_job_status",
        table_name="clear_acceptance_job",
    )
    op.drop_table("clear_acceptance_job")
