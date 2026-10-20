"""Persistent global hiding, independent of browsers and catalogue enrichment."""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "global_exclusions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("artist", sa.String(300), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("artist_normalized", sa.Text(), nullable=False),
        sa.Column("title_normalized", sa.Text(), nullable=False),
    )
    op.create_index(
        "ix_global_exclusions_match",
        "global_exclusions",
        ["artist_normalized", "title_normalized"],
    )


def downgrade():
    op.drop_table("global_exclusions")
