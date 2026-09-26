"""
Data Migration Model
Records which one-off data repairs (see app/services/data_migrations.py) have
run on this install, so each runs exactly once instead of on every startup.
"""

from sqlalchemy import Column, String, Float, Text, DateTime
from sqlalchemy.sql import func

from app.database import Base


class DataMigration(Base):
    __tablename__ = "data_migrations"

    id = Column(String(100), primary_key=True)          # registry id, e.g. "0002_repair_field_radius"
    status = Column(String(20), nullable=False)         # "applied" | "failed"
    applied_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    duration_seconds = Column(Float, nullable=True)
    result = Column(Text, nullable=True)                # JSON summary on success, error text on failure

    def __repr__(self):
        return f"<DataMigration(id='{self.id}', status='{self.status}')>"
