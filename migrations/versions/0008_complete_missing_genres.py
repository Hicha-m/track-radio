"""Queue existing automatic matches with unknown genres for a secondary lookup."""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    table = sa.table(
        "track_metadata",
        sa.column("status"),
        sa.column("manual", sa.Boolean()),
        sa.column("automatic_done", sa.Boolean()),
        sa.column("retry_at"),
        sa.column("primary_genre"),
        sa.column("revision", sa.Integer()),
    )
    op.execute(
        table.update()
        .where(
            table.c.status == "matched",
            table.c.manual.is_(False),
            table.c.automatic_done.is_(True),
            table.c.primary_genre.is_(None),
        )
        .values(automatic_done=False, retry_at=None, revision=table.c.revision + 1)
    )


def downgrade():
    pass  # Queued enrichment is safe to finish; no schema or collected data changed.
