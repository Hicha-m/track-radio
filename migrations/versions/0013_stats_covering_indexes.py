"""Cover SQLite analytics without reading raw source or catalogue JSON payloads."""

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

INDEXES = (
    ("ix_plays_chart_cover", "plays", ["artist", "title", "detected_at", "station_id", "track_id"]),
    (
        "ix_plays_period_cover",
        "plays",
        ["detected_at", "artist", "title", "station_id", "track_id"],
    ),
    ("ix_track_metadata_genre_cover", "track_metadata", ["id", "primary_genre"]),
)


def upgrade():
    if op.get_bind().dialect.name != "sqlite":
        return
    for name, table, columns in INDEXES:
        op.create_index(name, table, columns)
        # Analyze only these small indexes, not every source/metadata table.
        op.execute(f"ANALYZE {name}")


def downgrade():
    if op.get_bind().dialect.name != "sqlite":
        return
    for name, table, _ in reversed(INDEXES):
        op.drop_index(name, table_name=table)
