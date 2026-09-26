"""capture_time_provenance

Revision ID: d7f9b3e41007
Revises: c6e8a2d31006
Create Date: 2026-09-27 10:00:00.000000

P0 (docs/design/P0-R0-equipment-sites.md §3.2/§3.5): adds
images.capture_date_utc (+ index) and images.capture_time_source. Values are
filled by the indexer for new files and by data migration
0005_capture_time_provenance for existing rows.

Defensive: on a fresh install create_all has already built these from the
model, so inspect before adding.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd7f9b3e41007'
down_revision = 'c6e8a2d31006'
branch_labels = None
depends_on = None

INDEX_NAME = 'ix_images_capture_date_utc'


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c['name'] for c in inspector.get_columns('images')]

    if 'capture_date_utc' not in columns:
        op.add_column('images', sa.Column('capture_date_utc', sa.DateTime(), nullable=True))
    if 'capture_time_source' not in columns:
        op.add_column('images', sa.Column('capture_time_source', sa.String(length=20), nullable=True))

    indexes = [ix['name'] for ix in sa.inspect(conn).get_indexes('images')]
    if INDEX_NAME not in indexes:
        op.create_index(INDEX_NAME, 'images', ['capture_date_utc'])


def downgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    indexes = [ix['name'] for ix in inspector.get_indexes('images')]
    if INDEX_NAME in indexes:
        op.drop_index(INDEX_NAME, table_name='images')

    columns = [c['name'] for c in inspector.get_columns('images')]
    if 'capture_time_source' in columns:
        op.drop_column('images', 'capture_time_source')
    if 'capture_date_utc' in columns:
        op.drop_column('images', 'capture_date_utc')
