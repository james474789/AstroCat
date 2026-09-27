"""
Star quality metrics: HFR, FWHM, eccentricity, star count (Q1,
docs/design/Q1-star-quality.md §4).

Pure functions over a file path (or a numpy array) and its stored header:
no database access, so the maths is testable on synthetic star fields.

    measure(path, file_format, raw_header) -> StarMetrics
    measure_array(data, cfa_factor=1, saturation=None) -> StarMetrics

All sizes are returned in native pixels of the file as captured (binned
pixels for a binned frame), so they combine directly with the plate-solve
pixel scale. Undebayered colour data (FITS/XISF with BAYERPAT, camera RAW)
is measured on 2x2 super-pixels, which needs no Bayer pattern or row order,
and the results are scaled back by cfa_factor = 2.

Bump ALGO_VERSION whenever a change alters the numbers: the sweeper then
re-measures every row produced by an older version.
"""

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

ALGO_VERSION = 2   # 2: fit candidates by flux, sharp-artefact rejection (Q1b)

DETECT_SIGMA = 5.0          # detection threshold, x global background rms
MIN_STARS = 10              # fewer usable stars than this -> NO_STARS
MAX_FIT_STARS = 100         # PSF fits per frame
MIN_FITS = 5                # fewer successful fits -> FWHM = 2 x HFR
FIT_MIN_SNR = 30.0
MOFFAT_BETA = 4.0           # PixInsight SubframeSelector's default family
SATURATION_FRACTION = 0.9   # pixels above this x saturation count as saturated
MAX_MEASURE_PIXELS = 80_000_000  # larger frames are centre-cropped to this area
DETECT_BIN = 2              # detection binning for large frames
DETECT_BIN_ABOVE_PIXELS = 8_000_000
SHARP_OUTLIER_FRACTION = 0.5   # HFR below this x the median -> not a star
FIT_MIN_FWHM_PER_HFR = 0.8     # reject fits narrower than this x the star's own HFR
GRID = 3                    # 3x3 region grid of median HFR (tilt/curvature)
GRID_MIN_STARS = 5

NONLINEAR_FORMATS = {"JPG", "JPEG", "PNG"}
RAW_FORMATS = {"CR2", "CR3", "ARW", "NEF", "DNG"}

# FWHM of a Moffat profile is 2 * alpha * sqrt(2^(1/beta) - 1).
_MOFFAT_FWHM_PER_ALPHA = 2.0 * math.sqrt(2.0 ** (1.0 / MOFFAT_BETA) - 1.0)


class SkipMeasurement(Exception):
    """The file cannot be measured meaningfully (e.g. JPG, 8-bit TIFF)."""


@dataclass
class StarMetrics:
    status: str                              # OK | NO_STARS | SKIPPED | FAILED
    hfr_px: Optional[float] = None
    fwhm_px: Optional[float] = None
    eccentricity: Optional[float] = None
    star_count: Optional[int] = None
    details: Dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def _num(value: Any) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _header_get(header: Optional[dict], *keys: str) -> Any:
    if not header:
        return None
    for key in keys:
        if key in header and header[key] not in (None, ""):
            return header[key]
    return None


def is_cfa_header(header: Optional[dict]) -> bool:
    """Undebayered one-shot-colour data: a Bayer pattern keyword is present."""
    pattern = _header_get(header, "BAYERPAT", "COLORTYP", "CFA-PAT", "CFAPAT")
    if pattern is None:
        return False
    text = str(pattern).strip().upper()
    return len(text) == 4 and set(text) <= set("RGB")


def superpixel(data: np.ndarray) -> np.ndarray:
    """Sum each 2x2 cell (R+G+G+B for Bayer data). Odd trailing row/col dropped."""
    h, w = data.shape
    d = data[: h - h % 2, : w - w % 2].astype(np.float32, copy=False)
    return d[0::2, 0::2] + d[0::2, 1::2] + d[1::2, 0::2] + d[1::2, 1::2]


