"""Add Sharpless (Sh2) catalog table

Revision ID: a4d6f8091004
Revises: f2b8d4fa0002
Create Date: 2026-09-26 12:00:00.000000

Adds the sh2_catalog table (mirrors caldwell_catalog). The SH2 value on
the catalogtype enum is added separately in b5f7091005_add_sh2_to_catalogtype_enum
- keeping it out of this revision avoids mixing an autocommit_block with
regular DDL in the same migration (see that revision for why).
"""
from alembic import op
import sqlalchemy as sa
from geoalchemy2 import Geography


# revision identifiers, used by Alembic.
revision = 'a4d6f8091004'
down_revision = 'f2b8d4fa0002'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if 'sh2_catalog' not in inspector.get_table_names():
        op.create_table(
            'sh2_catalog',
            sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
            sa.Column('designation', sa.String(length=10), nullable=False),
            sa.Column('sh2_number', sa.Integer(), nullable=False),
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
            sa.Column('major_axis_arcmin', sa.Float(), nullable=True),
            sa.Column('minor_axis_arcmin', sa.Float(), nullable=True),
            sa.UniqueConstraint('designation', name='uq_sh2_catalog_designation'),
            sa.UniqueConstraint('sh2_number', name='uq_sh2_catalog_number')
        )
        op.create_index(op.f('ix_sh2_catalog_designation'), 'sh2_catalog', ['designation'], unique=False)
        op.create_index(op.f('ix_sh2_catalog_sh2_number'), 'sh2_catalog', ['sh2_number'], unique=False)
        op.create_index(op.f('ix_sh2_catalog_source_designation'), 'sh2_catalog', ['source_designation'], unique=False)
        # No explicit index on `location` - geoalchemy2's Geography type has
        # spatial_index=True by default and auto-creates idx_sh2_catalog_location
        # via its own DDL event when the table is created (mirrors caldwell_catalog).


def downgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if 'sh2_catalog' in inspector.get_table_names():
        op.drop_index(op.f('ix_sh2_catalog_source_designation'), table_name='sh2_catalog')
        op.drop_index(op.f('ix_sh2_catalog_sh2_number'), table_name='sh2_catalog')
        op.drop_index(op.f('ix_sh2_catalog_designation'), table_name='sh2_catalog')
        op.drop_table('sh2_catalog')  # also drops the auto-created spatial index
