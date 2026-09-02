import hashlib
import logging
import os
from pathlib import Path

import numpy as np
from PIL import Image

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


class ThumbnailGenerator:
    @staticmethod
    def _source_stride(width, height, target_size):
        if not target_size or not width or not height:
            return 1
        target_width, target_height = target_size
        return max(1, int(np.ceil(max(width / target_width, height / target_height))))

    @staticmethod
    def _prepare_array(data, max_dimension=2048):
        if data.ndim == 2:
            height, width = data.shape
            if max(height, width) > max_dimension:
                stride = max(1, int(np.ceil(max(height, width) / max_dimension)))
                data = data[::stride, ::stride]
        elif data.ndim == 3:
            channel_first = data.shape[0] in (1, 3, 4) and data.shape[1] > 4
            height, width = data.shape[1:3] if channel_first else data.shape[:2]
            if max(height, width) > max_dimension:
                stride = max(1, int(np.ceil(max(height, width) / max_dimension)))
                data = data[:, ::stride, ::stride] if channel_first else data[::stride, ::stride, ...]
        return data

    @staticmethod
    def apply_stf_stretch(data, target_bg=0.25, shadows_clip=-1.25):
        data = np.nan_to_num(data.astype(np.float32, copy=False))
        d_min = np.min(data)
        d_max = np.max(data)
        if d_max <= d_min:
            return np.zeros_like(data, dtype=np.uint8)
        data = (data - d_min) / (d_max - d_min)
        median = np.median(data)
        mad = np.median(np.abs(data - median))
        c0 = max(0.0, median + shadows_clip * mad)
        data = (np.clip(data, c0, 1.0) - c0) / (1.0 - c0)
        median_new = np.median(data)
        denominator = median_new + target_bg - 2 * median_new * target_bg
        midpoint = (median_new * (1 - target_bg) / denominator
                    if denominator and 0 < median_new < 1 and median_new != target_bg else 0.5)
        if midpoint != 0.5:
            with np.errstate(divide="ignore", invalid="ignore"):
                data = ((midpoint - 1) * data) / ((2 * midpoint - 1) * data - midpoint)
        return (np.clip(data, 0, 1) * 255).astype(np.uint8)

    @staticmethod
    def _normalize(data, apply_stf):
        if apply_stf:
            return ThumbnailGenerator.apply_stf_stretch(data)
        data = np.nan_to_num(data.astype(np.float32, copy=False))
        d_min = np.min(data)
        d_max = np.max(data)
        if d_max > d_min:
            data = (data - d_min) / (d_max - d_min)
        else:
            data = data - d_min
        return (np.clip(data, 0, 1) * 255).astype(np.uint8)

    @staticmethod
    def _read_xisf_preview(source_path, target_size):
        reader = xisf.XISF(source_path)
        metadata = reader.get_images_metadata()[0]
        width, height, channels = metadata["geometry"]
        location = metadata["location"]
        if location[0] != "attachment" or "compression" in metadata:
            return None
        _, offset, _ = location
        stride = ThumbnailGenerator._source_stride(width, height, target_size)
        data = np.memmap(
            source_path,
            dtype=metadata["dtype"],
            mode="r",
            offset=offset,
            shape=(channels, height, width),
        )
        data = data[:, ::stride, ::stride]
        return np.transpose(data, (1, 2, 0)) if channels > 1 else data[0]

    @staticmethod
    def load_source_image(source_path: str, is_subframe: bool = True,
                          apply_stf: bool = False, target_size=(1024, 1024)) -> Image.Image:
        source = Path(source_path)
        if not source.exists():
            return None
        ext = source.suffix.lower()
        try:
            if ext in {".cr2", ".nef", ".arw", ".dng", ".raf", ".cr3"} and rawpy:
                with rawpy.imread(source_path) as raw:
                    half_size = bool(target_size and max(raw.sizes.height, raw.sizes.width) > max(target_size))
                    if is_subframe:
                        rgb = raw.postprocess(gamma=(1, 1), no_auto_bright=True, output_bps=16,
                                              use_camera_wb=True, half_size=half_size)
                        rgb = ThumbnailGenerator._normalize(rgb, apply_stf)
                    else:
                        rgb = raw.postprocess(use_camera_wb=True, bright=1.0, half_size=half_size)
                    return Image.fromarray(rgb).convert("RGB")

            if ext in {".fits", ".fit"} and fits:
                with fits.open(source_path, memmap=True) as hdul:
                    for hdu in hdul:
                        shape = hdu.shape
                        if not shape or len(shape) < 2:
                            continue
                        stride = ThumbnailGenerator._source_stride(shape[-1], shape[-2], target_size)
                        data = hdu.section[0, ::stride, ::stride] if len(shape) == 3 else hdu.section[::stride, ::stride]
                        return Image.fromarray(ThumbnailGenerator._normalize(data, apply_stf)).convert("RGB")
                return None

            if ext == ".xisf" and xisf:
                data = ThumbnailGenerator._read_xisf_preview(source_path, target_size)
                if data is None:
                    data = xisf.XISF.read(source_path)
                if data is None:
                    return None
                if data.ndim == 3:
                    if data.shape[0] in (3, 4) and data.shape[1] > 4:
                        data = np.transpose(data, (1, 2, 0))
                    elif data.shape[2] not in (3, 4):
                        data = data[0]
                data = ThumbnailGenerator._prepare_array(data)
                return Image.fromarray(ThumbnailGenerator._normalize(data, apply_stf)).convert("RGB")

            if ext in {".tif", ".tiff"} and tifffile:
                with tifffile.TiffFile(source_path) as tif:
                    series = tif.series[0]
                    level = series
                    for candidate in getattr(series, "levels", ()):
                        if max(candidate.shape[-2:]) <= max(target_size):
                            level = candidate
                            break
                    try:
                        data = level.asarray(out="memmap")
                    except (ValueError, OSError):
                        data = level.asarray()
                data = ThumbnailGenerator._prepare_array(data)
                return Image.fromarray(ThumbnailGenerator._normalize(data, apply_stf)).convert("RGB")

            with Image.open(source_path) as opened:
                if target_size and max(opened.size) > max(target_size):
                    opened.draft(opened.mode, target_size)
                img = opened.copy()
            if apply_stf or img.mode in {"I", "I;16", "I;16L", "I;16B", "I;16S", "F", "I;32"}:
                img = Image.fromarray(ThumbnailGenerator._normalize(np.asarray(img), apply_stf))
            return img.convert("RGB")
        except Exception:
            logger.exception("Failed to load source image %s", source_path)
            return None

    @staticmethod
    def generate(source_path: str, output_dir: str, max_size=(400, 400),
                 is_subframe: bool = True, apply_stf: bool = False,
                 overwrite: bool = False) -> str:
        source = Path(source_path)
        os.makedirs(output_dir, exist_ok=True)
        path_hash = hashlib.md5(str(source).encode("utf-8")).hexdigest()[:8]
        thumb_path = os.path.join(output_dir, f"{source.stem}_{path_hash}_thumb.jpg")
        if os.path.exists(thumb_path) and not overwrite:
            return thumb_path
        img = ThumbnailGenerator.load_source_image(
            source_path, is_subframe=is_subframe, apply_stf=apply_stf, target_size=max_size)
        if img is None:
            return None
        img.thumbnail(max_size, Image.Resampling.LANCZOS, reducing_gap=2.0)
        img.info = {}
        img.save(thumb_path, "JPEG", quality=85)
        return thumb_path
