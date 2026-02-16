from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, ConfigDict
from app.models.image import ImageFormat, ImageSubtype
from app.models.matches import CatalogType

class CatalogMatchSchema(BaseModel):
    id: Optional[int] = None
    image_id: Optional[int] = None
    catalog_type: CatalogType
    catalog_designation: str
    angular_separation_degrees: Optional[float] = None
    is_in_field: bool
    
    # Match metadata
    match_source: Optional[str] = None
    confidence_score: Optional[float] = None
    matched_at: Optional[datetime] = None
    
    # Pixel coordinates for overlay (dynamically added in API)
    pixel_x: Optional[float] = None
    pixel_y: Optional[float] = None
    
    # Sky Coordinates (dynamically added in API)
    ra_degrees: Optional[float] = None
    dec_degrees: Optional[float] = None
    
    # Catalog object information (dynamically added in API)
    name: Optional[str] = None
    common_name: Optional[str] = None
    designation: Optional[str] = None
    
    model_config = ConfigDict(extra='ignore')

class ImageBase(BaseModel):
    file_name: str
    file_format: ImageFormat
    file_size_bytes: int
    
    # Dimensions
    width_pixels: Optional[int] = None
    height_pixels: Optional[int] = None
    
    # Classification
    subtype: ImageSubtype
    is_plate_solved: bool = False
    plate_solve_source: Optional[str] = None
    
    # Exposure
    exposure_time_seconds: Optional[float] = None
    capture_date: Optional[datetime] = None
    gain: Optional[float] = None
    iso_speed: Optional[int] = None
    temperature_celsius: Optional[float] = None
    
    # Equipment
    camera_name: Optional[str] = None
    telescope_name: Optional[str] = None
    filter_name: Optional[str] = None
    lens_model: Optional[str] = None
    
    # Photography Metadata
    rating: Optional[int] = None
    rating_manually_edited: Optional[bool] = None
    aperture: Optional[float] = None
    focal_length: Optional[float] = None
    focal_length_35mm: Optional[float] = None
    white_balance: Optional[str] = None
    metering_mode: Optional[str] = None
    flash_fired: Optional[bool] = None
    
    # Target
    object_name: Optional[str] = None
    
    # Coordinates (optional display)
    ra_center_degrees: Optional[float] = None
    dec_center_degrees: Optional[float] = None

    # File System Dates
    file_created: Optional[datetime] = None
    file_last_modified: Optional[datetime] = None

class ImageDetail(ImageBase):
    id: int
    file_path: str
    
    # Extended WCS
    field_radius_degrees: Optional[float] = None
    pixel_scale_arcsec: Optional[float] = None
    rotation_degrees: Optional[float] = None
    
    # Extended Meta
    observer_name: Optional[str] = None
    site_name: Optional[str] = None
    site_latitude: Optional[float] = None
    site_longitude: Optional[float] = None
    
    # Astrometry.net
    astrometry_submission_id: Optional[str] = None
    astrometry_job_id: Optional[str] = None
    astrometry_url: Optional[str] = None
    astrometry_status: str = "NONE"
    plate_solve_provider: Optional[str] = None
    plate_solve_provider: Optional[str] = None
    has_annotated_image: bool = False
    has_pixinsight_annotation: bool = False
    
    # Thumbnails
    thumbnail_path: Optional[str] = None
    thumbnail_generated_at: Optional[datetime] = None
    sidecar_path: Optional[str] = None
    rating_flushed_at: Optional[datetime] = None
    
    # Timestamps
    indexed_at: datetime
    updated_at: Optional[datetime] = None
    
    # Full Metadata
    raw_header: Optional[Dict[str, Any]] = None
    
    # PostGIS (excluded from response)
    # center_location and field_boundary are PostGIS types that can't serialize to JSON
    
    # Matches
    catalog_matches: List[CatalogMatchSchema] = []

    model_config = ConfigDict(from_attributes=True, extra='ignore')

class ImageList(ImageBase):
    id: int
    file_path: str
    thumbnail_path: Optional[str] = None
    thumbnail_generated_at: Optional[datetime] = None
    pixel_scale_arcsec: Optional[float] = None
    rotation_degrees: Optional[float] = None

class UpdateImageRequest(BaseModel):
    """Request schema for updating image metadata."""
    subtype: Optional[ImageSubtype] = None
    rating: Optional[int] = None
    rating_manually_edited: Optional[bool] = None
    plate_solve_source: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)
    catalog_matches: List[CatalogMatchSchema] = [] # Optional for list view to save bandwidth?
    
    model_config = ConfigDict(from_attributes=True)
