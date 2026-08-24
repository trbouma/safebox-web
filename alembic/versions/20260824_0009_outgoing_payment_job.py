"""add outgoing payment job

Revision ID: 20260824_0009
Revises: 20260823_0008
Create Date: 2026-08-24
"""

from alembic import op
import sqlalchemy as sa


revision = "20260824_0009"
down_revision = "20260823_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outgoing_payment_job",
        sa.Column("npub", sa.String(), nullable=False),
        sa.Column("owner_token", sa.String(), nullable=False),
        sa.Column("owner_worker_id", sa.String(), nullable=True),
        sa.Column("payment_kind", sa.String(), nullable=False),
        sa.Column("recipient", sa.String(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("phase", sa.String(), nullable=False),
        sa.Column("total_fees", sa.Integer(), nullable=True),
        sa.Column("mint_fees", sa.Integer(), nullable=True),
        sa.Column("lightning_fee", sa.Integer(), nullable=True),
        sa.Column("lightning_fee_reserve", sa.Integer(), nullable=True),
        sa.Column("lightning_fee_return", sa.Integer(), nullable=True),
        sa.Column("message", sa.String(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("npub"),
    )
    op.create_index(
        "ix_outgoing_payment_job_owner_worker_id",
        "outgoing_payment_job",
        ["owner_worker_id"],
    )
    op.create_index(
        "ix_outgoing_payment_job_status",
        "outgoing_payment_job",
        ["status"],
    )
    op.create_index(
        "ix_outgoing_payment_job_lease_expires_at",
        "outgoing_payment_job",
        ["lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_outgoing_payment_job_lease_expires_at",
        table_name="outgoing_payment_job",
    )
    op.drop_index(
        "ix_outgoing_payment_job_status",
        table_name="outgoing_payment_job",
    )
    op.drop_index(
        "ix_outgoing_payment_job_owner_worker_id",
        table_name="outgoing_payment_job",
    )
    op.drop_table("outgoing_payment_job")