def _pick_plane(data: np.ndarray, channel_axis: int) -> np.ndarray:
    """Green plane of an RGB cube (or the only plane of a 1-channel cube)."""
    n = data.shape[channel_axis]
    index = 1 if n >= 3 else 0
    return np.take(data, index, axis=channel_axis)


def _nominal_saturation(header: Optional[dict], dtype: np.dtype) -> Optional[float]:
    explicit = _num(_header_get(header, "SATURATE", "DATAMAX"))
    if explicit and explicit > 0:
        return explicit
    bitpix = _num(_header_get(header, "BITPIX"))
    if bitpix is not None and bitpix > 0:
        return float(2 ** int(bitpix) - 1)
    if np.issubdtype(dtype, np.integer):
        return float(np.iinfo(dtype).max)
    return None


def _load_fits(path: str) -> Tuple[np.ndarray, dict]:
    from astropy.io import fits

    def read(memmap: bool):
        with fits.open(path, memmap=memmap) as hdul:
            for hdu in hdul:
                shape = hdu.shape
                if not shape or len(shape) < 2:
                    continue
                header = {k: v for k, v in hdu.header.items() if k}
                return np.array(hdu.data), header
        return None, {}

    try:
        data, header = read(True)
    except ValueError:
        # BZERO/BSCALE/BLANK scaling refuses memory mapping (see thumbnails.py).
        data, header = read(False)
    if data is None:
        raise SkipMeasurement("NO_IMAGE_DATA")
    return data, header


def load_luminance(path: str, file_format: str,
                   raw_header: Optional[dict] = None) -> Tuple[np.ndarray, int, Optional[float]]:
    """
    Linear 2-D float32 luminance for measurement.

    Returns (data, cfa_factor, saturation_adu). saturation_adu is in the
    units of the *returned* array's source pixels (before super-pixel
    summing), or None when unknown.
    """
    fmt = (file_format or "").upper()
    if fmt in NONLINEAR_FORMATS:
        raise SkipMeasurement("NONLINEAR_FORMAT")

    header = dict(raw_header or {})
    cfa = False

    if fmt in RAW_FORMATS:
        import rawpy
        with rawpy.imread(path) as raw:
            data = np.array(raw.raw_image_visible, dtype=np.float32)
            black = float(np.mean(raw.black_level_per_channel)) if raw.black_level_per_channel else 0.0
            white = float(raw.white_level) if raw.white_level else None
        data -= black
        saturation = (white - black) if white else None
        cfa = True
    elif fmt in ("FITS", "FIT"):
        data, file_header = _load_fits(path)
        header = {**header, **file_header}
        saturation = _nominal_saturation(header, data.dtype)
        cfa = is_cfa_header(header)
    elif fmt == "XISF":
        import xisf
        data = xisf.XISF(path).read_image(0)
        saturation = _nominal_saturation(header, data.dtype)
        if np.issubdtype(data.dtype, np.floating):
            saturation = 1.0   # XISF float data is normalised to [0, 1]
        cfa = is_cfa_header(header)
    elif fmt in ("TIFF", "TIF"):
        import tifffile
        data = tifffile.imread(path)
        if data.dtype == np.uint8:
            raise SkipMeasurement("8BIT_TIFF")
        saturation = _nominal_saturation(header, data.dtype)
    else:
        raise SkipMeasurement(f"UNSUPPORTED_FORMAT:{fmt or 'UNKNOWN'}")

    data = np.asarray(data)
    if data.ndim == 3:
        # (C, H, W) from FITS cubes, (H, W, C) from XISF/TIFF readers.
        if data.shape[0] in (1, 3, 4) and data.shape[1] > 4:
            data = _pick_plane(data, 0)
        elif data.shape[-1] in (1, 3, 4):
            data = _pick_plane(data, -1)
        else:
            data = data[0]
        cfa = False   # already debayered / separate planes
    if data.ndim != 2:
        raise SkipMeasurement(f"UNSUPPORTED_SHAPE:{data.shape}")

    data = np.ascontiguousarray(data, dtype=np.float32)
    return data, (2 if cfa else 1), saturation


