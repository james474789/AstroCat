"""star_quality_metrics

Revision ID: c2e4a8d95012
Revises: b1d3f7c84011
Create Date: 2026-09-27 15:00:00.000000

Q1 (docs/design/20260927-Q1-star-quality.md §5.1): per-image star quality columns
(hfr_px, fwhm_px, eccentricity, star_count), measurement status/version/time
and a star_metrics JSONB of details.

Schema only: no rows are created or rewritten. Existing images are measured
by the app.tasks.quality.sweep beat task, not here.

Defensive: on a fresh install create_all has already built the columns from
the model, so inspect before adding.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'c2e4a8d95012'
down_revision = 'b1d3f7c84011'
branch_labels = None
depends_on = None


_COLUMNS = [
    ('hfr_px', sa.Float()),
    ('fwhm_px', sa.Float()),
    ('eccentricity', sa.Float()),
    ('star_count', sa.Integer()),
    ('star_metrics_status', sa.String(length=12)),
    ('star_metrics_version', sa.SmallInteger()),
    ('star_metrics_at', sa.DateTime()),
    ('star_metrics', postgresql.JSONB()),
]
_STATUS_INDEX = 'ix_images_star_metrics_status'


def upgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = {c['name'] for c in inspector.get_columns('images')}
    for name, type_ in _COLUMNS:
        if name not in columns:
            op.add_column('images', sa.Column(name, type_, nullable=True))
    if _STATUS_INDEX not in {ix['name'] for ix in inspector.get_indexes('images')}:
        op.create_index(_STATUS_INDEX, 'images', ['star_metrics_status'])


def downgrade():
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if _STATUS_INDEX in {ix['name'] for ix in inspector.get_indexes('images')}:
        op.drop_index(_STATUS_INDEX, table_name='images')
    columns = {c['name'] for c in inspector.get_columns('images')}
    for name, _ in reversed(_COLUMNS):
        if name in columns:
            op.drop_column('images', name)
