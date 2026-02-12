from typing import Optional
from pydantic import BaseModel, ConfigDict

class MessierSchema(BaseModel):
    id: int
    messier_number: int
    designation: str
    common_name: Optional[str] = None
    ngc_designation: Optional[str] = None
    
    ra_degrees: float
    dec_degrees: float
    
    object_type: str
    constellation: Optional[str] = None
    apparent_magnitude: Optional[float] = None
    angular_size_arcmin: Optional[str] = None
    
    axis_ratio: Optional[float] = None
    position_angle: Optional[float] = None
    pgc_designation: Optional[str] = None
    
    # Computed fields
    cumulative_exposure_seconds: float = 0.0
    image_count: int = 0
    max_separation_degrees: float = 0.0
    
    model_config = ConfigDict(from_attributes=True)

class NGCSchema(BaseModel):
    id: int
    ngc_number: int
    designation: str
    common_name: Optional[str] = None
    
    ra_degrees: float
    dec_degrees: float
    
    object_type: Optional[str] = None
    constellation: Optional[str] = None
    apparent_magnitude: Optional[float] = None
    major_axis_arcmin: Optional[float] = None
    minor_axis_arcmin: Optional[float] = None

    # Computed fields
    cumulative_exposure_seconds: float = 0.0
    image_count: int = 0
    max_separation_degrees: float = 0.0
    
    model_config = ConfigDict(from_attributes=True)


class NamedStarSchema(BaseModel):
    id: int
    designation: str
    common_name: Optional[str] = None
    
    ra_degrees: float
    dec_degrees: float
    magnitude: Optional[float] = None
    spectral_type: Optional[str] = None
    
    # Computed fields
    cumulative_exposure_seconds: float = 0.0
    image_count: int = 0
    max_separation_degrees: float = 0.0
    
    model_config = ConfigDict(from_attributes=True)
