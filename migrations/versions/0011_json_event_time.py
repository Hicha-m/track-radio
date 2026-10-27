"""Configurable event timestamps for JSON station sources."""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("stations", sa.Column("json_started_path", sa.String(200), nullable=True))


def downgrade():
    op.drop_column("stations", "json_started_path")
