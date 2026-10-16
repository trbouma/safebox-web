"""Persistent NFC login attempts and single-use browser challenges."""
from alembic import op
import sqlalchemy as sa

revision = "20261016_0015"
down_revision = "20261016_0014"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("nfc_card_policy", sa.Column("login_failures", sa.Integer(), nullable=False, server_default="0"))
    op.create_table("nfc_login_challenge",
        sa.Column("token_hash", sa.String(), primary_key=True),
        sa.Column("browser_hash", sa.String(), nullable=False),
        sa.Column("issuer", sa.String(), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False),
        sa.Column("payload_digest", sa.String(), nullable=True))
    op.create_index("ix_nfc_login_challenge_expires_at", "nfc_login_challenge", ["expires_at"])
    op.create_table("nfc_login_rate",
        sa.Column("bucket", sa.String(), primary_key=True),
        sa.Column("requests", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.Integer(), nullable=False))
    op.create_index("ix_nfc_login_rate_expires_at", "nfc_login_rate", ["expires_at"])


def downgrade():
    op.drop_table("nfc_login_rate")
    op.drop_table("nfc_login_challenge")
    op.drop_column("nfc_card_policy", "login_failures")
