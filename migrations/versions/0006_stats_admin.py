"""Stats identities, administrative overrides and enrichment progress."""

import json

import sqlalchemy as sa
from alembic import op

from app.services.track_identity import identity

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    for name, kind, default, nullable in (
        ("lyrics_status", sa.String(20), "pending", False),
        ("automatic_done", sa.Boolean(), sa.false(), False),
        ("attempts", sa.JSON(), "{}", False),
        ("revision", sa.Integer(), "0", False),
        ("primary_genre", sa.String(100), None, True),
    ):
        op.add_column(
            "track_metadata", sa.Column(name, kind, server_default=default, nullable=nullable)
        )
    op.create_index("ix_track_metadata_primary_genre", "track_metadata", ["primary_genre"])
    op.add_column("stations", sa.Column("enabled_override", sa.Boolean(), nullable=True))
    op.add_column(
        "stations",
        sa.Column("config_enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.add_column(
        "stations", sa.Column("configured", sa.Boolean(), server_default=sa.true(), nullable=False)
    )
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE stations SET config_enabled = enabled"))
    op.add_column("plays", sa.Column("track_id", sa.String(64), nullable=True))
    cursor = 0
    while True:
        rows = (
            conn.execute(
                sa.text(
                    "SELECT id, artist, title FROM plays WHERE id > :cursor ORDER BY id LIMIT 250"
                ),
                {"cursor": cursor},
            )
            .mappings()
            .all()
        )
        if not rows:
            break
        conn.execute(
            sa.text("UPDATE plays SET track_id = :key WHERE id = :id"),
            [{"id": r["id"], "key": identity(r["artist"], r["title"])} for r in rows],
        )
        cursor = rows[-1]["id"]
    op.create_index("ix_plays_track_id", "plays", ["track_id"])
    op.create_index(
        "ix_plays_track_station_detected", "plays", ["track_id", "station_id", "detected_at"]
    )
    # Preserve already validated lyrics, including manual choices.
    conn.execute(
        sa.text(
            "UPDATE track_metadata SET lyrics_status = 'matched' "
            "WHERE lyrics IS NOT NULL AND CAST(lyrics AS TEXT) != 'null'"
        )
    )
    rows = conn.execute(
        sa.text("SELECT id, selected FROM track_metadata WHERE selected IS NOT NULL")
    )
    for row in rows.mappings():
        selected = (
            json.loads(row["selected"]) if isinstance(row["selected"], str) else row["selected"]
        )
        if selected and selected.get("genres"):
            conn.execute(
                sa.text("UPDATE track_metadata SET primary_genre = :genre WHERE id = :id"),
                {"genre": selected["genres"][0], "id": row["id"]},
            )


def downgrade():
    op.drop_index("ix_plays_track_station_detected", table_name="plays")
    op.drop_index("ix_plays_track_id", table_name="plays")
    op.drop_column("plays", "track_id")
    for name in ("configured", "config_enabled", "enabled_override"):
        op.drop_column("stations", name)
    op.drop_index("ix_track_metadata_primary_genre", table_name="track_metadata")
    for name in ("primary_genre", "revision", "attempts", "automatic_done", "lyrics_status"):
        op.drop_column("track_metadata", name)
