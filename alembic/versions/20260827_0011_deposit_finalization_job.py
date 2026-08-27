"""add attached Acorn deposit finalization job

Revision ID: 20260827_0011
Revises: 20260824_0010
Create Date: 2026-08-27
"""

from alembic import op
import sqlalchemy as sa


revision = "20260827_0011"
down_revision = "20260824_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deposit_finalization_job",
        sa.Column("quote_hash", sa.String(), nullable=False),
        sa.Column("npub", sa.String(), nullable=False),
        sa.Column("owner_token", sa.String(), nullable=False),
        sa.Column("owner_worker_id", sa.String(), nullable=True),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("mint", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("phase", sa.String(), nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("quote_hash"),
    )
    op.create_index(
        "ix_deposit_finalization_job_npub",
        "deposit_finalization_job",
        ["npub"],
    )
    op.create_index(
        "ix_deposit_finalization_job_owner_worker_id",
        "deposit_finalization_job",
        ["owner_worker_id"],
    )
    op.create_index(
        "ix_deposit_finalization_job_status",
        "deposit_finalization_job",
        ["status"],
    )
    op.create_index(
        "ix_deposit_finalization_job_lease_expires_at",
        "deposit_finalization_job",
        ["lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_deposit_finalization_job_lease_expires_at",
        table_name="deposit_finalization_job",
    )
    op.drop_index(
        "ix_deposit_finalization_job_status",
        table_name="deposit_finalization_job",
    )
    op.drop_index(
        "ix_deposit_finalization_job_owner_worker_id",
        table_name="deposit_finalization_job",
    )
    op.drop_index(
        "ix_deposit_finalization_job_npub",
        table_name="deposit_finalization_job",
    )
    op.drop_table("deposit_finalization_job")
