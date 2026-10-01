"""
System Setting Model
Durable home for the admin-editable system settings document (mount friendly
names, astrometry provider, ...). Redis is only a cache of this row, so the
settings survive a Redis restart, wipe or volume loss.
"""

from sqlalchemy import Column, String, Text, DateTime
from sqlalchemy.sql import func

from app.database import Base


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key = Column(String(100), primary_key=True)         # "system_settings"
    value = Column(Text, nullable=False)                # JSON document
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<SystemSetting(key='{self.key}')>"
