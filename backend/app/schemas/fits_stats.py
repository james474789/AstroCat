from typing import List, Optional
from pydantic import BaseModel
from datetime import date

class FitsStatsOverview(BaseModel):
    total_images: int
    total_exposure_hours: float
    average_exposure_seconds: float
    total_subs: int

class DistributionBin(BaseModel):
    bin_start: float
    bin_end: float
    count: int

class UsageStats(BaseModel):
    name: str
    count: int

class SkyPoint(BaseModel):
    ra: float
    dec: float

class FitsStatsResponse(BaseModel):
    overview: FitsStatsOverview
    exposure_distribution: List[DistributionBin]
    rotation_distribution: List[DistributionBin]
    pixel_scale_distribution: List[DistributionBin]
    cameras: List[UsageStats]
    telescopes: List[UsageStats]
    filters: List[UsageStats]
    sky_coverage: List[SkyPoint]
