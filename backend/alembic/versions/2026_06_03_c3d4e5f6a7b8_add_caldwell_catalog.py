"""Add Caldwell catalog table

Revision ID: c3d4e5f6a7b8
Revises: 52a1b3c4d5e6
Create Date: 2026-06-03 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from geoalchemy2 import Geography


# revision identifiers, used by Alembic.
revision = 'c3d4e5f6a7b8'
down_revision = '52a1b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade():
    # Check if table already exists
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if 'caldwell_catalog' not in inspector.get_table_names():
        op.create_table(
            'caldwell_catalog',
            sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
            sa.Column('designation', sa.String(length=10), nullable=False),
            sa.Column('caldwell_number', sa.Integer(), nullable=False),
            sa.Column('source_designation', sa.String(length=20), nullable=True),
            sa.Column('common_name', sa.String(length=100), nullable=True),
            sa.Column('aliases', sa.Text(), nullable=True),
            sa.Column('ra_degrees', sa.Float(), nullable=False),
            sa.Column('dec_degrees', sa.Float(), nullable=False),
            sa.Column('location', Geography(geometry_type='POINT', srid=4326), nullable=True),
            sa.Column('object_type', sa.String(length=50), nullable=True),
            sa.Column('object_definition', sa.String(length=100), nullable=True),
            sa.Column('constellation', sa.String(length=50), nullable=True),
            sa.Column('apparent_magnitude', sa.Float(), nullable=True),
            sa.Column('b_magnitude', sa.Float(), nullable=True),
            sa.Column('major_axis_arcmin', sa.Float(), nullable=True),
            sa.Column('minor_axis_arcmin', sa.Float(), nullable=True),
            sa.UniqueConstraint('designation', name='uq_caldwell_catalog_designation'),
            sa.UniqueConstraint('caldwell_number', name='uq_caldwell_catalog_number')
        )
        op.create_index(op.f('ix_caldwell_catalog_designation'), 'caldwell_catalog', ['designation'], unique=False)
        op.create_index(op.f('ix_caldwell_catalog_caldwell_number'), 'caldwell_catalog', ['caldwell_number'], unique=False)
        op.create_index(op.f('ix_caldwell_catalog_source_designation'), 'caldwell_catalog', ['source_designation'], unique=False)


def downgrade():
    # Check if table exists before dropping
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if 'caldwell_catalog' in inspector.get_table_names():
        op.drop_index(op.f('ix_caldwell_catalog_source_designation'), table_name='caldwell_catalog')
        op.drop_index(op.f('ix_caldwell_catalog_caldwell_number'), table_name='caldwell_catalog')
        op.drop_index(op.f('ix_caldwell_catalog_designation'), table_name='caldwell_catalog')
        op.drop_table('caldwell_catalog')
