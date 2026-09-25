"""
Target Schemas (F2)
Pydantic models for the /api/targets endpoints.
"""

from datetime import datetime, date
from typing import Optional, List, Dict
from pydantic import BaseModel, ConfigDict


class FilterIntegration(BaseModel):
    filter: str                    # normalized bucket, e.g. "Ha", "Other:Foo"
    raw_names: List[str] = []
    subs: int
    seconds: float
    goal_seconds: Optional[float] = None

    model_config = ConfigDict(from_attributes=True)


class TargetSummary(BaseModel):
    target_key: str
    display_name: str
    catalog_type: Optional[str] = None       # MESSIER/NGC/IC/CALDWELL/NAMED_STAR/None
    object_type: Optional[str] = None
    constellation: Optional[str] = None
    total_seconds: float = 0
    total_subs: int = 0
    planetary_count: int = 0
    nights: int = 0
    first_capture: Optional[datetime] = None
    last_capture: Optional[datetime] = None
    filters: List[FilterIntegration] = []
    cameras: List[str] = []
    telescopes: List[str] = []
    master_count: int = 0
    cover_image_id: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


class TargetListResponse(BaseModel):
    items: List[TargetSummary]
    total: int
    page: int
    page_size: int
    total_pages: int


class UnassignedSummary(BaseModel):
    count: int
    total_seconds: float


class ByFilterRig(BaseModel):
    filter: str
    camera: Optional[str] = None
    telescope: Optional[str] = None
    subs: int
    seconds: float


class NightBreakdown(BaseModel):
    night: date
    filters: Dict[str, float]  # normalized filter -> seconds


class GoalProgress(BaseModel):
    filter_group: str
    goal_seconds: float
    have_seconds: float = 0


class TargetCatalogInfo(BaseModel):
    catalog_type: str
    designation: str
    common_name: Optional[str] = None
    object_type: Optional[str] = None
    constellation: Optional[str] = None
    apparent_magnitude: Optional[float] = None
    ra_degrees: Optional[float] = None
    dec_degrees: Optional[float] = None


class TargetDetail(TargetSummary):
    by_filter_rig: List[ByFilterRig] = []
    nights_detail: List[NightBreakdown] = []
    masters: List[dict] = []
    goals: List[GoalProgress] = []
    catalog: Optional[TargetCatalogInfo] = None


class TargetGoalInput(BaseModel):
    filter_group: str
    goal_seconds: float
