"""rig_target_size_window

Revision ID: d3f5b9e06013
Revises: c2e4a8d95012
Create Date: 2026-09-30 12:00:00.000000

R1b (docs/design/20260930-R1b-rig-aware-picking.md §1): per-rig target-size window for
recommendations, rigs.min_target_arcmin / max_target_arcmin. NULL means "use
the default from the field of view".

Schema only: no rows are rewritten.

Defensive: on a fresh install create_all has already built the columns from
the model, so inspect before adding.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd3f5b9e06013'
down_revision = 'c2e4a8d95012'
branch_labels = None
depends_on = None


_COLUMNS = ('min_target_arcmin', 'max_target_arcmin')


def upgrade():
    conn = op.get_bind()
    columns = {c['name'] for c in sa.inspect(conn).get_columns('rigs')}
    for name in _COLUMNS:
        if name not in columns:
            op.add_column('rigs', sa.Column(name, sa.Float(), nullable=True))


def downgrade():
    conn = op.get_bind()
    columns = {c['name'] for c in sa.inspect(conn).get_columns('rigs')}
    for name in reversed(_COLUMNS):
        if name in columns:
            op.drop_column('rigs', name)
