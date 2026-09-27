"""recommendation_feedback

Revision ID: a0c2e6b73010
Revises: f9b1d5a62009
Create Date: 2026-09-27 13:00:00.000000

R2a (docs/design/R2a-feedback-dashboard.md §3): per-user recommendation
feedback (recommendation_target_state), the append-only action log
(recommendation_events) and what Tonight showed (recommendation_impressions).

Schema only: no rows are created or rewritten.

Defensive: on a fresh install create_all has already built these from the
models, so inspect before creating anything.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'a0c2e6b73010'
down_revision = 'f9b1d5a62009'
branch_labels = None
depends_on = None


def _index_names(conn, table):
    return {ix['name'] for ix in sa.inspect(conn).get_indexes(table)}


def _user_fk():
    return sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)


def upgrade():
    conn = op.get_bind()
    tables = set(sa.inspect(conn).get_table_names())

    if 'recommendation_target_state' not in tables:
        op.create_table(
            'recommendation_target_state',
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
            sa.Column('target_key', sa.String(64), primary_key=True),
            sa.Column('pinned', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('snoozed_until', sa.Date(), nullable=True),
            sa.Column('dismissed', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('dismiss_reason', sa.String(20), nullable=True),
            sa.Column('note', sa.String(200), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        )
    if 'ix_recommendation_target_state_updated_at' not in _index_names(conn, 'recommendation_target_state'):
        op.create_index('ix_recommendation_target_state_updated_at', 'recommendation_target_state', ['updated_at'])

    if 'recommendation_events' not in tables:
        op.create_table(
            'recommendation_events',
            sa.Column('id', sa.Integer(), primary_key=True),
            _user_fk(),
            sa.Column('target_key', sa.String(64), nullable=False),
            sa.Column('action', sa.String(12), nullable=False),
            sa.Column('night', sa.Date(), nullable=True),
            sa.Column('lane', sa.String(20), nullable=True),
            sa.Column('rank', sa.Integer(), nullable=True),
            sa.Column('score', sa.Float(), nullable=True),
            sa.Column('rig_id', sa.Integer(), nullable=True),
            sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        )
    existing = _index_names(conn, 'recommendation_events')
    if 'ix_recommendation_events_target_key' not in existing:
        op.create_index('ix_recommendation_events_target_key', 'recommendation_events', ['target_key'])
    if 'ix_recommendation_events_created_at' not in existing:
        op.create_index('ix_recommendation_events_created_at', 'recommendation_events', ['created_at'])
    if 'ix_recommendation_events_user_action' not in existing:
        op.create_index('ix_recommendation_events_user_action', 'recommendation_events', ['user_id', 'action'])

    if 'recommendation_impressions' not in tables:
        op.create_table(
            'recommendation_impressions',
            sa.Column('id', sa.Integer(), primary_key=True),
            _user_fk(),
            sa.Column('night', sa.Date(), nullable=False),
            sa.Column('target_key', sa.String(64), nullable=False),
            sa.Column('site_id', sa.Integer(), nullable=True),
            sa.Column('rig_mode', sa.String(16), nullable=True),
            sa.Column('rig_id', sa.Integer(), nullable=True),
            sa.Column('lane', sa.String(20), nullable=True),
            sa.Column('rank', sa.Integer(), nullable=True),
            sa.Column('score', sa.Float(), nullable=True),
            sa.Column('is_hero', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('first_shown_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
            sa.UniqueConstraint('user_id', 'night', 'target_key', name='uq_recommendation_impression'),
        )


def downgrade():
    conn = op.get_bind()
    tables = set(sa.inspect(conn).get_table_names())
    for table in ('recommendation_impressions', 'recommendation_events', 'recommendation_target_state'):
        if table in tables:
            op.drop_table(table)
