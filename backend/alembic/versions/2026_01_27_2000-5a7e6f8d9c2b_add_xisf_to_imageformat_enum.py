"""Add XISF to ImageFormat enum

Revision ID: 5a7e6f8d9c2b
Revises: 9155761982ca
Create Date: 2026-01-27 20:00:00.000000+00:00

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '5a7e6f8d9c2b'
down_revision: Union[str, None] = '9155761982ca'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    # Use autocommit for ALTER TYPE as it cannot run in a transaction in some cases
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE imageformat ADD VALUE IF NOT EXISTS 'XISF'")

def downgrade() -> None:
    # Postgres doesn't easily support removing enum values. 
    # Usually we leave them or recreate the type, but for a simple addition it's safer to leave it.
    pass
