"""add_extraction_error_column

Revision ID: a8f9e0d1c2b3
Revises: c3d4e5f6a7b8
Create Date: 2026-09-01 12:00:00.000000

Adds images.extraction_error, populated by process_image when metadata
extraction failed and only a minimal record could be created. This makes
poison files (e.g. FITS with malformed header cards) visible in the UI
instead of being silently re-queued on every scan.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a8f9e0d1c2b3'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c['name'] for c in inspector.get_columns('images')]

    if 'extraction_error' not in columns:
        op.add_column('images', sa.Column('extraction_error', sa.String(length=500), nullable=True))


def downgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c['name'] for c in inspector.get_columns('images')]

    if 'extraction_error' in columns:
        op.drop_column('images', 'extraction_error')
