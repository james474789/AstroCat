"""
Image-Catalog Match Model
Junction table linking images to catalog objects they contain.
"""

import enum
from datetime import datetime

from sqlalchemy import Column, Integer, Float, String, Boolean, Enum, DateTime, ForeignKey, UniqueConstraint, Index
from sqlalchemy.orm import relationship

from app.database import Base


class CatalogType(str, enum.Enum):
    """Catalog types for matching."""
    MESSIER = "MESSIER"
    NGC = "NGC"
    IC = "IC"
    NAMED_STAR = "NAMED_STAR" # Named Stars


class ImageCatalogMatch(Base):
    """
    Junction table linking images to catalog objects.
    An image can contain multiple objects, and an object can appear in multiple images.
    """
    __tablename__ = "image_catalog_matches"
    
    id = Column(Integer, primary_key=True, index=True)
    
    # Foreign Keys
    image_id = Column(
        Integer, 
        ForeignKey("images.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    
    # Catalog reference (stores type + designation for flexibility)
    catalog_type = Column(Enum(CatalogType), nullable=False)
    catalog_designation = Column(String(20), nullable=False, index=True)
    
    # Match quality metrics
    angular_separation_degrees = Column(Float, nullable=True)  # Distance from image center
    is_in_field = Column(Boolean, default=True)  # Is the object within the image field?
    
    # How the match was determined
    match_source = Column(String(50), nullable=True)  # "AUTOMATIC", "MANUAL", "HEADER"
    confidence_score = Column(Float, nullable=True)  # 0.0 to 1.0
    
    # Timestamps
    matched_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    
    # Relationships
    image = relationship("Image", back_populates="catalog_matches")
    
    # Ensure no duplicate matches
    __table_args__ = (
        UniqueConstraint(
            'image_id', 'catalog_type', 'catalog_designation',
            name='uq_image_catalog_match'
        ),
        Index('ix_image_catalog_matches_type', 'catalog_type'),
    )
    
    def __repr__(self):
        return f"<ImageCatalogMatch(image_id={self.image_id}, {self.catalog_type.value} {self.catalog_designation})>"
