"""
Star quality search filters (Q1d, docs/design/20260927-Q1-star-quality.md §6, §7.1).

QualityFilters is a FastAPI dependency shared by every endpoint built on
_build_image_query (list, CSV export, bulk actions), so a quality selection
such as "suspect subs" can be exported or bulk-edited like any other filter.

Suspect flags use the same rules as the Nights page (services/session_quality):
relative to the median of the sub's night, rig and filter, computed in SQL
over the library. Filters group by the raw filter name (the Nights page folds
spellings like "Ha"/"H-alpha" together), which only matters for mixed names.
"""

from typing import List, Optional

from fastapi import Query
from sqlalchemy import Integer, and_, bindparam, case, or_, select, text

from app.models.equipment import Rig
from app.models.image import Image
from app.services.session_quality import (
    CLOUD_FACTOR, FLAG_MIN_GROUP, SOFT_FACTOR, TRAILED_MARGIN, TRAILED_MIN_ECC,
)
from app.utils.observing_night import NIGHT_JOIN_SQL, NIGHT_SQL
from app.utils.star_quality import SCALE_MISMATCH_FACTOR

FLAGS = ("SOFT", "CLOUD", "TRAILED")
STATUSES = ("OK", "NO_STARS", "SKIPPED", "HINT", "FAILED", "PENDING", "NEVER")


def scale_expr():
    """arcsec/px per image, as in resolve_scale: plate scale unless its rig's measured scale disagrees."""
    rig_scale = select(Rig.measured_scale_arcsec).where(Rig.id == Image.rig_id).scalar_subquery()
    img = Image.pixel_scale_arcsec
    return case(
        (and_(rig_scale > 0, or_(img.is_(None), img < rig_scale / SCALE_MISMATCH_FACTOR,
                                 img > rig_scale * SCALE_MISMATCH_FACTOR)), rig_scale),
        (and_(img > 0, img < 3600), img),
        else_=None,
    )


def fwhm_arcsec_expr():
    return Image.fwhm_px * scale_expr()


SUSPECT_SQL = f"""
    WITH m AS (
        SELECT images.id, {NIGHT_SQL} AS night, images.rig_id, images.filter_name,
               images.fwhm_px, images.star_count, images.eccentricity
        FROM images {NIGHT_JOIN_SQL}
        WHERE images.frame_type = 'LIGHT' AND images.subtype = 'SUB_FRAME'
          AND images.star_metrics_status = 'OK' AND images.fwhm_px IS NOT NULL
          AND images.capture_date IS NOT NULL
    ), g AS (
        SELECT night, rig_id, filter_name, count(*) AS n,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY fwhm_px) AS med_fwhm,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY star_count) AS med_stars,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY eccentricity) AS med_ecc
        FROM m GROUP BY night, rig_id, filter_name
    ), f AS (
        SELECT m.id,
               CASE WHEN g.med_stars > 0 AND m.star_count < {CLOUD_FACTOR} * g.med_stars THEN 'CLOUD'
                    WHEN m.fwhm_px > {SOFT_FACTOR} * g.med_fwhm THEN 'SOFT'
                    WHEN m.eccentricity > greatest({TRAILED_MIN_ECC}, g.med_ecc + {TRAILED_MARGIN}) THEN 'TRAILED'
               END AS flag
        FROM m JOIN g ON g.night = m.night
                     AND g.rig_id IS NOT DISTINCT FROM m.rig_id
                     AND g.filter_name IS NOT DISTINCT FROM m.filter_name
        WHERE g.n >= {FLAG_MIN_GROUP}
    )
    SELECT id FROM f WHERE flag IN :flags
"""


def suspect_ids(flags: List[str]):
    """A textual subquery of image ids carrying any of `flags`."""
    return text(SUSPECT_SQL).bindparams(bindparam("flags", value=list(flags), expanding=True)).columns(id=Integer)


def _csv(value: Optional[str], allowed) -> List[str]:
    if not value:
        return []
    return [v for v in (s.strip().upper() for s in value.split(",")) if v in allowed]


class QualityFilters:
    """Query parameters for star quality (units apply to fwhm_*/hfr_* bounds)."""

    def __init__(
        self,
        fwhm_min: Optional[float] = Query(None, ge=0, description="Minimum FWHM (in quality_units)"),
        fwhm_max: Optional[float] = Query(None, ge=0, description="Maximum FWHM (in quality_units)"),
        hfr_max: Optional[float] = Query(None, ge=0, description="Maximum HFR (in quality_units)"),
        quality_units: str = Query("ARCSEC", description="ARCSEC or PX for fwhm_*/hfr_max"),
        eccentricity_max: Optional[float] = Query(None, ge=0, le=1),
        star_count_min: Optional[int] = Query(None, ge=0),
        star_metrics_status: Optional[str] = Query(None, description="Comma-separated: OK,NO_STARS,SKIPPED,HINT,FAILED,PENDING,NEVER"),
        quality_flag: Optional[str] = Query(None, description="Suspect subs: ANY or comma-separated SOFT,CLOUD,TRAILED"),
    ):
        self.fwhm_min = fwhm_min
        self.fwhm_max = fwhm_max
        self.hfr_max = hfr_max
        self.units = "PX" if (quality_units or "").upper() == "PX" else "ARCSEC"
        self.eccentricity_max = eccentricity_max
        self.star_count_min = star_count_min
        self.statuses = _csv(star_metrics_status, STATUSES)
        flag = (quality_flag or "").strip().upper()
        self.flags = list(FLAGS) if flag == "ANY" else _csv(quality_flag, FLAGS)

    @classmethod
    def none(cls) -> "QualityFilters":
        return cls(None, None, None, "ARCSEC", None, None, None, None)

    def apply(self, stmt):
        size_filters = any(v is not None for v in (self.fwhm_min, self.fwhm_max, self.hfr_max,
                                                   self.eccentricity_max, self.star_count_min))
        if size_filters:
            # Only AstroCat-measured values are comparable.
            stmt = stmt.where(Image.star_metrics_status == "OK")
        scale = scale_expr() if self.units == "ARCSEC" else None
        fwhm = Image.fwhm_px * scale if scale is not None else Image.fwhm_px
        hfr = Image.hfr_px * scale if scale is not None else Image.hfr_px
        if self.fwhm_min is not None:
            stmt = stmt.where(fwhm >= self.fwhm_min)
        if self.fwhm_max is not None:
            stmt = stmt.where(fwhm <= self.fwhm_max)
        if self.hfr_max is not None:
            stmt = stmt.where(hfr <= self.hfr_max)
        if self.eccentricity_max is not None:
            stmt = stmt.where(Image.eccentricity <= self.eccentricity_max)
        if self.star_count_min is not None:
            stmt = stmt.where(Image.star_count >= self.star_count_min)
        if self.statuses:
            named = [s for s in self.statuses if s != "NEVER"]
            conds = [Image.star_metrics_status.in_(named)] if named else []
            if "NEVER" in self.statuses:
                conds.append(Image.star_metrics_status.is_(None))
            stmt = stmt.where(or_(*conds))
        if self.flags:
            stmt = stmt.where(Image.id.in_(suspect_ids(self.flags)))
        return stmt
