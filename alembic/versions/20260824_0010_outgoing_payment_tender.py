"""capture outgoing payment tender value

Revision ID: 20260824_0010
Revises: 20260824_0009
Create Date: 2026-08-24
"""

from alembic import op
import sqlalchemy as sa


revision = "20260824_0010"
down_revision = "20260824_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "outgoing_payment_job",
        sa.Column("tendered_amount", sa.Float(), nullable=True),
    )
    op.add_column(
        "outgoing_payment_job",
        sa.Column(
            "tendered_currency",
            sa.String(),
            nullable=False,
            server_default="SAT",
        ),
    )


def downgrade() -> None:
    op.drop_column("outgoing_payment_job", "tendered_currency")
    op.drop_column("outgoing_payment_job", "tendered_amount")
