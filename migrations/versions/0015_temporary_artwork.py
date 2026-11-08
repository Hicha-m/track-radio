"""Station artwork fallback independent of the selected recording."""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("track_metadata", sa.Column("temporary_cover_url", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("track_metadata", "temporary_cover_url")
