"""
Request schemas for the Equipment & Sites API (R0, spec §4.6).

Responses are built by serializers in app/api/equipment.py so their shapes
match the R0 API contract exactly (nullable fields are null, never omitted).
"""

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

OpticKind = Literal["TELESCOPE", "LENS"]
Band = Literal["L", "R", "G", "B", "Ha", "OIII", "SII", "Hb", "Duo", "None", "Other"]
HorizonSource = Literal["LEARNED", "IMPORTED", "MANUAL"]


def _clean_name(v):
    if v is None:
        return v
    v = str(v).strip()
    if not v:
        raise ValueError("name must not be empty")
    return v


def _clean_patterns(v):
    if v is None:
        return v
    return [str(p).strip().lower() for p in v if str(p).strip()]


class _Body(BaseModel):
    model_config = ConfigDict(extra="ignore")


class CameraCreate(_Body):
    name: str = Field(..., max_length=100)
    maker: Optional[str] = Field(None, max_length=50)
    sensor_width_px: Optional[int] = Field(None, gt=0)
    sensor_height_px: Optional[int] = Field(None, gt=0)
    pixel_size_um: Optional[float] = Field(None, gt=0, le=50)
    is_color: Optional[bool] = None
    is_cooled: Optional[bool] = None
    match_patterns: Optional[List[str]] = None
    external_ref: Optional[str] = Field(None, max_length=50)
    notes: Optional[str] = None

    _name = field_validator("name")(_clean_name)
    _patterns = field_validator("match_patterns")(_clean_patterns)


class CameraUpdate(CameraCreate):
    name: Optional[str] = Field(None, max_length=100)


class OpticCreate(_Body):
    name: str = Field(..., max_length=100)
    kind: OpticKind = "TELESCOPE"
    aperture_mm: Optional[float] = Field(None, gt=0)
    focal_length_mm: float = Field(..., gt=0)
    external_ref: Optional[str] = Field(None, max_length=50)
    notes: Optional[str] = None

    _name = field_validator("name")(_clean_name)


class OpticUpdate(OpticCreate):
    name: Optional[str] = Field(None, max_length=100)
    kind: Optional[OpticKind] = None
    focal_length_mm: Optional[float] = Field(None, gt=0)


class FilterCreate(_Body):
    name: str = Field(..., max_length=100)
    band: Optional[Band] = None          # derived from the name when omitted
    bandwidth_nm: Optional[float] = Field(None, gt=0)
    match_patterns: Optional[List[str]] = None
    external_ref: Optional[str] = Field(None, max_length=50)

    _name = field_validator("name")(_clean_name)
    _patterns = field_validator("match_patterns")(_clean_patterns)


class FilterUpdate(FilterCreate):
    name: Optional[str] = Field(None, max_length=100)


class RigCreate(_Body):
    name: str = Field(..., max_length=150)
    camera_id: int
    optic_id: int
    modifier_name: Optional[str] = Field(None, max_length=50)
    modifier_factor: float = Field(1.0, gt=0, le=10)
    binning: int = Field(1, ge=1, le=8)
    is_active: bool = True
    mount_name: Optional[str] = Field(None, max_length=100)
    filter_ids: List[int] = []

    _name = field_validator("name")(_clean_name)


class RigUpdate(_Body):
    name: Optional[str] = Field(None, max_length=150)
    camera_id: Optional[int] = None
    optic_id: Optional[int] = None
    modifier_name: Optional[str] = Field(None, max_length=50)
    modifier_factor: Optional[float] = Field(None, gt=0, le=10)
    binning: Optional[int] = Field(None, ge=1, le=8)
    is_active: Optional[bool] = None
    mount_name: Optional[str] = Field(None, max_length=100)
    filter_ids: Optional[List[int]] = None

    _name = field_validator("name")(_clean_name)


class SiteCreate(_Body):
    name: str = Field(..., max_length=100)
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)
    timezone: str = Field(..., max_length=64)
    elevation_m: Optional[float] = None
    bortle: Optional[int] = Field(None, ge=1, le=9)
    sqm: Optional[float] = Field(None, gt=0, le=25)
    typical_seeing_arcsec: float = Field(2.5, gt=0, le=20)
    is_default: bool = False

    _name = field_validator("name")(_clean_name)


class SiteUpdate(_Body):
    name: Optional[str] = Field(None, max_length=100)
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)
    timezone: Optional[str] = Field(None, max_length=64)
    elevation_m: Optional[float] = None
    bortle: Optional[int] = Field(None, ge=1, le=9)
    sqm: Optional[float] = Field(None, gt=0, le=25)
    typical_seeing_arcsec: Optional[float] = Field(None, gt=0, le=20)
    is_default: Optional[bool] = None

    _name = field_validator("name")(_clean_name)


class HorizonUpdate(_Body):
    points: List[List[float]]
    source: HorizonSource = "MANUAL"


class AcceptItem(_Body):
    proposal_id: str
    name: Optional[str] = Field(None, max_length=150)
    optic_id: Optional[int] = None
    modifier_factor: Optional[float] = Field(None, gt=0, le=10)
    modifier_name: Optional[str] = Field(None, max_length=50)
    binning: Optional[int] = Field(None, ge=1, le=8)
    timezone: Optional[str] = Field(None, max_length=64)


class DetectApply(_Body):
    timezone: str = "UTC"
    accept: List[AcceptItem] = []
    include_older: bool = False
