"""add_targets

Revision ID: f2b8d4fa0002
Revises: f1a7c3e90001
Create Date: 2026-09-25 10:30:00.000000

Adds images.target_key and images.target_source (F2 - target integration
dashboard), plus the target_goals table for optional per-target/per-filter
integration goals. Schema-only migration; resolution is performed by
app/services/targets.py and backfilled by app/scripts/backfill_targets.py.

NOTE: down_revision is f1a7c3e90001 while F2 develops in parallel with F7 and
F16 (see docs/design/README.md §3). It must be rebased onto F7's migration
head before merge.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f2b8d4fa0002'
down_revision = 'f1a7c3e90001'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    columns = {c['name'] for c in inspector.get_columns('images')}

    if 'target_key' not in columns:
        op.add_column('images', sa.Column('target_key', sa.String(length=64), nullable=True))

    if 'target_source' not in columns:
        op.add_column('images', sa.Column('target_source', sa.String(length=20), nullable=True))

    indexes = {i['name'] for i in inspector.get_indexes('images')}

    if 'ix_images_target_key' not in indexes:
        op.create_index('ix_images_target_key', 'images', ['target_key'])

    if 'ix_images_target_frame' not in indexes:
        op.create_index('ix_images_target_frame', 'images', ['target_key', 'frame_type'])

    if not inspector.has_table('target_goals'):
        op.create_table(
            'target_goals',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('target_key', sa.String(length=64), nullable=False),
            sa.Column('filter_group', sa.String(length=20), nullable=False),
            sa.Column('goal_seconds', sa.Float(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.UniqueConstraint('target_key', 'filter_group', name='uq_target_goal'),
        )
        op.create_index('ix_target_goals_target_key', 'target_goals', ['target_key'])
    else:
        # Table exists (e.g. created fresh via create_all) - ensure its index exists too.
        existing_indexes = {i['name'] for i in inspector.get_indexes('target_goals')}
        if 'ix_target_goals_target_key' not in existing_indexes:
            op.create_index('ix_target_goals_target_key', 'target_goals', ['target_key'])


def downgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if inspector.has_table('target_goals'):
        op.drop_table('target_goals')

    indexes = {i['name'] for i in inspector.get_indexes('images')}
    if 'ix_images_target_frame' in indexes:
        op.drop_index('ix_images_target_frame', table_name='images')
    if 'ix_images_target_key' in indexes:
        op.drop_index('ix_images_target_key', table_name='images')

    columns = {c['name'] for c in inspector.get_columns('images')}
    if 'target_source' in columns:
        op.drop_column('images', 'target_source')
    if 'target_key' in columns:
        op.drop_column('images', 'target_key')
