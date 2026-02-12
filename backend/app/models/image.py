"""
Image Model
Stores metadata for astronomical images (FITS, CR2, JPG, etc.)
"""

import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, 
    Enum, Text, BigInteger, Index
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from geoalchemy2 import Geography

from app.database import Base


class ImageSubtype(str, enum.Enum):
    """Classification of image purpose/quality."""
    SUB_FRAME = "SUB_FRAME"                    # Individual exposure
    INTEGRATION_MASTER = "INTEGRATION_MASTER"  # Stacked/processed master
    INTEGRATION_DEPRECATED = "INTEGRATION_DEPRECATED"  # Old/superseded version
    PLANETARY = "PLANETARY"                    # Planetary/Lunar/Solar images


class ImageFormat(str, enum.Enum):
    """Supported image file formats."""
    FITS = "FITS"
    FIT = "FIT"
    CR2 = "CR2"
    CR3 = "CR3"
    ARW = "ARW"
    NEF = "NEF"
    DNG = "DNG"
    JPG = "JPG"
    JPEG = "JPEG"
    PNG = "PNG"
    TIFF = "TIFF"
    TIF = "TIF"
    XISF = "XISF"


class Image(Base):
    """
    Astronomical image metadata model.
    Stores file information, WCS coordinates, exposure data, and catalog matches.
    """
    __tablename__ = "images"
    
    # Primary Key
    id = Column(Integer, primary_key=True, index=True)
    
    # File Information
    file_path = Column(String(1024), unique=True, nullable=False, index=True)
    file_name = Column(String(255), nullable=False)
    file_format = Column(Enum(ImageFormat), nullable=False)
    file_size_bytes = Column(BigInteger, nullable=False)
    file_hash = Column(String(64), nullable=True)  # SHA-256 hash for deduplication
    
    # File System Metadata
    file_last_modified = Column(DateTime, nullable=True, index=True)
    file_created = Column(DateTime, nullable=True, index=True)
    
    # Image Dimensions
    width_pixels = Column(Integer, nullable=True)
    height_pixels = Column(Integer, nullable=True)
    
    # Classification
    subtype = Column(
        Enum(ImageSubtype), 
        default=ImageSubtype.SUB_FRAME, 
        nullable=False
    )
    
    # Plate Solve Status
    is_plate_solved = Column(Boolean, default=False, nullable=False, index=True)
    plate_solve_source = Column(String(50), nullable=True)  # "HEADER", "SIDECAR", "MANUAL"
    plate_solve_provider = Column(String(50), nullable=True) # "NOVA", "LOCAL"
    
    # WCS Coordinates (if plate solved)
    ra_center_degrees = Column(Float, nullable=True)        # Right Ascension (0-360)
    dec_center_degrees = Column(Float, nullable=True)       # Declination (-90 to +90)
    field_radius_degrees = Column(Float, nullable=True)     # Field of view radius
    pixel_scale_arcsec = Column(Float, nullable=True)       # Arcsec per pixel
    rotation_degrees = Column(Float, nullable=True)         # Position angle (0-360)
    
    # PostGIS Geography columns for spatial queries
    center_location = Column(
        Geography(geometry_type='POINT', srid=4326),
        nullable=True
    )
    field_boundary = Column(
        Geography(geometry_type='POLYGON', srid=4326),
        nullable=True
    )
    
    # Exposure Information
    exposure_time_seconds = Column(Float, nullable=True)
    capture_date = Column(DateTime, nullable=True, index=True)
    
    # Equipment Metadata
    camera_name = Column(String(100), nullable=True)
    telescope_name = Column(String(100), nullable=True)
    filter_name = Column(String(50), nullable=True)
    gain = Column(Float, nullable=True)
    iso_speed = Column(Integer, nullable=True)
    temperature_celsius = Column(Float, nullable=True)
    binning = Column(String(10), nullable=True)  # e.g., "1x1", "2x2"
    
    # Photography/Rating Metadata
    rating = Column(Integer, nullable=True)  # 0-5 stars or custom rating from EXIF/metadata
    rating_manually_edited = Column(Boolean, default=False, nullable=True)  # True if rating was manually set by user
    rating_flushed_at = Column(DateTime, nullable=True)  # When the rating was last synced to filesystem
    aperture = Column(Float, nullable=True)  # F-number (e.g., 2.8, 5.6)
    focal_length = Column(Float, nullable=True)  # In mm
    focal_length_35mm = Column(Float, nullable=True)  # 35mm equivalent in mm
    white_balance = Column(String(50), nullable=True)  # e.g., "Auto", "Daylight", "Tungsten"
    metering_mode = Column(String(50), nullable=True)  # e.g., "Matrix", "Center-weighted"
    flash_fired = Column(Boolean, nullable=True)  # True if flash was used
    lens_model = Column(String(100), nullable=True)  # Lens model name
    
    # Additional FITS/EXIF Metadata (JSON-like storage)
    raw_header = Column(JSONB, nullable=True)
    observer_name = Column(String(100), nullable=True)
    object_name = Column(String(100), nullable=True, index=True)  # Target name from header
    site_name = Column(String(100), nullable=True)
    site_latitude = Column(Float, nullable=True)
    site_longitude = Column(Float, nullable=True)
    
    # Thumbnail
    thumbnail_path = Column(String(1024), nullable=True)
    thumbnail_generated_at = Column(DateTime, nullable=True)
    
    # Sidecar File
    sidecar_path = Column(String(1024), nullable=True)

    # Astrometry.net Integration
    astrometry_submission_id = Column(String(50), nullable=True)
    astrometry_job_id = Column(String(50), nullable=True)
    astrometry_url = Column(String(1024), nullable=True)
    astrometry_status = Column(String(20), default="NONE", nullable=False) # NONE, SUBMITTED, PROCESSING, SOLVED, FAILED
    
    # WCS Header (Full SIP Solution)
    wcs_header = Column(JSONB, nullable=True)
    
    # Timestamps
    indexed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    catalog_matches = relationship(
        "ImageCatalogMatch",
        back_populates="image",
        cascade="all, delete-orphan"
    )
    
    # Indexes for common queries
    __table_args__ = (
        Index('ix_images_ra_dec', 'ra_center_degrees', 'dec_center_degrees'),
        Index('ix_images_subtype_capture', 'subtype', 'capture_date'),
        Index('ix_images_format_solved', 'file_format', 'is_plate_solved'),
    )
    
    def __repr__(self):
        return f"<Image(id={self.id}, file_name='{self.file_name}', solved={self.is_plate_solved})>"
    
    @property
    def coordinates_display(self) -> Optional[str]:
        """Format coordinates for display (RA/DEC in standard notation)."""
        if self.ra_center_degrees is None or self.dec_center_degrees is None:
            return None
        
        # Convert RA to hours
        ra_hours = self.ra_center_degrees / 15.0
        ra_h = int(ra_hours)
        ra_m = int((ra_hours - ra_h) * 60)
        ra_s = ((ra_hours - ra_h) * 60 - ra_m) * 60
        
        # Format DEC
        dec_sign = '+' if self.dec_center_degrees >= 0 else '-'
        dec_abs = abs(self.dec_center_degrees)
        dec_d = int(dec_abs)
        dec_m = int((dec_abs - dec_d) * 60)
        dec_s = ((dec_abs - dec_d) * 60 - dec_m) * 60
        
        return f"{ra_h:02d}h {ra_m:02d}m {ra_s:05.2f}s, {dec_sign}{dec_d:02d}° {dec_m:02d}' {dec_s:04.1f}\""
