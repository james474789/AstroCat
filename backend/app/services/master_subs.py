"""
Master -> subs: the light sub-frames an integration master was most likely stacked from.

Masters usually live away from their subs, so the link is geometric: the same framing, pixel
scale and (crop-tolerant) dimensions. Two paths, either of which matches:

A. Solved: centre within GROUP_CENTER_FRACTION of the master's field radius, scale within
   SCALE_TOLERANCE, size within the crop window, and rotation equal mod 180 (meridian flips) or
   mirrored (180 - rot, mod 180: sidecar solves with unknown parity). Missing rotation skips that check.
B. Unsolved (sub or master): same target_key, same rig (else same camera), size within the crop window.

Pure helpers plus one DB lookup (find_sub_ids); the API layer turns the ids into a search filter.
"""

from types import SimpleNamespace
from typing import Any, List, Optional

from fastapi import HTTPException
from geoalchemy2 import Geography
from sqlalchemy import and_, cast, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.image import FrameType, Image, ImageSubtype
from app.services.field_overlaps import GROUP_CENTER_FRACTION, GROUP_ROTATION_DEG, angular_separation
from app.utils.field_geometry import effective_field_radius

SCALE_TOLERANCE = 0.03     # sub scale within +/-3% of the master's
CROP_MIN = 0.85            # master side may be cropped to 85% of the sub's...
CROP_MAX = 1.02            # ...but not meaningfully larger
METERS_PER_DEGREE = 111320  # same approximation as the search API's spatial filter

_COLUMNS = (
    Image.id, Image.is_plate_solved, Image.ra_center_degrees, Image.dec_center_degrees,
    Image.field_radius_degrees, Image.pixel_scale_arcsec, Image.rotation_degrees,
    Image.width_pixels, Image.height_pixels, Image.target_key, Image.rig_id, Image.camera_name,
)


def _angle_close(a: float, b: float) -> bool:
    d = abs(a - b) % 180.0
    return min(d, 180.0 - d) < GROUP_ROTATION_DEG


def rotation_matches(master_rot: Optional[float], sub_rot: Optional[float]) -> bool:
    """Equal mod 180, or mirrored (sub = 180 - master, mod 180). True when either is unknown."""
    if master_rot is None or sub_rot is None:
        return True
    return _angle_close(master_rot, sub_rot) or _angle_close(180.0 - master_rot, sub_rot)


def size_matches(master: Any, sub: Any) -> bool:
    """Each master side is CROP_MIN..CROP_MAX of the sub's (the master may be cropped)."""
    mw, mh, sw, sh = master.width_pixels, master.height_pixels, sub.width_pixels, sub.height_pixels
    if not (mw and mh and sw and sh):
        return False
    return CROP_MIN * sw <= mw <= CROP_MAX * sw and CROP_MIN * sh <= mh <= CROP_MAX * sh


def _is_solved(img: Any) -> bool:
    return bool(getattr(img, "is_plate_solved", False)) and None not in (
        img.ra_center_degrees, img.dec_center_degrees, img.pixel_scale_arcsec,
    ) and img.pixel_scale_arcsec > 0


def _master_radius(master: Any) -> Optional[float]:
    return effective_field_radius(master.field_radius_degrees, master.width_pixels,
                                  master.height_pixels, master.pixel_scale_arcsec)


def _solved_match(master: Any, sub: Any) -> bool:
    radius = _master_radius(master)
    if not radius or not size_matches(master, sub):
        return False
    if abs(sub.pixel_scale_arcsec - master.pixel_scale_arcsec) > SCALE_TOLERANCE * master.pixel_scale_arcsec:
        return False
    sep = angular_separation(master.ra_center_degrees, master.dec_center_degrees,
                             sub.ra_center_degrees, sub.dec_center_degrees)
    if sep >= GROUP_CENTER_FRACTION * radius:
        return False
    return rotation_matches(master.rotation_degrees, sub.rotation_degrees)


def _same_equipment(master: Any, sub: Any) -> bool:
    if master.rig_id is not None:
        return sub.rig_id == master.rig_id
    if master.camera_name:
        return sub.camera_name == master.camera_name
    return False


def _unsolved_match(master: Any, sub: Any) -> bool:
    if not master.target_key or sub.target_key != master.target_key:
        return False
    return _same_equipment(master, sub) and size_matches(master, sub)


def is_sub_of(master: Any, sub: Any) -> bool:
    if getattr(sub, "id", None) == getattr(master, "id", None):
        return False
    if _is_solved(master) and _is_solved(sub):
        return _solved_match(master, sub)
    return _unsolved_match(master, sub)


def _size_window(col, master_side: int):
    """SQL: sub side such that master_side is within CROP_MIN..CROP_MAX of it."""
    return col.between(master_side / CROP_MAX, master_side / CROP_MIN)


async def find_sub_ids(db: AsyncSession, master_id: int) -> List[int]:
    """Ids of the light subs matching master_id (404 if it doesn't exist)."""
    row = (await db.execute(select(*_COLUMNS).where(Image.id == master_id))).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Image not found")
    master = SimpleNamespace(**row._mapping)
    if not master.width_pixels or not master.height_pixels:
        return []

    size = and_(_size_window(Image.width_pixels, master.width_pixels),
                _size_window(Image.height_pixels, master.height_pixels))
    branches = []
    master_solved = _is_solved(master)
    radius = _master_radius(master) if master_solved else None
    if radius:
        scale = master.pixel_scale_arcsec
        point = func.ST_SetSRID(func.ST_MakePoint(master.ra_center_degrees, master.dec_center_degrees), 4326)
        branches.append(and_(
            Image.is_plate_solved.is_(True),
            func.ST_DWithin(Image.center_location, cast(point, Geography),
                            GROUP_CENTER_FRACTION * radius * METERS_PER_DEGREE),
            Image.pixel_scale_arcsec.between(scale * (1 - SCALE_TOLERANCE), scale * (1 + SCALE_TOLERANCE)),
        ))
    if master.target_key and (master.rig_id is not None or master.camera_name):
        equipment = (Image.rig_id == master.rig_id if master.rig_id is not None
                     else Image.camera_name == master.camera_name)
        unsolved = Image.is_plate_solved.is_(False) if master_solved else true()
        branches.append(and_(unsolved, Image.target_key == master.target_key, equipment))
    if not branches:
        return []

    stmt = select(*_COLUMNS).where(
        Image.subtype == ImageSubtype.SUB_FRAME,
        Image.frame_type == FrameType.LIGHT,
        Image.id != master_id,
        size,
        or_(*branches),
    )
    rows = (await db.execute(stmt)).all()
    return [r.id for r in rows if is_sub_of(master, SimpleNamespace(**r._mapping))]
