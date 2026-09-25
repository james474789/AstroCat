"""add_frame_type

Revision ID: f1a7c3e90001
Revises: a8f9e0d1c2b3
Create Date: 2026-09-25 10:00:00.000000

Adds images.frame_type and images.frame_type_source (F1 — frame-type
classification). frame_type distinguishes LIGHT/DARK/FLAT/BIAS/DARK_FLAT
frames, orthogonal to the existing subtype (processing stage) column.
This is a schema-only migration; classification is performed by the
backfill script app/scripts/backfill_frame_types.py, not here.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'f1a7c3e90001'
down_revision = 'a8f9e0d1c2b3'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    op.execute("""
      DO $$ BEGIN
        CREATE TYPE frametype AS ENUM ('LIGHT','DARK','FLAT','BIAS','DARK_FLAT');
      EXCEPTION WHEN duplicate_object THEN NULL; END $$;
    """)

    inspector = sa.inspect(conn)
    columns = {c['name'] for c in inspector.get_columns('images')}

    if 'frame_type' not in columns:
        op.add_column('images', sa.Column(
            'frame_type',
            postgresql.ENUM('LIGHT', 'DARK', 'FLAT', 'BIAS', 'DARK_FLAT', name='frametype', create_type=False),
            nullable=False,
            server_default='LIGHT',
        ))

    if 'frame_type_source' not in columns:
        op.add_column('images', sa.Column('frame_type_source', sa.String(length=20), nullable=True))

    indexes = {i['name'] for i in inspector.get_indexes('images')}

    if 'ix_images_frame_type' not in indexes:
        op.create_index('ix_images_frame_type', 'images', ['frame_type'])

    if 'ix_images_frame_subtype' not in indexes:
        op.create_index('ix_images_frame_subtype', 'images', ['frame_type', 'subtype'])


def downgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    indexes = {i['name'] for i in inspector.get_indexes('images')}
    if 'ix_images_frame_subtype' in indexes:
        op.drop_index('ix_images_frame_subtype', table_name='images')
    if 'ix_images_frame_type' in indexes:
        op.drop_index('ix_images_frame_type', table_name='images')

    columns = {c['name'] for c in inspector.get_columns('images')}
    if 'frame_type_source' in columns:
        op.drop_column('images', 'frame_type_source')
    if 'frame_type' in columns:
        op.drop_column('images', 'frame_type')

    op.execute("DROP TYPE IF EXISTS frametype;")
