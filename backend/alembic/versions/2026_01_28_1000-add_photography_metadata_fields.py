"""Add photography metadata fields (rating, aperture, focal length, etc.)

Revision ID: 72af1b8d9e4f
Revises: 5a7e6f8d9c2b
Create Date: 2026-01-28 10:00:00.000000+00:00

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '72af1b8d9e4f'
down_revision: Union[str, None] = '5a7e6f8d9c2b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add new columns for photography metadata
    op.add_column('images', sa.Column('rating', sa.Integer(), nullable=True))
    op.add_column('images', sa.Column('aperture', sa.Float(), nullable=True))
    op.add_column('images', sa.Column('focal_length', sa.Float(), nullable=True))
    op.add_column('images', sa.Column('focal_length_35mm', sa.Float(), nullable=True))
    op.add_column('images', sa.Column('white_balance', sa.String(50), nullable=True))
    op.add_column('images', sa.Column('metering_mode', sa.String(50), nullable=True))
    op.add_column('images', sa.Column('flash_fired', sa.Boolean(), nullable=True))
    op.add_column('images', sa.Column('lens_model', sa.String(100), nullable=True))


def downgrade() -> None:
    # Remove columns in reverse order
    op.drop_column('images', 'lens_model')
    op.drop_column('images', 'flash_fired')
    op.drop_column('images', 'metering_mode')
    op.drop_column('images', 'white_balance')
    op.drop_column('images', 'focal_length_35mm')
    op.drop_column('images', 'focal_length')
    op.drop_column('images', 'aperture')
    op.drop_column('images', 'rating')
