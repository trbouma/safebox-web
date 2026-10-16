"""Store NFC generation policy without wallet secrets."""
from alembic import op
import sqlalchemy as sa

revision = "20261016_0014"
down_revision = "20261004_0013"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("nfc_card_policy",
        sa.Column("npub", sa.String(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("active_generation", sa.String(), nullable=True),
        sa.Column("active_expires_at", sa.Integer(), nullable=True),
        sa.Column("pending_generation", sa.String(), nullable=True),
        sa.Column("pending_digest", sa.String(), nullable=True),
        sa.Column("pending_deadline", sa.Integer(), nullable=True))


def downgrade():
    op.drop_table("nfc_card_policy")
