"""add_data_migrations

Revision ID: c6e8a2d31006
Revises: b5f7091005
Create Date: 2026-09-26 15:00:00.000000

Adds the data_migrations table, which records which one-off data repairs
(app/services/data_migrations.py) have run so the worker applies each one
exactly once per install, in the background, after startup.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c6e8a2d31006'
down_revision = 'b5f7091005'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    # May already exist on a fresh install (created by create_all).
    if not inspector.has_table('data_migrations'):
        op.create_table(
            'data_migrations',
            sa.Column('id', sa.String(length=100), primary_key=True),
            sa.Column('status', sa.String(length=20), nullable=False),
            sa.Column('applied_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
            sa.Column('duration_seconds', sa.Float(), nullable=True),
            sa.Column('result', sa.Text(), nullable=True),
        )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table('data_migrations'):
        op.drop_table('data_migrations')
