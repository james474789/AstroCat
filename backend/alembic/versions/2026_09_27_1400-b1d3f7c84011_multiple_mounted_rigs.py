"""multiple_mounted_rigs

Revision ID: b1d3f7c84011
Revises: a0c2e6b73010
Create Date: 2026-09-27 14:00:00.000000

Several rigs can be mounted at once (up to MAX_MOUNTED_RIGS, enforced by
the API): drop the partial unique index that allowed only one.

Schema only: no rows are created or rewritten.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b1d3f7c84011'
down_revision = 'a0c2e6b73010'
branch_labels = None
depends_on = None


def _index_names(conn, table):
    return {ix['name'] for ix in sa.inspect(conn).get_indexes(table)}


def upgrade():
    conn = op.get_bind()
    if 'uq_rigs_mounted' in _index_names(conn, 'rigs'):
        op.drop_index('uq_rigs_mounted', table_name='rigs')


def downgrade():
    conn = op.get_bind()
    # Keep the lowest-id mounted rig so the unique index can be rebuilt.
    conn.execute(sa.text(
        "UPDATE rigs SET is_mounted = false WHERE is_mounted AND id <> "
        "(SELECT min(id) FROM rigs WHERE is_mounted)"))
    if 'uq_rigs_mounted' not in _index_names(conn, 'rigs'):
        op.create_index('uq_rigs_mounted', 'rigs', ['is_mounted'], unique=True,
                        postgresql_where=sa.text('is_mounted'))
