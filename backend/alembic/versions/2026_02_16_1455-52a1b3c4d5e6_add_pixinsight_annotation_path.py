"""add_pixinsight_annotation_path

Revision ID: 52a1b3c4d5e6
Revises: 9e6019939191
Create Date: 2026-02-16 14:55:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '52a1b3c4d5e6'
down_revision = '9e6019939191'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('images', sa.Column('pixinsight_annotation_path', sa.String(length=1024), nullable=True))


def downgrade():
    op.drop_column('images', 'pixinsight_annotation_path')
