"""Index des consultations globales par date."""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_plays_detected", "plays", ["detected_at"])


def downgrade():
    op.drop_index("ix_plays_detected", table_name="plays")
