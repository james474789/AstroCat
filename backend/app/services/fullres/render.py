"""
Full-resolution rendering for the deep-zoom viewer (V1, docs/design/20261001-V1-full-resolution-viewer.md §4.1).

load (no decimation) -> debayer (OSC FITS/XISF) -> global stretch -> uint8 mono/RGB.

Orientation matches `ThumbnailGenerator.load_source_image`: rows are used exactly as read,
with no flips, so the viewer's pixel -> sky mapping is the same as the thumbnail's.
The stretch curve is fitted once from a whole-frame sample and applied through a lookup table
(integer data) or in row blocks (float data), never per tile.
"""

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable, List, Optional

import numpy as np
from PIL import Image as PILImage

from app.config import settings
from app.services.thumbnails import ThumbnailGenerator

logger = logging.getLogger(__name__)

try:
    import rawpy
except ImportError:
    rawpy = None

try:
    from astropy.io import fits
except ImportError:
    fits = None

try:
    import tifffile
except ImportError:
    tifffile = None

try:
    import xisf
except ImportError:
    xisf = None

try:
    import cv2
except ImportError:
    cv2 = None

PRESETS = ("linear", "auto", "strong", "unlinked")
# STF parameters per preset (`auto` is what the thumbnail task uses for subs)
STF_SETTINGS = {
    "auto": {"target_bg": 0.25, "shadows_clip": -1.25},
    "strong": {"target_bg": 0.40, "shadows_clip": -2.0},
    "unlinked": {"target_bg": 0.25, "shadows_clip": -1.25},
}

RAW_EXTS = {".cr2", ".nef", ".arw", ".dng", ".raf", ".cr3"}
FITS_EXTS = {".fits", ".fit"}
TIFF_EXTS = {".tif", ".tiff"}
NATIVE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
NATIVE_MAX_DIMENSION = 4096

BAYER_PATTERNS = ("RGGB", "BGGR", "GRBG", "GBRG")
SAMPLE_PIXELS = 2_000_000
BLOCK_ELEMENTS = 8_000_000
CHUNK_BYTES = 16 * 1024 * 1024
# An uncompressed XISF up to this size is read into RAM in one pass. The library sits on network
# shares where every extra pass over the file costs as much as the first, so scan + stretch then
# work from memory; larger files fall back to block reads (two passes, bounded memory).
RAM_LOAD_LIMIT_BYTES = 6 * 1024 ** 3

Progress = Optional[Callable[[str, Optional[float]], None]]


@dataclass
class RenderResult:
    array: np.ndarray                  # uint8, HxW or HxWx3
    scale: int                         # integer downsample factor applied by the size guard
    native_width: int
    native_height: int
    notes: List[str] = field(default_factory=list)

    @property
    def width(self):
        return int(self.array.shape[1])

    @property
    def height(self):
        return int(self.array.shape[0])

    @property
    def channels(self):
        return 1 if self.array.ndim == 2 else int(self.array.shape[2])


@dataclass
class Probe:
    """Header-level facts about a source file; no pixel data is read."""
    width: Optional[int]
    height: Optional[int]
    colour: bool
    mode: Optional[str] = None         # PIL mode, for JPEG/PNG/WebP only
    exif_orientation: int = 1


