"""Add plate_solve_provider column

Revision ID: 9155761982ca
Revises: 15ec32f5cfc8
Create Date: 2026-01-26 17:41:21.607837+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import geoalchemy2
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '9155761982ca'
down_revision: Union[str, None] = '15ec32f5cfc8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Safe upgrade: only add the new column
    op.add_column('images', sa.Column('plate_solve_provider', sa.String(length=50), nullable=True))


def downgrade() -> None:
    # Safe downgrade
    op.drop_column('images', 'plate_solve_provider')
