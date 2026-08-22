"""Add web-worker liveness for prompt orphaned-job recovery.

Revision ID: 20260822_0007
Revises: 20260822_0006
Create Date: 2026-08-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260822_0007"
down_revision = "20260822_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "web_worker_heartbeat",
        sa.Column("worker_id", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("worker_id"),
    )
    op.create_index(
        "ix_web_worker_heartbeat_heartbeat_at",
        "web_worker_heartbeat",
        ["heartbeat_at"],
        unique=False,
    )
    with op.batch_alter_table("funds_finalization_job") as batch_op:
        batch_op.add_column(sa.Column("owner_worker_id", sa.String(), nullable=True))
        batch_op.create_index(
            "ix_funds_finalization_job_owner_worker_id",
            ["owner_worker_id"],
            unique=False,
        )
    with op.batch_alter_table("clear_acceptance_job") as batch_op:
        batch_op.add_column(sa.Column("owner_worker_id", sa.String(), nullable=True))
        batch_op.create_index(
            "ix_clear_acceptance_job_owner_worker_id",
            ["owner_worker_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("clear_acceptance_job") as batch_op:
        batch_op.drop_index("ix_clear_acceptance_job_owner_worker_id")
        batch_op.drop_column("owner_worker_id")
    with op.batch_alter_table("funds_finalization_job") as batch_op:
        batch_op.drop_index("ix_funds_finalization_job_owner_worker_id")
        batch_op.drop_column("owner_worker_id")
    op.drop_index(
        "ix_web_worker_heartbeat_heartbeat_at",
        table_name="web_worker_heartbeat",
    )
    op.drop_table("web_worker_heartbeat")