class PlanarFile:
    """Planar (C, H, W) pixel data read from a file with explicit block reads.

    Not an mmap: page-faulting a memory map is pathologically slow on the network shares the
    library lives on (a 390 MB file took 26 s mapped versus well under a second read).
    """

    def __init__(self, path, dtype, shape, offset, channels_used):
        self.path, self.dtype, self.offset = path, np.dtype(dtype), offset
        self.channels_total, self.height, self.width = shape
        self.channels = channels_used

    @property
    def nbytes(self):
        return self.channels * self.height * self.width * self.dtype.itemsize

    def _read_plane_rows(self, f, out, c, y0, progress=None):
        """Fill out[c] (n rows) from plane c starting at row y0, in chunks of a few MB."""
        row_bytes = self.width * self.dtype.itemsize
        f.seek(self.offset + c * self.height * row_bytes + y0 * row_bytes)
        view = memoryview(out[c]).cast("B")
        total, done = len(view), 0
        while len(view):
            got = f.readinto(view[:CHUNK_BYTES])
            if not got:
                raise EOFError(f"{self.path}: unexpected end of file")
            view = view[got:]
            done += got
            if progress:
                progress(done / total)

    def rows(self, y0, y1, step=1):
        out = np.empty((self.channels, y1 - y0, self.width), dtype=self.dtype)
        with open(self.path, "rb", buffering=0) as f:
            for c in range(self.channels):
                self._read_plane_rows(f, out, c, y0)
        out = out[:, ::step, ::step]
        return out[0] if self.channels == 1 else np.moveaxis(out, 0, -1)

    def load(self, progress=None):
        """Read everything into a (C, H, W) array in one sequential pass, reporting 0-100 progress."""
        out = np.empty((self.channels, self.height, self.width), dtype=self.dtype)
        with open(self.path, "rb", buffering=0) as f:
            for c in range(self.channels):
                self._read_plane_rows(f, out, c, 0,
                                      (lambda frac, c=c: progress(100.0 * (c + frac) / self.channels))
                                      if progress else None)
        return out


class Frame:
    """A source image behind a uniform row-block reader.

    Either `data` is an array that is (H, W), (H, W, C), or (C, H, W) when `planar`, or `reader`
    supplies the rows itself (PlanarFile).
    """

    def __init__(self, data=None, planar=False, passthrough=False, header=None, notes=None, reader=None):
        self.data = data
        self.reader = reader
        self.planar = planar
        self.passthrough = passthrough   # 8-bit data that is already display-ready
        self.header = header or {}
        self.notes = notes or []
        if reader is not None:
            self.height, self.width, self.channels = reader.height, reader.width, reader.channels
            self._dtype = reader.dtype
            return
        if planar:
            self.channels, self.height, self.width = data.shape
        else:
            self.height, self.width = data.shape[:2]
            self.channels = 1 if data.ndim == 2 else data.shape[2]
        if self.channels == 1 and planar:
            self.data, self.planar, self.channels = data[0], False, 1
        self._dtype = self.data.dtype

    @property
    def dtype(self):
        return self._dtype

    def rows(self, y0, y1, step=1):
        """Source rows y0:y1:step and every `step`-th column, as H x W [x C]."""
        if self.reader is not None:
            return self.reader.rows(y0, y1, step)
        if self.planar:
            return np.moveaxis(self.data[:, y0:y1:step, ::step], 0, -1)
        return self.data[y0:y1:step, ::step]


# ----------------------------------------------------------------------------
# Bayer
# ----------------------------------------------------------------------------

def parse_bayer_header(header):
    """(pattern, x_offset, y_offset, bottom_up) from FITS-style keywords, or None if not a CFA frame."""
    if not header:
        return None
    pattern = None
    for key in ("BAYERPAT", "COLORTYP"):
        value = str(header.get(key) or "").strip().upper()
        if value in BAYER_PATTERNS:
            pattern = value
            break
    if pattern is None:
        return None

    def _int(key):
        try:
            return int(float(header.get(key) or 0))
        except (TypeError, ValueError):
            return 0

    bottom_up = "BOTTOM" in str(header.get("ROWORDER") or "").upper()
    return pattern, _int("XBAYROFF"), _int("YBAYROFF"), bottom_up


def effective_bayer_pattern(pattern, x_offset=0, y_offset=0, bottom_up=False):
    """The 2x2 pattern (RGGB/BGGR/GRBG/GBRG) at array pixel (0, 0).

    An odd X/Y offset shifts the pattern by one column/row; ROWORDER=BOTTOM-UP means the
    stored rows run bottom-first, which flips the pattern's rows.
    """
    grid = [[pattern[0], pattern[1]], [pattern[2], pattern[3]]]
    y = (y_offset + (1 if bottom_up else 0)) % 2
    x = x_offset % 2
    return "".join((grid[y][x], grid[y][1 - x], grid[1 - y][x], grid[1 - y][1 - x]))


