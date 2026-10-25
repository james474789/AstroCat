"""
Master -> subs: the light sub-frames an integration master was most likely stacked from.

Masters usually live away from their subs, so the link is geometric. Two paths, either of which matches:

A. Solved: scale within SCALE_TOLERANCE, master area AREA_MIN..AREA_MAX of the sub's, and at least
   MIN_COVERAGE of the master's field inside the sub's footprint. Coverage (rather than separate
   centre/crop/rotation limits) is what lets multi-session masters match: sessions shot with the camera
   turned a few degrees apart are stacked, then cropped to the area they all share. The sub's footprint
   is also tried mirrored (180 - rot), for sidecar solves that report rotation in the opposite sense;
   meridian flips need nothing, as a frame about its centre is the same rectangle at rot and rot + 180.
   A master without a rotation can't be placed, so it falls back to centre + scale + area.
B. Unsolved (sub or master): same target_key, same rig (else same camera), size within the crop window.

Pure helpers plus one DB lookup (find_sub_ids); the API layer turns the ids into a search filter.
"""

from types import SimpleNamespace
from typing import Any, List, Optional

import numpy as np
from fastapi import HTTPException
from geoalchemy2 import Geography
from sqlalchemy import Float, and_, cast, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.image import FrameType, Image, ImageSubtype
from app.services.field_overlaps import (
    GROUP_CENTER_FRACTION, SEEN_IN_GRID, angular_separation, pixel_to_sky, sky_to_pixel, tan_params,
)
from app.utils.field_geometry import effective_field_radius

SCALE_TOLERANCE = 0.03     # sub scale within +/-3% of the master's
MIN_COVERAGE = 0.9         # solved path: share of the master's field that must lie inside the sub
AREA_MIN = 0.5             # solved path: master area may be cropped to half the sub's...
AREA_MAX = 1.04            # ...but not meaningfully larger
CROP_MIN = 0.85            # unsolved path: master side may be cropped to 85% of the sub's...
CROP_MAX = 1.02            # ...but not meaningfully larger
METERS_PER_DEGREE = 111320  # same approximation as the search API's spatial filter

_COLUMNS = (
    Image.id, Image.is_plate_solved, Image.ra_center_degrees, Image.dec_center_degrees,
    Image.field_radius_degrees, Image.pixel_scale_arcsec, Image.rotation_degrees,
    Image.width_pixels, Image.height_pixels, Image.target_key, Image.rig_id, Image.camera_name,
)


def size_matches(master: Any, sub: Any) -> bool:
    """Each master side is CROP_MIN..CROP_MAX of the sub's (the master may be cropped)."""
    mw, mh, sw, sh = master.width_pixels, master.height_pixels, sub.width_pixels, sub.height_pixels
    if not (mw and mh and sw and sh):
        return False
    return CROP_MIN * sw <= mw <= CROP_MAX * sw and CROP_MIN * sh <= mh <= CROP_MAX * sh


def area_matches(master: Any, sub: Any) -> bool:
    """Master area is AREA_MIN..AREA_MAX of the sub's (cropped, possibly at an angle)."""
    mw, mh, sw, sh = master.width_pixels, master.height_pixels, sub.width_pixels, sub.height_pixels
    if not (mw and mh and sw and sh):
        return False
    return AREA_MIN <= (mw * mh) / (sw * sh) <= AREA_MAX


def master_sky(master: Any) -> Optional[np.ndarray]:
    """(N,2) [ra, dec] of a regular grid across the master, or None without a rotation (can't be placed)."""
    params = tan_params(master)
    if params is None:
        return None
    w, h = params["w"], params["h"]
    xs = (np.arange(SEEN_IN_GRID) + 0.5) / SEEN_IN_GRID * w
    ys = (np.arange(SEEN_IN_GRID) + 0.5) / SEEN_IN_GRID * h
    gx, gy = np.meshgrid(xs, ys)
    return pixel_to_sky(params, np.column_stack([gx.ravel(), gy.ravel()]))


def _inside(sub: Any, rot: float, sky: np.ndarray) -> float:
    # Parity doesn't change a centred rectangle's footprint, so it isn't needed here
    params = tan_params(SimpleNamespace(**{**vars(sub), "rotation_degrees": rot, "parity": 1}))
    if params is None:
        return 0.0
    pix = sky_to_pixel(params, sky)
    with np.errstate(invalid="ignore"):
        inside = (pix[:, 0] >= 0) & (pix[:, 0] <= params["w"]) & (pix[:, 1] >= 0) & (pix[:, 1] <= params["h"])
    return float(np.mean(inside))


