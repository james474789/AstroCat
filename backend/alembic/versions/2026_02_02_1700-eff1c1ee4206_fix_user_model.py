"""fix user model

Revision ID: eff1c1ee4206
Revises: 5893c1ee4205
Create Date: 2026-02-02 17:00:00.000000+00:00

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'eff1c1ee4206'
down_revision: Union[str, None] = '5893c1ee4205'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # This migration is a placeholder for the changes already in the DB
    # that were missing from the repo files.
    # It converts username -> email and adds is_admin to the users table.
    op.add_column('users', sa.Column('email', sa.String(length=255), nullable=True))
    op.execute("UPDATE users SET email = username")
    op.alter_column('users', 'email', nullable=False)
    op.drop_index('ix_users_username', table_name='users')
    op.drop_column('users', 'username')
    op.create_index('ix_users_username', 'users', ['email'], unique=True)
    op.add_column('users', sa.Column('is_admin', sa.Boolean(), server_default='false', nullable=False))


def downgrade() -> None:
    op.drop_column('users', 'is_admin')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.add_column('users', sa.Column('username', sa.String(length=255), nullable=False))
    op.execute("UPDATE users SET username = email")
    op.create_index('ix_users_username', 'users', ['username'], unique=True)
    op.drop_column('users', 'email')
