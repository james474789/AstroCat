"""
User Model
Stores user credentials and profile information.
"""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Index
from app.database import Base

class User(Base):
    """
    User model for authentication.
    """
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False)
    hashed_password = Column(String(1024), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_admin = Column(Boolean, default=False, nullable=False)
    
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    __table_args__ = (
        Index('ix_users_username', 'email', unique=True),
    )
    
    def __repr__(self):
        return f"<User(id={self.id}, email='{self.email}')>"
