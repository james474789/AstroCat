"""Add SH2 to catalogtype enum

Revision ID: b5f7091005
Revises: a4d6f8091004
Create Date: 2026-09-26 12:01:00.000000

Adds the SH2 value to the existing catalogtype enum (used by
image_catalog_matches.catalog_type), mirroring 5a7e6f8d9c2b's approach
for adding XISF to imageformat. Kept as its own revision, separate from
sh2_catalog's creation, because ALTER TYPE ... ADD VALUE must run outside
a transaction and mixing that with ordinary DDL in one migration caused
the ordinary DDL to be re-executed and fail with "already exists".
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'b5f7091005'
down_revision = 'a4d6f8091004'
branch_labels = None
depends_on = None


def upgrade():
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE catalogtype ADD VALUE IF NOT EXISTS 'SH2'")


def downgrade():
    # Postgres doesn't support removing enum values; leave 'SH2' on catalogtype.
    pass
