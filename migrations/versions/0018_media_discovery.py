"""Durable background discovery queue, independent of YouTube search quota."""

from alembic import op

from app.models import MediaDiscovery

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade():
    MediaDiscovery.__table__.create(op.get_bind())


def downgrade():
    MediaDiscovery.__table__.drop(op.get_bind())
