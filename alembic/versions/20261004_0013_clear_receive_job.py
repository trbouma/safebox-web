"""Track background incoming Clear checks without storing secrets."""
from alembic import op
import sqlalchemy as sa

revision = "20261004_0013"
down_revision = "20260922_0012"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("clear_receive_job",
        sa.Column("npub", sa.String(), primary_key=True),
        sa.Column("owner_token", sa.String(), nullable=False),
        sa.Column("owner_worker_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("phase", sa.String(), nullable=False),
        sa.Column("stored_count", sa.Integer(), nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=False))
    for column in ("owner_worker_id", "status", "lease_expires_at"):
        op.create_index(f"ix_clear_receive_job_{column}", "clear_receive_job", [column])


def downgrade():
    op.drop_table("clear_receive_job")
