"""Track bounded provider delivery retries.

Revision ID: 20260823_0008
Revises: 20260822_0007
Create Date: 2026-08-23
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260823_0008"
down_revision = "20260822_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("provider_payment") as batch_op:
        batch_op.add_column(
            sa.Column(
                "delivery_attempts",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("provider_payment") as batch_op:
        batch_op.drop_column("delivery_attempts")
