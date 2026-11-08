"""Bounded, payload-free collection diagnostics and worker probe queue."""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "collection_status",
        sa.Column(
            "station_id",
            sa.Integer(),
            sa.ForeignKey("stations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        *(
            sa.Column(name, sa.DateTime(timezone=True), nullable=True)
            for name in (
                "last_attempt_at",
                "last_success_at",
                "last_observation_at",
                "last_change_at",
                "next_attempt_at",
                "probe_requested_at",
            )
        ),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("runtime", sa.JSON(), nullable=False),
        *(
            sa.Column(name, sa.Integer(), nullable=False)
            for name in (
                "attempts",
                "successes",
                "errors",
                "consecutive_errors",
            )
        ),
    )
    op.create_table(
        "collection_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("attempt_id", sa.String(32), unique=True, nullable=False),
        sa.Column("round_id", sa.String(32), nullable=True),
        sa.Column(
            "station_id",
            sa.Integer(),
            sa.ForeignKey("stations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("source", sa.String(24), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sqlite_autoincrement=True,
    )
    op.create_index("ix_collection_station_id", "collection_events", ["station_id", "id"])
    op.create_index("ix_collection_started", "collection_events", ["started_at"])
    op.create_index("ix_collection_pending", "collection_events", ["kind", "outcome", "id"])


def downgrade():
    op.drop_table("collection_events")
    op.drop_table("collection_status")
