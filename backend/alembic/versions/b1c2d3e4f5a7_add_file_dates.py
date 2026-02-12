"""Add file modification and creation dates

Revision ID: 2026_02_08_1029-add_file_dates
Revises: b1c2d3e4f5a6
Create Date: 2026-02-08 10:29:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b1c2d3e4f5a7'
down_revision = 'b1c2d3e4f5a6'
branch_labels = None
depends_on = None


def upgrade():
    # Helper to check if column exists
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c['name'] for c in inspector.get_columns('images')]

    # Add file_last_modified column if it doesn't exist
    if 'file_last_modified' not in columns:
        op.add_column('images', sa.Column('file_last_modified', sa.DateTime(), nullable=True))
        op.create_index(op.f('ix_images_file_last_modified'), 'images', ['file_last_modified'], unique=False)
    
    # Add file_created column if it doesn't exist
    if 'file_created' not in columns:
        op.add_column('images', sa.Column('file_created', sa.DateTime(), nullable=True))
        op.create_index(op.f('ix_images_file_created'), 'images', ['file_created'], unique=False)


def downgrade():
    # Helper to check if column exists
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c['name'] for c in inspector.get_columns('images')]

    # Remove file_created column
    if 'file_created' in columns:
        try:
            op.drop_index(op.f('ix_images_file_created'), table_name='images')
        except:
            pass
        op.drop_column('images', 'file_created')
    
    # Remove file_last_modified column
    if 'file_last_modified' in columns:
        try:
            op.drop_index(op.f('ix_images_file_last_modified'), table_name='images')
        except:
            pass
        op.drop_column('images', 'file_last_modified')
