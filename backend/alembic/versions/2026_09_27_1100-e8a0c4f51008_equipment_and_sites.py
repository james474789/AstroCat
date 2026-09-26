"""equipment_and_sites

Revision ID: e8a0c4f51008
Revises: d7f9b3e41007
Create Date: 2026-09-27 11:00:00.000000

R0 (docs/design/P0-R0-equipment-sites.md §4.1): cameras, optics, filters,
rigs (+ rig_filters) and sites, the partial unique indexes for "one mounted
rig" / "one default site", and images.rig_id / rig_source / site_id.

Schema only: no rows are created or rewritten. Rigs and sites are created
by the user (Equipment page); assignment runs as a Celery task.

Defensive: on a fresh install create_all has already built all of this from
the models, so inspect before creating anything.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'e8a0c4f51008'
down_revision = 'd7f9b3e41007'
branch_labels = None
depends_on = None


def _timestamps():
    return [
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('now()')),
    ]


def _jsonb_list(name):
    return sa.Column(name, postgresql.JSONB(astext_type=sa.Text()), nullable=False,
                     server_default=sa.text("'[]'::jsonb"))


def _index_names(conn, table):
    return {ix['name'] for ix in sa.inspect(conn).get_indexes(table)}


def upgrade():
    conn = op.get_bind()
    tables = set(sa.inspect(conn).get_table_names())

    if 'cameras' not in tables:
        op.create_table(
            'cameras',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('name', sa.String(100), nullable=False, unique=True),
            sa.Column('maker', sa.String(50), nullable=True),
            sa.Column('sensor_width_px', sa.Integer(), nullable=True),
            sa.Column('sensor_height_px', sa.Integer(), nullable=True),
            sa.Column('pixel_size_um', sa.Float(), nullable=True),
            sa.Column('is_color', sa.Boolean(), nullable=True),
            sa.Column('is_cooled', sa.Boolean(), nullable=True),
            _jsonb_list('match_patterns'),
            sa.Column('source', sa.String(20), nullable=False, server_default='MANUAL'),
            sa.Column('external_ref', sa.String(50), nullable=True),
            sa.Column('notes', sa.Text(), nullable=True),
            *_timestamps(),
        )
    if 'ix_cameras_id' not in _index_names(conn, 'cameras'):
        op.create_index('ix_cameras_id', 'cameras', ['id'])

    if 'optics' not in tables:
        op.create_table(
            'optics',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('name', sa.String(100), nullable=False, unique=True),
            sa.Column('kind', sa.String(10), nullable=False, server_default='TELESCOPE'),
            sa.Column('aperture_mm', sa.Float(), nullable=True),
            sa.Column('focal_length_mm', sa.Float(), nullable=False),
            sa.Column('source', sa.String(20), nullable=False, server_default='MANUAL'),
            sa.Column('external_ref', sa.String(50), nullable=True),
            sa.Column('notes', sa.Text(), nullable=True),
            *_timestamps(),
        )
    if 'ix_optics_id' not in _index_names(conn, 'optics'):
        op.create_index('ix_optics_id', 'optics', ['id'])

    if 'filters' not in tables:
        op.create_table(
            'filters',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('name', sa.String(100), nullable=False, unique=True),
            sa.Column('band', sa.String(20), nullable=False, server_default='Other'),
            sa.Column('bandwidth_nm', sa.Float(), nullable=True),
            _jsonb_list('match_patterns'),
            sa.Column('source', sa.String(20), nullable=False, server_default='MANUAL'),
            sa.Column('external_ref', sa.String(50), nullable=True),
            *_timestamps(),
        )
    if 'ix_filters_id' not in _index_names(conn, 'filters'):
        op.create_index('ix_filters_id', 'filters', ['id'])

    if 'rigs' not in tables:
        op.create_table(
            'rigs',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('name', sa.String(150), nullable=False, unique=True),
            sa.Column('camera_id', sa.Integer(), sa.ForeignKey('cameras.id'), nullable=False),
            sa.Column('optic_id', sa.Integer(), sa.ForeignKey('optics.id'), nullable=False),
            sa.Column('modifier_name', sa.String(50), nullable=True),
            sa.Column('modifier_factor', sa.Float(), nullable=False, server_default=sa.text('1.0')),
            sa.Column('binning', sa.Integer(), nullable=False, server_default=sa.text('1')),
            sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
            sa.Column('is_mounted', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('mount_name', sa.String(100), nullable=True),
            sa.Column('measured_scale_arcsec', sa.Float(), nullable=True),
            sa.Column('measured_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
            *_timestamps(),
        )
    rig_indexes = _index_names(conn, 'rigs')
    if 'ix_rigs_id' not in rig_indexes:
        op.create_index('ix_rigs_id', 'rigs', ['id'])
    if 'ix_rigs_camera_id' not in rig_indexes:
        op.create_index('ix_rigs_camera_id', 'rigs', ['camera_id'])
    if 'ix_rigs_optic_id' not in rig_indexes:
        op.create_index('ix_rigs_optic_id', 'rigs', ['optic_id'])
    if 'uq_rigs_mounted' not in rig_indexes:
        op.create_index('uq_rigs_mounted', 'rigs', ['is_mounted'], unique=True,
                        postgresql_where=sa.text('is_mounted'))

    if 'rig_filters' not in tables:
        op.create_table(
            'rig_filters',
            sa.Column('rig_id', sa.Integer(), sa.ForeignKey('rigs.id', ondelete='CASCADE'), primary_key=True),
            sa.Column('filter_id', sa.Integer(), sa.ForeignKey('filters.id', ondelete='CASCADE'), primary_key=True),
        )

    if 'sites' not in tables:
        op.create_table(
            'sites',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('name', sa.String(100), nullable=False, unique=True),
            sa.Column('latitude', sa.Float(), nullable=False),
            sa.Column('longitude', sa.Float(), nullable=False),
            sa.Column('elevation_m', sa.Float(), nullable=True),
            sa.Column('timezone', sa.String(64), nullable=False),
            sa.Column('bortle', sa.Integer(), nullable=True),
            sa.Column('sqm', sa.Float(), nullable=True),
            sa.Column('typical_seeing_arcsec', sa.Float(), nullable=False, server_default=sa.text('2.5')),
            sa.Column('is_default', sa.Boolean(), nullable=False, server_default=sa.text('false')),
            sa.Column('horizon', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
            sa.Column('horizon_source', sa.String(10), nullable=True),
            *_timestamps(),
        )
    site_indexes = _index_names(conn, 'sites')
    if 'ix_sites_id' not in site_indexes:
        op.create_index('ix_sites_id', 'sites', ['id'])
    if 'uq_sites_default' not in site_indexes:
        op.create_index('uq_sites_default', 'sites', ['is_default'], unique=True,
                        postgresql_where=sa.text('is_default'))

    # images.rig_id / rig_source / site_id
    columns = {c['name'] for c in sa.inspect(conn).get_columns('images')}
    if 'rig_id' not in columns:
        op.add_column('images', sa.Column('rig_id', sa.Integer(), nullable=True))
    if 'rig_source' not in columns:
        op.add_column('images', sa.Column('rig_source', sa.String(10), nullable=True))
    if 'site_id' not in columns:
        op.add_column('images', sa.Column('site_id', sa.Integer(), nullable=True))

    fks = {(tuple(fk['constrained_columns']), fk['referred_table'])
           for fk in sa.inspect(conn).get_foreign_keys('images')}
    if (('rig_id',), 'rigs') not in fks:
        op.create_foreign_key('fk_images_rig_id_rigs', 'images', 'rigs', ['rig_id'], ['id'], ondelete='SET NULL')
    if (('site_id',), 'sites') not in fks:
        op.create_foreign_key('fk_images_site_id_sites', 'images', 'sites', ['site_id'], ['id'], ondelete='SET NULL')

    image_indexes = _index_names(conn, 'images')
    if 'ix_images_rig_id' not in image_indexes:
        op.create_index('ix_images_rig_id', 'images', ['rig_id'])
    if 'ix_images_site_id' not in image_indexes:
        op.create_index('ix_images_site_id', 'images', ['site_id'])


def downgrade():
    conn = op.get_bind()

    image_indexes = _index_names(conn, 'images')
    for name in ('ix_images_rig_id', 'ix_images_site_id'):
        if name in image_indexes:
            op.drop_index(name, table_name='images')
    for fk in sa.inspect(conn).get_foreign_keys('images'):
        if fk['referred_table'] in ('rigs', 'sites') and fk.get('name'):
            op.drop_constraint(fk['name'], 'images', type_='foreignkey')
    columns = {c['name'] for c in sa.inspect(conn).get_columns('images')}
    for name in ('site_id', 'rig_source', 'rig_id'):
        if name in columns:
            op.drop_column('images', name)

    tables = set(sa.inspect(conn).get_table_names())
    for table in ('rig_filters', 'rigs', 'sites', 'filters', 'optics', 'cameras'):
        if table in tables:
            op.drop_table(table)
