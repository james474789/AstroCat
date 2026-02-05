from sqlalchemy import Column, Integer, BigInteger, Float, DateTime, String
from sqlalchemy.sql import func
from app.database import Base

class SystemStats(Base):
    __tablename__ = "system_stats"

    id = Column(Integer, primary_key=True, index=True)
    category = Column(String, unique=True, index=True) # e.g., "thumbnails"
    
    count = Column(BigInteger, default=0)
    size_bytes = Column(BigInteger, default=0)
    
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    @property
    def size_mb(self):
        return round(self.size_bytes / (1024 * 1024), 2)