# --------------------------------------------------------------------------
# Measurement
# --------------------------------------------------------------------------

def _effective_saturation(data: np.ndarray, nominal: Optional[float]) -> float:
    """
    Nominal saturation, lowered to a clipping plateau when the frame shows one
    (e.g. 12/14-bit cameras written unscaled into 16-bit FITS).
    """
    dmax = float(np.max(data))
    plateau = int(np.count_nonzero(data >= dmax)) >= 5
    if nominal is None or nominal <= 0:
        return dmax if plateau else dmax * 1.001 + 1e-9
    if plateau and dmax < nominal:
        return dmax
    return nominal


def _box_any(integral: np.ndarray, x: np.ndarray, y: np.ndarray, r: np.ndarray) -> np.ndarray:
    """For each (x, y, r): does the (2r+1)^2 box hold any True pixel? (summed-area table)."""
    h, w = integral.shape[0] - 1, integral.shape[1] - 1
    xi, yi, ri = np.rint(x).astype(int), np.rint(y).astype(int), np.ceil(r).astype(int)
    x0, x1 = np.clip(xi - ri, 0, w), np.clip(xi + ri + 1, 0, w)
    y0, y1 = np.clip(yi - ri, 0, h), np.clip(yi + ri + 1, 0, h)
    total = integral[y1, x1] - integral[y0, x1] - integral[y1, x0] + integral[y0, x0]
    return total > 0


def _moffat_residuals(p, xx, yy, img):
    amp, x0, y0, ax, ay, theta, c = p
    ct, st = math.cos(theta), math.sin(theta)
    dx, dy = xx - x0, yy - y0
    u = (dx * ct + dy * st) / ax
    v = (-dx * st + dy * ct) / ay
    model = amp * (1.0 + u * u + v * v) ** (-MOFFAT_BETA) + c
    return (model - img).ravel()


def _fit_star(sub: np.ndarray, x: float, y: float, half: int, alpha0: float):
    """Elliptical Moffat (beta fixed) fit. Returns (fwhm_major, fwhm_minor, theta_deg) or None."""
    from scipy.optimize import least_squares

    xi, yi = int(round(x)), int(round(y))
    h, w = sub.shape
    if xi - half < 0 or yi - half < 0 or xi + half + 1 > w or yi + half + 1 > h:
        return None
    img = sub[yi - half: yi + half + 1, xi - half: xi + half + 1].astype(np.float64)
    yy, xx = np.mgrid[yi - half: yi + half + 1, xi - half: xi + half + 1].astype(np.float64)
    peak = float(img.max())
    if peak <= 0:
        return None
    p0 = [peak, x, y, alpha0, alpha0, 0.0, 0.0]
    lo = [0.0, x - 3, y - 3, 0.15, 0.15, -np.inf, -np.inf]
    hi = [np.inf, x + 3, y + 3, 60.0, 60.0, np.inf, np.inf]
    try:
        res = least_squares(_moffat_residuals, p0, bounds=(lo, hi), args=(xx, yy, img),
                            method="trf", x_scale="jac", max_nfev=200)
    except Exception:
        return None
    if not res.success:
        return None
    _, _, _, ax, ay, theta, _ = res.x
    fx, fy = abs(ax) * _MOFFAT_FWHM_PER_ALPHA, abs(ay) * _MOFFAT_FWHM_PER_ALPHA
    major, minor = max(fx, fy), min(fx, fy)
    if not (0.7 <= minor and major <= 40.0):
        return None
    # theta is the angle of the ax axis; the major axis is ax or ay (+90 deg).
    angle = math.degrees(theta) + (0.0 if fx >= fy else 90.0)
    return major, minor, angle % 180.0


def _circular_median_deg(angles_deg: np.ndarray) -> float:
    """Median-ish direction of axial angles (0-180), via the doubled-angle mean."""
    doubled = np.radians(angles_deg) * 2.0
    mean = math.atan2(float(np.mean(np.sin(doubled))), float(np.mean(np.cos(doubled))))
    return (math.degrees(mean) / 2.0) % 180.0


