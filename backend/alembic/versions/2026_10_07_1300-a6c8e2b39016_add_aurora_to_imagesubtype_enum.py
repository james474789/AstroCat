"""Add AURORA to imagesubtype enum

Revision ID: a6c8e2b39016
Revises: f5b7d1a28015
Create Date: 2026-10-07 13:00:00.000000

Adds the AURORA value to the existing imagesubtype enum (images.subtype).
Kept as its own revision because ALTER TYPE ... ADD VALUE must run outside
a transaction (see b5f7091005 for the same pattern).
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'a6c8e2b39016'
down_revision = 'f5b7d1a28015'
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE imagesubtype ADD VALUE IF NOT EXISTS 'AURORA'")


def downgrade():
    # Postgres doesn't support removing enum values; leave 'AURORA' on imagesubtype.
    pass