def opencv_bayer_code(pattern):
    """OpenCV's COLOR_Bayer??2RGB code for a FITS-style pattern.

    OpenCV names the code after the second row of the 2x2 cell, reversed, so the names are
    offset from the FITS ones: FITS RGGB -> COLOR_BayerBG2RGB, BGGR -> RG, GRBG -> GB, GBRG -> GR.
    """
    return getattr(cv2, f"COLOR_Bayer{pattern[3]}{pattern[2]}2RGB")


def debayer(cfa, pattern):
    """Bilinear demosaic of a uint8/uint16 CFA frame (already effective-pattern oriented) to HxWx3 RGB."""
    cfa = np.ascontiguousarray(cfa, dtype=cfa.dtype.newbyteorder("="))
    return cv2.cvtColor(cfa, opencv_bayer_code(pattern))


# ----------------------------------------------------------------------------
# Loading
# ----------------------------------------------------------------------------

def _to_hwc(data):
    """Normalise a loaded array to (H, W) or (H, W, 3) with the same heuristics as the thumbnail path."""
    while data.ndim > 3:
        data = data[0]
    if data.ndim == 3:
        if data.shape[0] in (1, 3, 4) and data.shape[1] > 4:   # channel-first
            data = np.moveaxis(data, 0, -1)
        if data.shape[2] == 1:
            data = data[..., 0]
        elif data.shape[2] in (3, 4):
            data = data[..., :3]
        else:
            data = data[..., 0]
    return data


def _fits_header_dict(header):
    out = {}
    for key in ("BAYERPAT", "COLORTYP", "XBAYROFF", "YBAYROFF", "ROWORDER"):
        if key in header:
            out[key] = header[key]
    return out


@contextmanager
def _open_fits(path):
    # A plain read, not memmap=True: mapping the file is pathologically slow on network shares.
    with fits.open(path, memmap=False) as hdul:
        hdu = next((h for h in hdul if h.shape and len(h.shape) >= 2), None)
        if hdu is None:
            raise ValueError("no image HDU")
        data = hdu.data
        header = _fits_header_dict(hdu.header)
        if data.ndim == 3 and data.shape[0] == 3:
            frame = Frame(data, planar=True, header=header)
        else:
            while data.ndim > 2:
                data = data[0]
            frame = Frame(data, header=header)
        yield frame


def _xisf_keywords(metadata):
    keywords = {}
    for name, entries in (metadata.get("FITSKeywords") or {}).items():
        try:
            keywords[name] = entries[0]["value"]
        except (IndexError, KeyError, TypeError):
            continue
    return keywords


def _load_xisf(path, raw_header, progress=None):
    reader = xisf.XISF(path)
    metadata = reader.get_images_metadata()[0]
    header = {**(raw_header or {}), **_xisf_keywords(metadata)}
    width, height, channels = metadata["geometry"]
    location = metadata["location"]
    if location[0] == "attachment" and "compression" not in metadata:
        reader = PlanarFile(path, metadata["dtype"], (channels, height, width), location[1],
                            3 if channels >= 3 else 1)
        if reader.nbytes <= RAM_LOAD_LIMIT_BYTES:
            data = reader.load(lambda pct: progress("loading", pct) if progress else None)
            return Frame(data, planar=True, header=header)
        return Frame(reader=reader, header=header)
    data = xisf.XISF.read(path)
    if data is None:
        raise ValueError("unreadable XISF")
    return Frame(_to_hwc(data), header=header)


def _load_tiff(path):
    with tifffile.TiffFile(path) as tif:
        data = tif.series[0].asarray()
    return Frame(_to_hwc(data))


def _load_pillow(path):
    with PILImage.open(path) as opened:
        mode = opened.mode
        img = opened.copy()
    if mode in {"I", "I;16", "I;16L", "I;16B", "I;16S", "F", "I;32"}:
        return Frame(np.asarray(img))
    if mode in ("L", "RGB"):
        return Frame(np.asarray(img), passthrough=True)
    return Frame(np.asarray(img.convert("RGB")), passthrough=True)