def _r(value: Optional[float], digits: int = 3) -> Optional[float]:
    return None if value is None or not math.isfinite(value) else round(float(value), digits)


def measure_array(data: np.ndarray, cfa_factor: int = 1,
                  saturation: Optional[float] = None) -> StarMetrics:
    """
    Measure a linear 2-D frame. For cfa_factor == 2 the frame is the raw
    Bayer mosaic: it is summed into 2x2 super-pixels here and results are
    scaled back to native pixels.
    """
    import sep

    started = time.monotonic()
    details: Dict[str, Any] = {
        "method": "sep+moffat4", "algo_version": ALGO_VERSION, "cfa_factor": cfa_factor,
    }

    data = np.ascontiguousarray(data, dtype=np.float32)
    data = np.nan_to_num(data, copy=False)
    sat = _effective_saturation(data, saturation)
    satmask = data >= SATURATION_FRACTION * sat
    if cfa_factor == 2:
        data = superpixel(data)
        satmask = superpixel(satmask.astype(np.float32)) > 0

    # Very large frames: centre crop to bound memory and time.
    h, w = data.shape
    per_px = cfa_factor * cfa_factor
    if h * w * per_px > MAX_MEASURE_PIXELS:
        f = math.sqrt(MAX_MEASURE_PIXELS / (h * w * per_px))
        ch, cw = int(h * f), int(w * f)
        y0, x0 = (h - ch) // 2, (w - cw) // 2
        data = np.ascontiguousarray(data[y0:y0 + ch, x0:x0 + cw])
        satmask = satmask[y0:y0 + ch, x0:x0 + cw]
        details["cropped"] = True
        h, w = data.shape

    bkg = sep.Background(data, bw=64, bh=64, fw=3, fh=3)
    details["bkg_adu"] = _r(bkg.globalback / per_px, 2)
    details["noise_adu"] = _r(bkg.globalrms / cfa_factor, 3)
    bkg.subfrom(data)
    rms = float(bkg.globalrms) or 1.0

    # Detection is the slow step (~4 s on 26 MP), so large frames are detected
    # on a 2x2-binned copy (~5x faster) and positions refined at full
    # resolution. Measurement itself always uses the full-resolution frame.
    detect_bin = DETECT_BIN if h * w > DETECT_BIN_ABOVE_PIXELS else 1
    detect_img = superpixel(data) if detect_bin == 2 else data
    detect_rms = rms * detect_bin   # noise of a sum of bin^2 pixels
    sep.set_extract_pixstack(3_000_000)
    sep.set_sub_object_limit(4096)
    objs = None
    for thresh in (DETECT_SIGMA, DETECT_SIGMA * 2):
        try:
            objs = sep.extract(detect_img, thresh, err=detect_rms,
                               minarea=5 if detect_bin == 1 else 3,
                               deblend_cont=0.005, clean=True)
            break
        except Exception as e:  # pixel buffer / deblend overflow on busy frames
            logger.debug("sep.extract failed at %.1f sigma: %s", thresh, e)
    del detect_img
    if objs is None:
        raise RuntimeError("source extraction failed")
    details["n_detected"] = int(len(objs))
    details["detect_bin"] = detect_bin

    def finish(status: str, **values) -> StarMetrics:
        details["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        return StarMetrics(status=status, details=details, **values)

    if len(objs) == 0:
        return finish("NO_STARS", star_count=0)

    x, y, a, b = objs["x"], objs["y"], objs["a"], objs["b"]
    npix, flux = objs["npix"], objs["flux"]
    if detect_bin > 1:
        # Binned pixel (i, j) covers full-res pixels 2i..2i+1, centred at 2i + 0.5.
        x, y = x * detect_bin + 0.5 * (detect_bin - 1), y * detect_bin + 0.5 * (detect_bin - 1)
        a, b = a * detect_bin, b * detect_bin
        npix = npix * detect_bin * detect_bin
        xw, yw, wflag = sep.winpos(data, x, y, np.maximum(a, 0.8))
        ok = (wflag == 0) & (np.abs(xw - x) < 2) & (np.abs(yw - y) < 2)
        x, y = np.where(ok, xw, x), np.where(ok, yw, y)
    margin = np.maximum(10.0, 6.0 * a)
    if satmask.any():
        integral = np.zeros((h + 1, w + 1), dtype=np.int32)
        integral[1:, 1:] = np.cumsum(np.cumsum(satmask, axis=0, dtype=np.int32), axis=1, dtype=np.int32)
        saturated = _box_any(integral, x, y, np.maximum(3.0 * a, 2.0))
        del integral
    else:
        saturated = np.zeros(len(x), dtype=bool)
    del satmask
    keep = (
        (objs["flag"] == 0)
        & (a >= 0.6) & (a <= 25.0)
        & (b > 0) & (a / np.maximum(b, 1e-6) <= 4.0)
        & (npix >= 5)
        & (x > margin) & (x < w - 1 - margin)
        & (y > margin) & (y < h - 1 - margin)
        & ~saturated
    )
    details["saturated_rejected"] = int(np.count_nonzero(saturated))
    idx = np.flatnonzero(keep)
    if len(idx) < MIN_STARS:
        return finish("NO_STARS", star_count=int(len(idx)))

    # Isolation: no other detection within the measuring aperture.
    from scipy.spatial import cKDTree
    tree = cKDTree(np.column_stack([x, y]))
    nn_dist, _ = tree.query(np.column_stack([x[idx], y[idx]]), k=2)
    nn = nn_dist[:, 1] if nn_dist.ndim == 2 else np.full(len(idx), np.inf)

    # HFR: total flux inside a generous aperture. First pass sets a robust
    # aperture from the typical star size (faint stars' isophotal `a` is small).
    first, fflag = sep.flux_radius(data, x[idx], y[idx], np.clip(6.0 * a[idx], 6.0, 60.0), 0.5, subpix=5)
    typical = float(np.median(first[fflag == 0])) if np.any(fflag == 0) else float(np.median(a[idx]))
    rmax = np.clip(np.maximum(6.0 * a[idx], 5.0 * typical), 6.0, 60.0)
    isolated = nn > rmax
    if np.count_nonzero(isolated) >= MIN_STARS:
        idx, rmax = idx[isolated], rmax[isolated]
    inside = (x[idx] > rmax) & (x[idx] < w - 1 - rmax) & (y[idx] > rmax) & (y[idx] < h - 1 - rmax)
    idx, rmax = idx[inside], rmax[inside]
    if len(idx) < MIN_STARS:
        return finish("NO_STARS", star_count=int(len(idx)))
    hfr, hflag = sep.flux_radius(data, x[idx], y[idx], rmax, 0.5, subpix=5)
    good = (hflag == 0) & np.isfinite(hfr) & (hfr > 0)
    idx, hfr = idx[good], hfr[good]
    if len(idx) < MIN_STARS:
        return finish("NO_STARS", star_count=int(len(idx)))

    # Drop objects far sharper than the frame's typical star: warm/hot-pixel
    # clusters and cosmic rays that survived detection (common on uncalibrated,
    # oversampled narrowband subs, where they out-peak the soft real stars).
    # The reference size comes from the highest-flux objects (skipping the top
    # 5%), which are real stars even when warm pixels outnumber them.
    by_flux = np.argsort(flux[idx])[::-1]
    ref = by_flux[int(len(by_flux) * 0.05):][:MAX_FIT_STARS]
    ref_hfr = float(np.median(hfr[ref])) if len(ref) else float(np.median(hfr))
    sharp = hfr < SHARP_OUTLIER_FRACTION * ref_hfr
    details["sharp_rejected"] = int(np.count_nonzero(sharp))
    if np.count_nonzero(~sharp) >= MIN_STARS:
        idx, hfr = idx[~sharp], hfr[~sharp]

    hfr_med = float(np.median(hfr))
    details["n_hfr"] = int(len(idx))
    details["hfr_p10"] = _r(np.percentile(hfr, 10) * cfa_factor)
    details["hfr_p90"] = _r(np.percentile(hfr, 90) * cfa_factor)

    # 3x3 grid of median HFR.
    gx = np.minimum((x[idx] * GRID / w).astype(int), GRID - 1)
    gy = np.minimum((y[idx] * GRID / h).astype(int), GRID - 1)
    grid = []
    for row in range(GRID):
        cells = []
        for col in range(GRID):
            sel = hfr[(gy == row) & (gx == col)]
            cells.append(_r(np.median(sel) * cfa_factor) if len(sel) >= GRID_MIN_STARS else None)
        grid.append(cells)
    details["grid_hfr"] = grid

    # FWHM / eccentricity: Moffat fits on up to MAX_FIT_STARS bright,
    # unsaturated stars, skipping the brightest 5% (closest to saturation).
    # Ranked by total flux, not peak: a warm pixel has a high peak but little
    # flux, and ranking by peak let them crowd out soft, oversampled stars.
    snr = flux[idx] / (np.sqrt(np.maximum(npix[idx], 1)) * rms)
    order = np.argsort(flux[idx])[::-1]
    order = order[int(len(order) * 0.05):]
    order = order[snr[order] > FIT_MIN_SNR][:MAX_FIT_STARS]
    half = int(min(max(math.ceil(3.0 * hfr_med) + 2, 5), 25))
    alpha0 = max(2.0 * hfr_med / _MOFFAT_FWHM_PER_ALPHA, 0.3)
    fits_ = []
    for i in order:
        f = _fit_star(data, float(x[idx[i]]), float(y[idx[i]]), half, alpha0)
        # A real star's FWHM is at least ~1.1x its HFR (1.7x for a Moffat,
        # 2x for a Gaussian); a fit far below that locked onto a sharp artefact.
        if f and math.sqrt(f[0] * f[1]) >= FIT_MIN_FWHM_PER_HFR * hfr[i]:
            fits_.append(f)
    details["n_fwhm"] = len(fits_)

    if len(fits_) >= MIN_FITS:
        arr = np.array(fits_)
        major, minor = arr[:, 0], arr[:, 1]
        fwhm = float(np.median(np.sqrt(major * minor)))
        ecc = float(np.median(np.sqrt(1.0 - (minor / major) ** 2)))
        details["fwhm_method"] = "MOFFAT"
        details["fwhm_major_px"] = _r(np.median(major) * cfa_factor)
        details["fwhm_minor_px"] = _r(np.median(minor) * cfa_factor)
        details["theta_deg"] = _r(_circular_median_deg(arr[:, 2]), 1)
    else:
        # Undersampled or too few bright stars: FWHM = 2 x HFR (exact for a Gaussian).
        fwhm = 2.0 * hfr_med
        ai, bi = a[idx], b[idx]
        ecc = float(np.median(np.sqrt(np.clip(1.0 - (bi / ai) ** 2, 0.0, 1.0))))
        details["fwhm_method"] = "HFR_X2"
    details["undersampled"] = bool(fwhm * cfa_factor < 1.5)

    return finish(
        "OK",
        hfr_px=_r(hfr_med * cfa_factor),
        fwhm_px=_r(fwhm * cfa_factor),
        eccentricity=_r(ecc),
        star_count=int(len(idx)),
    )


def measure(path: str, file_format: str, raw_header: Optional[dict] = None) -> StarMetrics:
    """Load and measure one file. SkipMeasurement becomes a SKIPPED result; other errors propagate."""
    try:
        data, cfa_factor, saturation = load_luminance(path, file_format, raw_header)
    except SkipMeasurement as e:
        return StarMetrics(status="SKIPPED", details={"reason": str(e), "algo_version": ALGO_VERSION})
    return measure_array(data, cfa_factor=cfa_factor, saturation=saturation)
