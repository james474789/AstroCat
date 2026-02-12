"""Add rating_manually_edited flag to track user edits

Revision ID: 8c2e5f6a3d1b
Revises: 72af1b8d9e4f
Create Date: 2026-01-28 14:00:00.000000+00:00

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '8c2e5f6a3d1b'
down_revision: Union[str, None] = '72af1b8d9e4f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add rating_manually_edited flag to track if rating was manually set by user
    op.add_column('images', sa.Column('rating_manually_edited', sa.Boolean(), nullable=True, default=False))


def downgrade() -> None:
    # Remove the column
    op.drop_column('images', 'rating_manually_edited')