def _load_raw(path, is_subframe):
    with rawpy.imread(path) as raw:
        if is_subframe:
            rgb = raw.postprocess(gamma=(1, 1), no_auto_bright=True, output_bps=16,
                                  use_camera_wb=True, half_size=False)
            return Frame(rgb)
        rgb = raw.postprocess(use_camera_wb=True, bright=1.0, half_size=False)
    return Frame(rgb, passthrough=True)


@contextmanager
def _open_frame(image, progress: Progress = None, debayer_enabled=None):
    """Load the source at full resolution, debayering CFA FITS/XISF. Yields a Frame."""
    path = image.file_path
    ext = Path(path).suffix.lower()
    is_subframe = _is_subframe(image)
    if debayer_enabled is None:
        debayer_enabled = settings.fullres_debayer
    if progress:
        progress("loading", None)

    if ext in RAW_EXTS and rawpy:
        frame = _load_raw(path, is_subframe)
        yield frame
        return
    if ext in FITS_EXTS and fits:
        with _open_fits(path) as frame:
            yield _maybe_debayer(frame, debayer_enabled, progress)
        return
    if ext == ".xisf" and xisf:
        frame = _load_xisf(path, getattr(image, "raw_header", None), progress)
        yield _maybe_debayer(frame, debayer_enabled, progress)
        return
    if ext in TIFF_EXTS and tifffile:
        yield _load_tiff(path)
        return
    yield _load_pillow(path)


def _maybe_debayer(frame, enabled, progress):
    cfa = parse_bayer_header(frame.header) if enabled else None
    if cfa is None or frame.channels != 1 or frame.planar:
        return frame
    data = frame.data if frame.data is not None else frame.rows(0, frame.height)
    if _native(data.dtype) == np.int16:
        data = (data.astype(np.int32) + 32768).astype(np.uint16)
    if data.dtype.kind != "u" or data.dtype.itemsize > 2:
        frame.notes.append(f"debayer skipped: unsupported dtype {data.dtype}")
        return frame
    if cv2 is None:
        frame.notes.append("debayer skipped: OpenCV unavailable")
        return frame
    if progress:
        progress("debayering", None)
    pattern, x_off, y_off, bottom_up = cfa
    effective = effective_bayer_pattern(pattern, x_off, y_off, bottom_up)
    rgb = debayer(data, effective)
    notes = frame.notes + [f"debayered {effective}"]
    return Frame(rgb, header=frame.header, notes=notes)


def _is_subframe(image):
    subtype = getattr(image, "subtype", None)
    return getattr(subtype, "value", subtype) == "SUB_FRAME"


# ----------------------------------------------------------------------------
# Probing (header-level; used by the API before anything is built)
# ----------------------------------------------------------------------------

@lru_cache(maxsize=2048)
def _probe_cached(path, mtime_ns, size, ext, debayer_enabled, raw_header_key):
    raw_header = dict(raw_header_key)
    if ext in RAW_EXTS and rawpy:
        return Probe(None, None, True)
    if ext in FITS_EXTS and fits:
        with fits.open(path, memmap=False) as hdul:
            hdu = next((h for h in hdul if h.shape and len(h.shape) >= 2), None)
            if hdu is None:
                return Probe(None, None, False)
            shape = hdu.shape
            colour = len(shape) == 3 and shape[0] == 3
            if debayer_enabled and len(shape) == 2 and parse_bayer_header(_fits_header_dict(hdu.header)):
                colour = True
            return Probe(shape[-1], shape[-2], colour)
    if ext == ".xisf" and xisf:
        metadata = xisf.XISF(path).get_images_metadata()[0]
        width, height, channels = metadata["geometry"]
        header = {**raw_header, **_xisf_keywords(metadata)}
        colour = channels >= 3 or (channels == 1 and debayer_enabled and parse_bayer_header(header) is not None)
        return Probe(width, height, colour)
    if ext in TIFF_EXTS and tifffile:
        with tifffile.TiffFile(path) as tif:
            series = tif.series[0]
            shape, axes = series.shape, series.axes
            channels = 1
            if "S" in axes:
                channels = shape[axes.index("S")]
            elif len(shape) == 3 and shape[-1] in (3, 4):
                channels = shape[-1]
            elif len(shape) == 3 and shape[0] in (3, 4):
                channels = shape[0]
            return Probe(shape[axes.index("X")] if "X" in axes else shape[-1],
                         shape[axes.index("Y")] if "Y" in axes else shape[-2], channels >= 3)
    with PILImage.open(path) as opened:
        orientation = 1
        if ext in NATIVE_EXTS:
            try:
                orientation = int(opened.getexif().get(274, 1))
            except Exception:
                orientation = 1
        colour = opened.mode not in {"L", "LA", "1", "I", "I;16", "I;16L", "I;16B", "I;16S", "F", "I;32"}
        return Probe(opened.width, opened.height, colour, opened.mode, orientation)


