"""Configuration des adaptateurs HTML sans modifier les historiques."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("stations", sa.Column("html", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("stations", "html")
