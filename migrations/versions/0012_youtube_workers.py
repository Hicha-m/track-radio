"""Durable YouTube matches, shared quotas and worker supervision."""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "youtube_matches",
        sa.Column("track_id", sa.String(64), primary_key=True),
        sa.Column("artist", sa.String(300), nullable=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("candidates", sa.JSON(), nullable=False),
        sa.Column("selected", sa.JSON(), nullable=True),
        sa.Column("manual", sa.Boolean(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "service_quotas",
        sa.Column("name", sa.String(40), primary_key=True),
        sa.Column("day", sa.String(10), nullable=False),
        sa.Column("used", sa.Integer(), nullable=False),
        sa.Column("retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "worker_states",
        sa.Column("name", sa.String(40), primary_key=True),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed", sa.Integer(), nullable=False),
        sa.Column("failed", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("activity", sa.String(100), nullable=False),
    )
    op.create_table(
        "worker_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("worker", sa.String(40), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
    )


def downgrade():
    for table in ("worker_events", "worker_states", "service_quotas", "youtube_matches"):
        op.drop_table(table)