def coverage(sub: Any, sky: np.ndarray) -> float:
    """Share of the master's sampled field inside the sub's footprint (best of rot and its mirror)."""
    rot = sub.rotation_degrees
    if rot is None:
        # Orientation unknown: the half-diagonal circle covers every possible orientation
        radius = effective_field_radius(sub.field_radius_degrees, sub.width_pixels, sub.height_pixels,
                                        sub.pixel_scale_arcsec)
        if not radius:
            return 0.0
        sep = [angular_separation(sub.ra_center_degrees, sub.dec_center_degrees, ra, dec) for ra, dec in sky]
        return float(np.mean(np.array(sep) < radius))
    return max(_inside(sub, rot, sky), _inside(sub, 180.0 - rot, sky))


def _is_solved(img: Any) -> bool:
    return bool(getattr(img, "is_plate_solved", False)) and None not in (
        img.ra_center_degrees, img.dec_center_degrees, img.pixel_scale_arcsec,
    ) and img.pixel_scale_arcsec > 0


def _master_radius(master: Any) -> Optional[float]:
    return effective_field_radius(master.field_radius_degrees, master.width_pixels,
                                  master.height_pixels, master.pixel_scale_arcsec)


def _solved_match(master: Any, sub: Any, sky: Optional[np.ndarray]) -> bool:
    if not area_matches(master, sub):
        return False
    if abs(sub.pixel_scale_arcsec - master.pixel_scale_arcsec) > SCALE_TOLERANCE * master.pixel_scale_arcsec:
        return False
    if sky is not None:
        return coverage(sub, sky) >= MIN_COVERAGE
    radius = _master_radius(master)
    sep = angular_separation(master.ra_center_degrees, master.dec_center_degrees,
                             sub.ra_center_degrees, sub.dec_center_degrees)
    return bool(radius) and sep < GROUP_CENTER_FRACTION * radius


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


_UNSET = object()


def is_sub_of(master: Any, sub: Any, sky: Any = _UNSET) -> bool:
    """sky: master_sky(master), passed in when checking many subs against one master."""
    if getattr(sub, "id", None) == getattr(master, "id", None):
        return False
    if _is_solved(master) and _is_solved(sub):
        return _solved_match(master, sub, master_sky(master) if sky is _UNSET else sky)
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

    branches = []
    master_solved = _is_solved(master)
    radius = _master_radius(master) if master_solved else None
    if radius:
        scale = master.pixel_scale_arcsec
        area = float(master.width_pixels) * master.height_pixels
        point = func.ST_SetSRID(func.ST_MakePoint(master.ra_center_degrees, master.dec_center_degrees), 4326)
        # MIN_COVERAGE needs the centres well within a field radius of each other
        branches.append(and_(
            Image.is_plate_solved.is_(True),
            func.ST_DWithin(Image.center_location, cast(point, Geography), radius * METERS_PER_DEGREE),
            Image.pixel_scale_arcsec.between(scale * (1 - SCALE_TOLERANCE), scale * (1 + SCALE_TOLERANCE)),
            (cast(Image.width_pixels, Float) * Image.height_pixels).between(area / AREA_MAX, area / AREA_MIN),
        ))
    if master.target_key and (master.rig_id is not None or master.camera_name):
        equipment = (Image.rig_id == master.rig_id if master.rig_id is not None
                     else Image.camera_name == master.camera_name)
        unsolved = Image.is_plate_solved.is_(False) if master_solved else true()
        branches.append(and_(unsolved, Image.target_key == master.target_key, equipment,
                             _size_window(Image.width_pixels, master.width_pixels),
                             _size_window(Image.height_pixels, master.height_pixels)))
    if not branches:
        return []

    stmt = select(*_COLUMNS).where(
        Image.subtype == ImageSubtype.SUB_FRAME,
        Image.frame_type == FrameType.LIGHT,
        Image.id != master_id,
        or_(*branches),
    )
    rows = (await db.execute(stmt)).all()
    sky = master_sky(master) if master_solved else None
    return [r.id for r in rows if is_sub_of(master, SimpleNamespace(**r._mapping), sky)]