def probe_source(image, st=None):
    """Probe a source file's size/colour from its headers. `st` is an os.stat_result, if the caller has one."""
    import os
    st = st or os.stat(image.file_path)
    raw_header = getattr(image, "raw_header", None) or {}
    header_key = tuple(sorted((k, str(raw_header.get(k))) for k in
                              ("BAYERPAT", "COLORTYP", "XBAYROFF", "YBAYROFF", "ROWORDER") if k in raw_header))
    return _probe_cached(image.file_path, st.st_mtime_ns, st.st_size, Path(image.file_path).suffix.lower(),
                         settings.fullres_debayer, header_key)


def valid_presets(colour):
    return [p for p in PRESETS if colour or p != "unlinked"]


def default_preset(image):
    return "auto" if _is_subframe(image) else "linear"


def is_native_candidate(image, probe, preset):
    """JPEG/PNG/WebP small enough to show straight from the original file (no pyramid)."""
    ext = Path(image.file_path).suffix.lower()
    return (ext in NATIVE_EXTS and preset == "linear" and probe.mode in ("L", "RGB")
            and probe.exif_orientation == 1 and probe.width and probe.height
            and max(probe.width, probe.height) <= NATIVE_MAX_DIMENSION)


# ----------------------------------------------------------------------------
# Stretch
# ----------------------------------------------------------------------------

def _native(dtype):
    """The native-byte-order equivalent of a dtype (FITS data is big-endian)."""
    return np.dtype(f"{dtype.kind}{dtype.itemsize}")


def _is_lut_dtype(dtype):
    return _native(dtype) in (np.dtype(np.uint8), np.dtype(np.uint16), np.dtype(np.int16))


def _as_lut_index(block):
    if _native(block.dtype) == np.int16:
        return block.astype(np.int32) + 32768
    return block


def _lut_domain(dtype):
    dtype = _native(dtype)
    if dtype == np.dtype(np.uint8):
        return np.arange(256, dtype=np.float32)
    if dtype == np.dtype(np.uint16):
        return np.arange(65536, dtype=np.float32)
    return np.arange(65536, dtype=np.float32) - 32768


def _linear_fn(d_min, d_max):
    """Min/max normalisation, as `ThumbnailGenerator._normalize` does without STF."""
    def apply(x):
        x = np.nan_to_num(x.astype(np.float32, copy=False))
        x = (x - d_min) / (d_max - d_min) if d_max > d_min else x - d_min
        return (np.clip(x, 0, 1) * 255).astype(np.uint8)
    return apply


def _stf_fn(params):
    return lambda x: ThumbnailGenerator.stf_curve(x, params)


def _channel_view(arr, channel):
    return arr if channel is None or arr.ndim == 2 else arr[..., channel]


