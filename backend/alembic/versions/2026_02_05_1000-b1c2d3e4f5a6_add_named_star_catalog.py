"""Add named star catalog

Revision ID: b1c2d3e4f5a6
Revises: a1b2c3d4e5f6
Create Date: 2026-02-05 10:00:00.000000+00:00

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
import geoalchemy2

# revision identifiers, used by Alembic.
revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Check if table exists
    conn = op.get_bind()
    res = conn.execute(sa.text("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'named_star_catalog')"))
    if res.scalar():
        print("Table 'named_star_catalog' already exists, skipping creation.")
        return

    # 1. Create the named_star_catalog table
    op.create_table(
        'named_star_catalog',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('designation', sa.String(length=50), nullable=False),
        sa.Column('common_name', sa.String(length=100), nullable=True),
        sa.Column('hip_id', sa.String(length=20), nullable=True),
        sa.Column('hd_id', sa.String(length=20), nullable=True),
        sa.Column('ra_degrees', sa.Float(), nullable=False),
        sa.Column('dec_degrees', sa.Float(), nullable=False),
        sa.Column('location', geoalchemy2.types.Geography(geometry_type='POINT', srid=4326, from_text='ST_GeomFromWKB', name='geography'), nullable=True),
        sa.Column('magnitude', sa.Float(), nullable=True),
        sa.Column('spectral_type', sa.String(length=20), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    
    # 2. Create indexes
    op.create_index(op.f('ix_named_star_catalog_id'), 'named_star_catalog', ['id'], unique=False)
    op.create_index(op.f('ix_named_star_catalog_designation'), 'named_star_catalog', ['designation'], unique=True)
    op.create_index(op.f('ix_named_star_catalog_common_name'), 'named_star_catalog', ['common_name'], unique=False)
    op.create_index('idx_named_star_catalog_location', 'named_star_catalog', ['location'], unique=False, postgresql_using='gist')


def downgrade() -> None:
    op.drop_index(op.f('ix_named_star_catalog_common_name'), table_name='named_star_catalog')
    op.drop_index(op.f('ix_named_star_catalog_designation'), table_name='named_star_catalog')
    op.drop_index(op.f('ix_named_star_catalog_id'), table_name='named_star_catalog')
    op.drop_table('named_star_catalog')
