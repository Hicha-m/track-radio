"""Schema initial."""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "stations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("slug", sa.String(100), nullable=False, unique=True),
        sa.Column("stream_url", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(20), nullable=False, server_default="icy"),
        sa.Column("metadata_url", sa.Text(), nullable=True),
        sa.Column("json_artist_path", sa.String(200), nullable=True),
        sa.Column("json_title_path", sa.String(200), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "plays",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "station_id",
            sa.Integer(),
            sa.ForeignKey("stations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("artist", sa.String(300), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("raw_metadata", sa.Text(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
    )
    op.create_index("ix_plays_station_detected", "plays", ["station_id", "detected_at"])
    op.create_index("ix_plays_title", "plays", ["title"])


def downgrade():
    op.drop_table("plays")
    op.drop_table("stations")

