"""
Sky overlay: catalog objects projected into an image through its own plate solution.

Unlike field_overlaps (a TAN rebuilt from centre/scale/rotation), this uses the
stored WCS as-is, SIP distortion included, so markers land where the solver
says the sky is. Two sources, in order:

- SOLVER: images.wcs_header, the astrometry.net (Nova or local) solution. It
  describes the downsampled JPEG that was uploaded (IMAGEW x IMAGEH), not the
  original frame, so solve-grid pixels are rescaled to native pixels. Never
  merged with raw_header: leftover CDELT/PC/SIP cards would corrupt it.
- HEADER: the WCS embedded in the file itself (capture or processing
  software). Flagged as possibly less accurate.

Coordinate contract (single place: SkyFrame.to_native): astropy 0-based grid
pixel (x0, y0) -> native continuous pixel ((x0 + 0.5) * W / gridW,
(y0 + 0.5) * H / gridH), top-left origin, the same frame as field_overlaps
and the frontend. FITS row 1 is the top display row for every source: the
thumbnail, the full-res tiles and the solver upload all come from loaders
that never flip.

Pure functions only; the API layer does the DB work.
"""

import math
import re
import warnings
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
from astropy.utils.exceptions import AstropyWarning
from astropy.wcs import WCS, FITSFixedWarning

from app.utils.plate_scale import header_num, wcs_frame, wcs_matrix_scale, with_image_size
from app.utils.sky_wcs import has_sip, wcs_cards

SOURCE_SOLVER = "SOLVER"
SOURCE_HEADER = "HEADER"

# Merged markers take colour and label from the highest-priority catalog
CATALOG_PRIORITY = ("MESSIER", "CALDWELL", "NGC", "IC", "SH2", "NAMED_STAR")
# OpenNGC rows that are not objects of their own
EXCLUDED_TYPES = frozenset({"Dup", "NonEx"})
# Catalog query reaches this far past the field so large objects centred just outside still show
QUERY_MARGIN_DEG = 1.6
# Cross-referenced rows further apart than this are not merged (guards against bad cross-refs)
MERGE_MAX_SEP_DEG = 1.0
# Points this far from the tangent point are dropped (TAN blows up towards 90 degrees)
MAX_PROJECT_SEP_DEG = 85.0
MAX_OBJECTS = 1500

WARN_HEADER = "Using the file's embedded WCS — positions may be less accurate"
WARN_NO_SIP = "no distortion model"
WARN_XISF = "XISF row order not yet verified"
WARN_GRID_INFERRED = "solve grid size not recorded, inferred from the plate scale"
WARN_GRID_ASSUMED = "solve grid size unknown, assumed to be the full frame"


@dataclass
class SkyFrame:
    wcs: WCS
    width: int
    height: int
    grid_w: float
    grid_h: float
    source: str
    warnings: List[str] = field(default_factory=list)

    @property
    def accuracy_warning(self) -> Optional[str]:
        if not self.warnings:
            return None
        return "; ".join(self.warnings)

    def to_native(self, x0, y0) -> Tuple[np.ndarray, np.ndarray]:
        """astropy 0-based solve-grid pixels -> native continuous pixels (top-left origin)."""
        x0 = np.asarray(x0, dtype=float)
        y0 = np.asarray(y0, dtype=float)
        return (x0 + 0.5) * self.width / self.grid_w, (y0 + 0.5) * self.height / self.grid_h

    def from_native(self, x, y) -> Tuple[np.ndarray, np.ndarray]:
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        return x * self.grid_w / self.width - 0.5, y * self.grid_h / self.height - 0.5

    def project(self, ra, dec) -> Tuple[np.ndarray, np.ndarray]:
        """(ra, dec) degrees -> native pixels; NaN where the point can't be projected."""
        ra = np.atleast_1d(np.asarray(ra, dtype=float))
        dec = np.atleast_1d(np.asarray(dec, dtype=float))
        out_x = np.full(ra.shape, np.nan)
        out_y = np.full(ra.shape, np.nan)
        if ra.size == 0:
            return out_x, out_y
        ra0, dec0 = (float(v) for v in self.wcs.wcs.crval[:2])
        ok = np.isfinite(ra) & np.isfinite(dec) & (_separation(ra0, dec0, ra, dec) < MAX_PROJECT_SEP_DEG)
        if not ok.any():
            return out_x, out_y
        world = np.column_stack([ra[ok], dec[ok]])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                pix = self.wcs.all_world2pix(world, 0, quiet=True)
            except Exception:
                pix = self.wcs.wcs_world2pix(world, 0)
        x, y = self.to_native(pix[:, 0], pix[:, 1])
        out_x[ok] = x
        out_y[ok] = y
        return out_x, out_y

    def unproject(self, x, y) -> Tuple[np.ndarray, np.ndarray]:
        """Native pixels -> (ra, dec) degrees."""
        gx, gy = self.from_native(np.atleast_1d(x), np.atleast_1d(y))
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            world = self.wcs.all_pix2world(np.column_stack([gx, gy]), 0)
        return world[:, 0] % 360.0, world[:, 1]

    def field_circle(self) -> Tuple[float, float, float]:
        """(ra, dec, radius) in degrees of a circle around the centre that covers the whole frame."""
        w, h = self.width, self.height
        ra, dec = self.unproject([w / 2, 0, w, w, 0], [h / 2, 0, 0, h, h])
        radius = float(np.nanmax(_separation(ra[0], dec[0], ra[1:], dec[1:])))
        return float(ra[0]), float(dec[0]), radius


