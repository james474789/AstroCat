"""
Base Extractor
Abstract base class for all image metadata extractors.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from pathlib import Path


class BaseExtractor(ABC):
    """
    Abstract base class for metadata extractors.
    Subclasses must implement extract() method.
    """
    
    def __init__(self, file_path: str):
        self.file_path = Path(file_path)
        if not self.file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

    @abstractmethod
    def extract(self) -> Dict[str, Any]:
        """
        Extract metadata from the file.
        
        Returns:
            Dictionary containing standard metadata fields:
            - width_pixels (int)
            - height_pixels (int)
            - exposure_time_seconds (float)
            - capture_date (datetime)
            - camera_name (str)
            - telescope_name (str)
            - filter_name (str)
            - gain (float)
            - wcs_data (dict, optional) - if plate solved
            - raw_metadata (dict) - format-specific raw data
        """
        pass
    
    def get_file_stats(self) -> Dict[str, Any]:
        """Get basic file system stats."""
        stats = self.file_path.stat()
        return {
            "file_size_bytes": stats.st_size,
            "created_at": stats.st_ctime,
            "modified_at": stats.st_mtime,
        }
    @staticmethod
    def _parse_float(val: Any) -> Optional[float]:
        """Safely parse value to float, returns None if not a number."""
        if val is None:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_int(val: Any) -> Optional[int]:
        """Safely parse value to int, returns None if not a number."""
        if val is None:
            return None
        try:
            # Handle float strings being converted to int (e.g. "1.0" -> 1)
            return int(float(val))
        except (ValueError, TypeError):
            return None
