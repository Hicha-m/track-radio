"""Persistent music metadata, candidates and manual corrections."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "track_metadata",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("artist", sa.String(300), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("selected", sa.JSON(), nullable=True),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("lyrics", sa.JSON(), nullable=True),
        sa.Column("lyrics_candidates", sa.JSON(), nullable=False),
        sa.Column("manual", sa.Boolean(), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_track_metadata_retry_at", "track_metadata", ["retry_at"])


def downgrade():
    op.drop_table("track_metadata")