def _separation(ra1, dec1, ra2, dec2):
    """Great-circle separation in degrees (vectorised over the second point)."""
    ra1, dec1 = np.radians(ra1), np.radians(dec1)
    ra2, dec2 = np.radians(np.asarray(ra2, dtype=float)), np.radians(np.asarray(dec2, dtype=float))
    a = np.sin((dec2 - dec1) / 2) ** 2 + np.cos(dec1) * np.cos(dec2) * np.sin((ra2 - ra1) / 2) ** 2
    return np.degrees(2 * np.arcsin(np.minimum(1.0, np.sqrt(a))))


def _celestial_wcs(header: Any) -> Optional[WCS]:
    """A 2-D celestial WCS built from the WCS cards only, or None if the header has no usable one."""
    if not isinstance(header, dict) or header_num(header, "CRVAL1") is None or header_num(header, "CRVAL2") is None:
        return None
    # Without CD/CDELT astropy assumes 1 deg/pixel
    if not wcs_matrix_scale(header):
        return None
    cards = wcs_cards(header)
    for key in ("NAXIS", "NAXIS1", "NAXIS2"):
        cards.remove(key, ignore_missing=True)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", (AstropyWarning, FITSFixedWarning))
            w = WCS(cards, naxis=2)
    except Exception:
        return None
    if not w.is_celestial or w.wcs.lng != 0 or w.wcs.lat != 1:
        return None
    return w


def resolve_frame(image: Any) -> Optional[SkyFrame]:
    """The image's sky frame from its stored solution (SOLVER) or embedded WCS (HEADER), else None."""
    width = getattr(image, "width_pixels", None)
    height = getattr(image, "height_pixels", None)
    if not width or not height or width <= 0 or height <= 0:
        return None

    solver = getattr(image, "wcs_header", None)
    w = _celestial_wcs(solver)
    if w is not None:
        notes: List[str] = []
        gw, gh = header_num(solver, "IMAGEW"), header_num(solver, "IMAGEH")
        if not gw or not gh or gw <= 0 or gh <= 0:
            grid_scale = wcs_matrix_scale(solver)
            native_scale = getattr(image, "pixel_scale_arcsec", None)
            if grid_scale and native_scale:
                gw = width * native_scale / grid_scale
                gh = gw * height / width
                notes.append(WARN_GRID_INFERRED)
            else:
                gw, gh = float(width), float(height)
                notes.append(WARN_GRID_ASSUMED)
        return SkyFrame(w, width, height, float(gw), float(gh), SOURCE_SOLVER, notes)

    raw = getattr(image, "raw_header", None)
    w = _celestial_wcs(raw)
    if w is not None:
        sized = with_image_size(raw, width, height)
        gw, gh = wcs_frame(sized)
        if not gw or not gh or gw <= 0 or gh <= 0:
            gw, gh = width, height
        notes = [WARN_HEADER]
        if not has_sip(raw):
            notes.append(WARN_NO_SIP)
        if _format_value(image) == "XISF":
            notes.append(WARN_XISF)
        return SkyFrame(w, width, height, float(gw), float(gh), SOURCE_HEADER, notes)
    return None


