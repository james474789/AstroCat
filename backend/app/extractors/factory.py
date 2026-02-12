"""
Extractor Factory
Helper to get the right extractor for a file type.
"""

from pathlib import Path
from app.extractors.base import BaseExtractor
from app.extractors.fits_extractor import FITSExtractor
from app.extractors.exif_extractor import ExifExtractor
from app.extractors.xisf_extractor import XISFExtractor
from app.models.image import ImageFormat


def get_extractor(file_path: str) -> BaseExtractor:
    """Return appropriate extractor instance for the file."""
    path = Path(file_path)
    ext = path.suffix.lower().lstrip('.')
    
    if ext in ['fit', 'fits']:
        return FITSExtractor(file_path)
    elif ext == 'xisf':
        return XISFExtractor(file_path)
    elif ext in ['jpg', 'jpeg', 'png', 'tif', 'tiff', 'cr2', 'cr3', 'arw', 'nef', 'dng']:
        return ExifExtractor(file_path)
    else:
        # Default fallback (might just get file stats)
        # For now, treat unknown as generic Exif/Pillow readable
        return ExifExtractor(file_path)


def determine_format(file_path: str) -> ImageFormat:
    """Map file extension to ImageFormat enum."""
    path = Path(file_path)
    ext = path.suffix.upper().lstrip('.')
    
    try:
        return ImageFormat(ext)
    except ValueError:
        # Fallback mappings
        if ext == 'JPEG': return ImageFormat.JPG
        if ext == 'FITS': return ImageFormat.FIT
        if ext == 'XISF': return ImageFormat.XISF
        return ImageFormat.JPG # fallback
