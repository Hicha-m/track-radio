"""Transactional hourly counters and durable prepared rankings."""

from alembic import op

from app.models import PlayCount, RankingCount, RankingState
from app.services.play_counts import backfill, install
from app.services.sqlite_runtime import prepare_temp_directory

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    prepare_temp_directory()
    for table in (PlayCount.__table__, RankingState.__table__, RankingCount.__table__):
        table.create(connection)
    install(connection)
    backfill(connection)


def downgrade():
    connection = op.get_bind()
    if connection.dialect.name == "sqlite":
        for name in ("insert", "delete", "update"):
            connection.exec_driver_sql(f"DROP TRIGGER IF EXISTS plays_count_{name}")
    for table in (RankingCount.__table__, RankingState.__table__, PlayCount.__table__):
        table.drop(connection)