def _format_value(image: Any) -> Optional[str]:
    fmt = getattr(image, "file_format", None)
    fmt = getattr(fmt, "value", fmt)
    return str(fmt).upper() if fmt is not None else None


def describe(image: Any) -> Tuple[Optional[str], Optional[str]]:
    """(source, accuracy_warning) for the image detail page; (None, None) when unavailable."""
    try:
        frame = resolve_frame(image)
    except Exception:
        return None, None
    if frame is None:
        return None, None
    return frame.source, frame.accuracy_warning


# ---- designations --------------------------------------------------------------------------

def norm_designation(value: Any) -> Optional[str]:
    """Merge key: upper case, no spaces, no leading zeros in numbers ('NGC 0224' -> 'NGC224', 'M031' -> 'M31')."""
    if value is None:
        return None
    s = re.sub(r"\s+", "", str(value)).upper()
    s = re.sub(r"(?<=\D)0+(?=\d)", "", s)
    return s or None


def pretty_designation(catalog: str, designation: str) -> str:
    """Display form: 'NGC0224' -> 'NGC 224', 'IC0434' -> 'IC 434'; other catalogs unchanged apart from leading zeros."""
    if catalog in ("NGC", "IC"):
        m = re.fullmatch(r"(NGC|IC)\s*0*(\d+.*)", designation.strip(), re.IGNORECASE)
        if m:
            return f"{m.group(1).upper()} {m.group(2)}"
    if catalog == "MESSIER":
        return norm_designation(designation) or designation
    return designation


def split_aliases(value: Any) -> List[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v]
    return [p.strip() for p in str(value).split(",") if p.strip()]


def parse_size(value: Any) -> Optional[float]:
    """Arcmin from a float or a Messier string like '70 x 50' (the largest number)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return v if math.isfinite(v) and v > 0 else None
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", str(value))]
    nums = [n for n in nums if n > 0]
    return max(nums) if nums else None


def _first_name(value: Any) -> Optional[str]:
    """OpenNGC common names are comma-separated lists; keep the first."""
    if not value:
        return None
    first = str(value).split(",")[0].strip()
    return first or None


# ---- geometry ------------------------------------------------------------------------------

def _offset(ra: float, dec: float, pa_deg: float, dist_deg: float) -> Tuple[float, float]:
    """Point dist_deg from (ra, dec) along position angle pa_deg (north through east)."""
    d, pa = math.radians(dist_deg), math.radians(pa_deg)
    dec1 = math.radians(dec)
    dec2 = math.asin(math.sin(dec1) * math.cos(d) + math.cos(dec1) * math.sin(d) * math.cos(pa))
    dra = math.atan2(math.sin(pa) * math.sin(d) * math.cos(dec1), math.cos(d) - math.sin(dec1) * math.sin(dec2))
    return (ra + math.degrees(dra)) % 360.0, math.degrees(dec2)


def ellipse_for(frame: SkyFrame, ra: float, dec: float, major: Optional[float], minor: Optional[float],
                pa: Optional[float]) -> Optional[Dict[str, Any]]:
    """
    Native-pixel ellipse for an object of major x minor arcmin at position angle pa: the centre and
    the ends of both semi-axes are projected through the full WCS, so rotation, parity, distortion
    and scale all come out of the solution. No pa -> a circle of the mean size (pa_known False).
    """
    if not major or major <= 0:
        return None
    minor = minor if minor and minor > 0 else major
    pa_known = pa is not None and math.isfinite(pa) and minor < major
    if not pa_known:
        major = minor = (major + minor) / 2.0
        pa = 0.0
    a_end = _offset(ra, dec, pa, major / 120.0)
    b_end = _offset(ra, dec, pa + 90.0, minor / 120.0)
    xs, ys = frame.project([ra, a_end[0], b_end[0]], [dec, a_end[1], b_end[1]])
    if not np.isfinite(xs).all() or not np.isfinite(ys).all():
        return None
    va = (xs[1] - xs[0], ys[1] - ys[0])
    vb = (xs[2] - xs[0], ys[2] - ys[0])
    rx = math.hypot(*va)
    ry = math.hypot(*vb) if pa_known else rx
    return {"rx": rx, "ry": ry, "angle_deg": math.degrees(math.atan2(va[1], va[0])), "pa_known": pa_known}


# ---- objects -------------------------------------------------------------------------------

def _keys(row: Dict[str, Any]) -> List[str]:
    keys = [norm_designation(row["designation"])]
    keys += [norm_designation(a) for a in row.get("aliases") or []]
    return [k for k in keys if k]


def merge_rows(rows: Sequence[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """Group catalog rows that are the same object (shared designation or cross-reference, close on the sky)."""
    parent = list(range(len(rows)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    by_key: Dict[str, int] = {}
    for i, row in enumerate(rows):
        for key in _keys(row):
            j = by_key.get(key)
            if j is None:
                by_key[key] = i
                continue
            ri, rj = find(i), find(j)
            if ri != rj and _separation(row["ra"], row["dec"], rows[j]["ra"], rows[j]["dec"]) < MERGE_MAX_SEP_DEG:
                parent[ri] = rj

    groups: Dict[int, List[Dict[str, Any]]] = {}
    for i, row in enumerate(rows):
        groups.setdefault(find(i), []).append(row)
    return list(groups.values())


def _priority(row: Dict[str, Any]) -> int:
    try:
        return CATALOG_PRIORITY.index(row["catalog"])
    except ValueError:
        return len(CATALOG_PRIORITY)


def _geometry_row(group: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Row with the best size data: major axis and PA, then major axis, then the primary."""
    def score(r):
        has_major = bool(r.get("major"))
        has_pa = has_major and r.get("pa") is not None
        return (0 if has_pa else 1 if has_major else 2, _priority(r))
    return min(group, key=score)


