"""add_wcs_header_column

Revision ID: 9e6019939191
Revises: 96eb1f6405dd
Create Date: 2026-02-12 09:18:37.811059+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '9e6019939191'
down_revision: Union[str, None] = '96eb1f6405dd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Helper to check if column exists
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()
    
    if 'images' in tables:
        columns = [c['name'] for c in inspector.get_columns('images')]
        if 'wcs_header' not in columns:
            op.add_column('images', sa.Column('wcs_header', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    # Helper to check if column exists
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    if 'images' in tables:
        columns = [c['name'] for c in inspector.get_columns('images')]
        if 'wcs_header' in columns:
            op.drop_column('images', 'wcs_header')
