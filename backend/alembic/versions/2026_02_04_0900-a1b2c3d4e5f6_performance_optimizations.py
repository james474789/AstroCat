"""Performance optimizations

Revision ID: a1b2c3d4e5f6
Revises: 5893c1ee4205
Create Date: 2026-02-04 09:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = 'eff1c1ee4206'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Enable pg_trgm extension for trigram indexes
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # Helper function to check if index exists
    conn = op.get_bind()
    
    def index_exists(name):
        res = conn.execute(sa.text(f"SELECT 1 FROM pg_indexes WHERE indexname = '{name}'"))
        return res.first() is not None

    # 2. Add B-tree indexes for frequently filtered columns (with existence check)
    if not index_exists('ix_images_camera_name'):
        op.create_index('ix_images_camera_name', 'images', ['camera_name'], unique=False)
    if not index_exists('ix_images_telescope_name'):
        op.create_index('ix_images_telescope_name', 'images', ['telescope_name'], unique=False)
    if not index_exists('ix_images_filter_name'):
        op.create_index('ix_images_filter_name', 'images', ['filter_name'], unique=False)
    if not index_exists('ix_images_exposure_time'):
        op.create_index('ix_images_exposure_time', 'images', ['exposure_time_seconds'], unique=False)
    if not index_exists('ix_images_rating'):
        op.create_index('ix_images_rating', 'images', ['rating'], unique=False)
    if not index_exists('ix_images_gain'):
        op.create_index('ix_images_gain', 'images', ['gain'], unique=False)
    if not index_exists('ix_images_file_name'):
        op.create_index('ix_images_file_name', 'images', ['file_name'], unique=False)
    if not index_exists('ix_images_pixel_scale'):
        op.create_index('ix_images_pixel_scale', 'images', ['pixel_scale_arcsec'], unique=False)
    if not index_exists('ix_images_rotation'):
        op.create_index('ix_images_rotation', 'images', ['rotation_degrees'], unique=False)

    # 3. Add Trigram GIN indexes for ILIKE searches (using native IF NOT EXISTS)
    op.execute("CREATE INDEX IF NOT EXISTS ix_images_file_name_trgm ON images USING gin (file_name gin_trgm_ops)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_images_object_name_trgm ON images USING gin (object_name gin_trgm_ops)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_images_camera_name_trgm ON images USING gin (camera_name gin_trgm_ops)")

    # 4. Add JSONB GIN index for header searches
    if not index_exists('ix_images_raw_header'):
        op.create_index('ix_images_raw_header', 'images', ['raw_header'], unique=False, postgresql_using='gin')

    # 5. Add Materialized View for Catalog Statistics (with check)
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_matviews WHERE matviewname = 'mv_catalog_stats') THEN
                CREATE MATERIALIZED VIEW mv_catalog_stats AS
                SELECT 
                    m.catalog_type,
                    m.catalog_designation,
                    COUNT(DISTINCT m.image_id) as image_count,
                    COALESCE(SUM(i.exposure_time_seconds), 0) as total_exposure_seconds,
                    MAX(m.angular_separation_degrees) as max_separation
                FROM image_catalog_matches m
                JOIN images i ON m.image_id = i.id
                GROUP BY m.catalog_type, m.catalog_designation;
                
                CREATE UNIQUE INDEX ON mv_catalog_stats (catalog_type, catalog_designation);
            END IF;
        END $$;
    """)

    # 6. Add indexes on image_catalog_matches junction table
    if not index_exists('ix_matches_catalog_type_designation'):
        op.create_index('ix_matches_catalog_type_designation', 'image_catalog_matches', ['catalog_type', 'catalog_designation'], unique=False)


def downgrade() -> None:
    # Remove junction table indexes
    op.drop_index('ix_matches_catalog_type_designation', table_name='image_catalog_matches')

    # Remove Materialized View
    op.execute("DROP MATERIALIZED VIEW IF EXISTS mv_catalog_stats")

    # Remove JSONB index
    op.drop_index('ix_images_raw_header', table_name='images')

    # Remove Trigram indexes
    op.execute("DROP INDEX IF EXISTS ix_images_camera_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_images_object_name_trgm")
    op.execute("DROP INDEX IF EXISTS ix_images_file_name_trgm")

    # Remove B-tree indexes
    op.drop_index('ix_images_file_name', table_name='images')
    op.drop_index('ix_images_gain', table_name='images')
    op.drop_index('ix_images_rating', table_name='images')
    op.drop_index('ix_images_exposure_time', table_name='images')
    op.drop_index('ix_images_filter_name', table_name='images')
    op.drop_index('ix_images_telescope_name', table_name='images')
    op.drop_index('ix_images_camera_name', table_name='images')
    op.drop_index('ix_images_rotation', table_name='images')
    op.drop_index('ix_images_pixel_scale', table_name='images')

    # We generally don't drop pg_trgm in case other apps use it
    # op.execute("DROP EXTENSION IF EXISTS pg_trgm")
