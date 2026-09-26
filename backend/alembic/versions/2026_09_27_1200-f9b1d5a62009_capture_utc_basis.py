"""capture_utc_basis

Revision ID: f9b1d5a62009
Revises: e8a0c4f51008
Create Date: 2026-09-27 12:00:00.000000

R1 (docs/design/R1-recommendation-engine.md §3.2): adds
images.capture_utc_basis (SITE_TZ | DEFAULT_SITE_TZ | CAMERA_UTC), recording
how capture_date_utc of a FITS_LOCAL / EXIF_LOCAL row was derived. Values are
written by the equipment assignment task; existing rows are filled by data
migration 0008_fill_utc_default_site, not here.

Defensive: on a fresh install create_all has already built the column from
the model, so inspect before adding.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f9b1d5a62009'
down_revision = 'e8a0c4f51008'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    columns = [c['name'] for c in sa.inspect(conn).get_columns('images')]
    if 'capture_utc_basis' not in columns:
        op.add_column('images', sa.Column('capture_utc_basis', sa.String(length=20), nullable=True))


def downgrade():
    conn = op.get_bind()
    columns = [c['name'] for c in sa.inspect(conn).get_columns('images')]
    if 'capture_utc_basis' in columns:
        op.drop_column('images', 'capture_utc_basis')