def _block_rows(out_w, channels, multiple=1):
    rows = max(16, min(256, BLOCK_ELEMENTS // max(1, out_w * channels)))
    return max(multiple, rows // multiple * multiple)


def _scan(frame, step):
    """One pass over the rendered rows: per-channel (min, max) and a ~2 MP whole-frame sample.

    Bounds use the same NaN handling as the thumbnail path. The sample is a regular grid over the
    entire frame, so the curve fitted to it is global, never per tile.
    """
    out_h = len(range(0, frame.height, step))
    out_w = len(range(0, frame.width, step))
    k = max(1, int(np.ceil(np.sqrt(out_h * out_w / SAMPLE_PIXELS))))
    rows = _block_rows(out_w, frame.channels, multiple=k)
    lo = np.full(frame.channels, np.inf)
    hi = np.full(frame.channels, -np.inf)
    samples = []
    for a in range(0, out_h, rows):
        b = min(out_h, a + rows)
        block = frame.rows(a * step, (b - 1) * step + 1, step)
        view = block.reshape(block.shape[0], block.shape[1], -1)
        if view.dtype.kind == "f":
            view = np.nan_to_num(view.astype(np.float32, copy=False))
        lo = np.minimum(lo, view.min(axis=(0, 1)))
        hi = np.maximum(hi, view.max(axis=(0, 1)))
        samples.append(block[::k, ::k].copy())   # copy: don't keep the whole block alive
    return lo.astype(np.float32), hi.astype(np.float32), np.concatenate(samples, axis=0)


def _build_mappers(frame, preset, step):
    """One uint8-producing function per channel group: [fn] (linked) or one per channel (unlinked)."""
    lo, hi, sample = _scan(frame, step)
    if preset != "unlinked":
        groups = [(None, (np.float32(lo.min()), np.float32(hi.max())))]
    else:
        groups = [(c, (np.float32(lo[c]), np.float32(hi[c]))) for c in range(frame.channels)]
    mappers = []
    for channel, bounds in groups:
        if preset == "linear":
            mappers.append(_linear_fn(*bounds))
            continue
        params = ThumbnailGenerator.stf_params(_channel_view(sample, channel), bounds=bounds,
                                               **STF_SETTINGS[preset])
        mappers.append(_stf_fn(params))
    return mappers


def _make_apply(frame, preset, step):
    """Return block(uint/float HxW[xC]) -> uint8 for this frame and preset."""
    if frame.passthrough and preset == "linear":
        return lambda block: np.ascontiguousarray(block, dtype=np.uint8)
    mappers = _build_mappers(frame, preset, step)
    if _is_lut_dtype(frame.dtype):
        domain = _lut_domain(frame.dtype)
        luts = [fn(domain) for fn in mappers]
        if len(luts) == 1:
            return lambda block: luts[0][_as_lut_index(block)]
        return lambda block: np.stack([luts[c][_as_lut_index(block[..., c])] for c in range(len(luts))], axis=-1)
    if len(mappers) == 1:
        return mappers[0]
    return lambda block: np.stack([mappers[c](block[..., c]) for c in range(len(mappers))], axis=-1)


# ----------------------------------------------------------------------------
# Public entry point
# ----------------------------------------------------------------------------

def render_full(image, preset, progress: Progress = None) -> RenderResult:
    """Render `image` at full resolution (or the size-guard factor) with the given preset."""
    if preset not in PRESETS:
        raise ValueError(f"unknown preset {preset!r}")
    started = time.monotonic()
    with _open_frame(image, progress) as frame:
        if preset == "unlinked" and frame.channels < 3:
            raise ValueError("preset 'unlinked' needs a colour image")
        cap = settings.fullres_max_megapixels * 1_000_000
        step = max(1, int(np.ceil(np.sqrt(frame.height * frame.width / cap))))
        notes = list(frame.notes)
        if step > 1:
            notes.append(f"rendered at 1/{step} resolution")
        if progress:
            progress("stretching", 0)
        apply = _make_apply(frame, preset, step)

        out_h, out_w = len(range(0, frame.height, step)), len(range(0, frame.width, step))
        shape = (out_h, out_w) if frame.channels == 1 else (out_h, out_w, 3)
        out = np.empty(shape, dtype=np.uint8)
        block_rows = _block_rows(out_w, frame.channels)
        for a in range(0, out_h, block_rows):
            b = min(out_h, a + block_rows)
            out[a:b] = apply(frame.rows(a * step, (b - 1) * step + 1, step))
            if progress:
                progress("stretching", 100.0 * b / out_h)
        native = (frame.width, frame.height)
    notes.append(f"{preset} stretch")
    logger.info("Rendered %s %dx%d preset=%s in %.1fs", image.file_path, out_w, out_h, preset,
                time.monotonic() - started)
    return RenderResult(out, step, native[0], native[1], notes)
