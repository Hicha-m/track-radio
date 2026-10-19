"""Official station websites for automatic artwork discovery."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("stations", sa.Column("website_url", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("stations", "website_url")
