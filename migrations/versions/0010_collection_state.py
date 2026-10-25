"""Keep the last observation across skipped blacklisted broadcasts and restarts."""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("stations", sa.Column("collection_state", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("stations", "collection_state")