def _in_frame(x: float, y: float, r: float, w: float, h: float) -> bool:
    return x + r >= 0 and x - r <= w and y + r >= 0 and y - r <= h


def build_objects(frame: SkyFrame, rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Overlay objects for the frame from normalised catalog rows
    ({catalog, designation, aliases, common_name, object_type, magnitude, ra, dec, major, minor, pa}).
    Kept when the centre is in frame or the ellipse reaches into it; brightest-first within catalog priority.
    """
    rows = [r for r in rows
            if r.get("ra") is not None and r.get("dec") is not None and r.get("object_type") not in EXCLUDED_TYPES]
    out = []
    for group in merge_rows(rows):
        group.sort(key=lambda r: (_priority(r), norm_designation(r["designation"]) or ""))
        primary = group[0]
        geo = _geometry_row(group)
        x, y = frame.project([geo["ra"]], [geo["dec"]])
        x, y = float(x[0]), float(y[0])
        if not (math.isfinite(x) and math.isfinite(y)):
            continue
        ellipse = None
        if primary["catalog"] != "NAMED_STAR":
            ellipse = ellipse_for(frame, geo["ra"], geo["dec"], geo.get("major"), geo.get("minor"), geo.get("pa"))
        reach = max(ellipse["rx"], ellipse["ry"]) if ellipse else 0.0
        if not _in_frame(x, y, reach, frame.width, frame.height):
            continue

        designations, seen = [], set()
        for r in group:
            label = pretty_designation(r["catalog"], r["designation"])
            if norm_designation(label) not in seen:
                seen.add(norm_designation(label))
                designations.append(label)
        mags = [r["magnitude"] for r in group if r.get("magnitude") is not None]
        common_name = next((_first_name(r.get("common_name")) for r in group if _first_name(r.get("common_name"))), None)
        # Stars read better by name ('Betelgeuse') than by Bayer/Flamsteed designation
        label = common_name if primary["catalog"] == "NAMED_STAR" and common_name else designations[0]
        out.append({
            "key": f"{primary['catalog']}:{norm_designation(primary['designation'])}",
            "catalog": primary["catalog"],
            "catalogs": sorted({r["catalog"] for r in group}, key=CATALOG_PRIORITY.index),
            "designations": designations,
            "label": label,
            # As stored, which is what Search matches catalog designations against
            "search_name": re.sub(r"\s+", "", primary["designation"]),
            "common_name": common_name,
            "object_type": next((r.get("object_type") for r in group if r.get("object_type")), None),
            "magnitude": min(mags) if mags else None,
            "x": x,
            "y": y,
            "ellipse": ellipse,
        })
    out.sort(key=lambda o: (CATALOG_PRIORITY.index(o["catalog"]),
                            o["magnitude"] if o["magnitude"] is not None else 99.0))
    return out[:MAX_OBJECTS]
