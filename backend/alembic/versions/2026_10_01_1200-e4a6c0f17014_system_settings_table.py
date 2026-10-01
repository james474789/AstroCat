"""system_settings_table

Revision ID: e4a6c0f17014
Revises: d3f5b9e06013
Create Date: 2026-10-01 12:00:00.000000

Durable store for the admin system-settings document (mount friendly names,
astrometry provider, ...) that previously lived only in Redis and was lost
whenever the Redis volume was reset or not yet snapshotted.

Schema only: the existing Redis value is imported lazily by the app.

Defensive: on a fresh install create_all has already built the table from the
model, so inspect before creating.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e4a6c0f17014'
down_revision = 'd3f5b9e06013'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    if 'system_settings' in sa.inspect(conn).get_table_names():
        return
    op.create_table(
        'system_settings',
        sa.Column('key', sa.String(100), primary_key=True),
        sa.Column('value', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade():
    conn = op.get_bind()
    if 'system_settings' in sa.inspect(conn).get_table_names():
        op.drop_table('system_settings')
