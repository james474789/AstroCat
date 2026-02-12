"""Add iso and temperature fields

Revision ID: 96eb1f6405dd
Revises: b1c2d3e4f5a7
Create Date: 2026-02-08 15:40:42.710960+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '96eb1f6405dd'
down_revision: Union[str, None] = 'b1c2d3e4f5a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Helper to check if column exists
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c['name'] for c in inspector.get_columns('images')]

    if 'iso_speed' not in columns:
        op.add_column('images', sa.Column('iso_speed', sa.Integer(), nullable=True))
    if 'temperature_celsius' not in columns:
        op.add_column('images', sa.Column('temperature_celsius', sa.Float(), nullable=True))


def downgrade() -> None:
    # Helper to check if column exists
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [c['name'] for c in inspector.get_columns('images')]

    if 'temperature_celsius' in columns:
        op.drop_column('images', 'temperature_celsius')
    if 'iso_speed' in columns:
        op.drop_column('images', 'iso_speed')
