"""Add ALLSKY to imagesubtype enum

Revision ID: f5b7d1a28015
Revises: e4a6c0f17014
Create Date: 2026-10-07 12:00:00.000000

Adds the ALLSKY value to the existing imagesubtype enum (images.subtype).
Kept as its own revision because ALTER TYPE ... ADD VALUE must run outside
a transaction (see b5f7091005 for the same pattern).
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'f5b7d1a28015'
down_revision = 'e4a6c0f17014'
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE imagesubtype ADD VALUE IF NOT EXISTS 'ALLSKY'")


def downgrade():
    # Postgres doesn't support removing enum values; leave 'ALLSKY' on imagesubtype.
    pass
