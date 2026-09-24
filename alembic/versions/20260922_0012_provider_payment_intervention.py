"""Audit explicitly abandoned provider payments."""
from alembic import op
import sqlalchemy as sa

revision = "20260922_0012"
down_revision = "20260827_0011"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "provider_payment_intervention",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("payment_id", sa.String(), nullable=False),
        sa.Column("operator", sa.String(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("before_json", sa.String(), nullable=False),
        sa.Column("after_json", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_provider_payment_intervention_payment_id",
                    "provider_payment_intervention", ["payment_id"])


def downgrade():
    op.drop_table("provider_payment_intervention")
