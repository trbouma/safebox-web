"""Add non-secret recipient funds-finalization job coordination.

Revision ID: 20260813_0005
Revises: 20260812_0004
Create Date: 2026-08-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260813_0005"
down_revision = "20260812_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "funds_finalization_job",
        sa.Column("npub", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("owner_token", sa.String(), nullable=False),
        sa.Column("phase", sa.String(), nullable=False),
        sa.Column("discovered_count", sa.Integer(), nullable=False),
        sa.Column("discovered_amount", sa.Integer(), nullable=False),
        sa.Column("confirmed_count", sa.Integer(), nullable=False),
        sa.Column("confirmed_amount", sa.Integer(), nullable=False),
        sa.Column("pending_count", sa.Integer(), nullable=False),
        sa.Column("pending_amount", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("npub"),
    )
    op.create_index(
        "ix_funds_finalization_job_status",
        "funds_finalization_job",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_funds_finalization_job_lease_expires_at",
        "funds_finalization_job",
        ["lease_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_funds_finalization_job_lease_expires_at",
        table_name="funds_finalization_job",
    )
    op.drop_index(
        "ix_funds_finalization_job_status",
        table_name="funds_finalization_job",
    )
    op.drop_table("funds_finalization_job")
