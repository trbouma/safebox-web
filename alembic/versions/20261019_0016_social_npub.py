"""Optional public social identity, independent of the Acorn binding."""
from alembic import op
import sqlalchemy as sa

revision = "20261019_0016"
down_revision = "20261016_0015"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("claimed_handle", sa.Column("social_npub", sa.String(), nullable=True))


def downgrade():
    # A rollback must not silently republish wallet keys as chosen social identities.
    if op.get_bind().execute(sa.text("SELECT COUNT(*) FROM claimed_handle WHERE social_npub IS NOT NULL")).scalar():
        raise RuntimeError("Clear social identities deliberately before downgrading")
    op.drop_column("claimed_handle", "social_npub")
