"""Cover visible/review catalogue counts without reading provider payloads."""

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name == "sqlite":
        op.create_index(
            "ix_track_metadata_review_cover",
            "track_metadata",
            ["artist", "title", "status", "lyrics_status", "id"],
        )
        op.execute("ANALYZE ix_track_metadata_review_cover")


def downgrade():
    if op.get_bind().dialect.name == "sqlite":
        op.drop_index("ix_track_metadata_review_cover", table_name="track_metadata")
