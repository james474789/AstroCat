"""
XISF Extractor
Extracts metadata and WCS coordinates from XISF files using the xisf library.
"""

import logging
import xisf
from typing import Dict, Any, List
from datetime import datetime
from app.extractors.base import BaseExtractor
from app.extractors.fits_extractor import FITSExtractor

logger = logging.getLogger(__name__)

class XISFExtractor(BaseExtractor):
    """Extractor for XISF (Extensible Image Serialization Format) files."""

    def extract(self) -> Dict[str, Any]:
        """Extract metadata from XISF header."""
        metadata = {}
        
        try:
            x = xisf.XISF(self.file_path)
            # We usually care about the first image in the file
            images_md = x.get_images_metadata()
            if not images_md:
                logger.warning(f"No images found in XISF file: {self.file_path}")
                return metadata
            
            im_md = images_md[0]
            
            # Dimensions
            geometry = im_md.get("geometry")
            if isinstance(geometry, (list, tuple)):
                metadata["width_pixels"] = geometry[0] if len(geometry) > 0 else None
                metadata["height_pixels"] = geometry[1] if len(geometry) > 1 else None
            elif isinstance(geometry, dict):
                metadata["width_pixels"] = geometry.get("width")
                metadata["height_pixels"] = geometry.get("height")
            
            # Extract FITS keywords if present
            fits_keywords = im_md.get("FITSKeywords", [])
            header_dict = self._convert_fits_keywords(fits_keywords)
            
            # Use FITSExtractor logic to parse common keywords
            # We instantiate a temporary FITSExtractor to reuse its helper methods
            fits_ext = FITSExtractor(self.file_path)
            
            metadata["exposure_time_seconds"] = fits_ext._get_exposure(header_dict)
            metadata["capture_date"] = fits_ext._get_date(header_dict)
            metadata["gain"] = fits_ext._parse_float(header_dict.get("GAIN"))
            metadata["iso_speed"] = fits_ext._parse_int(header_dict.get("ISOSPEED") or header_dict.get("ISO"))
            metadata["temperature_celsius"] = fits_ext._parse_float(header_dict.get("CCD-TEMP") or header_dict.get("TEMP") or header_dict.get("SET-TEMP"))
            
            # Equipment
            metadata["camera_name"] = header_dict.get("INSTRUME", header_dict.get("CAMERA"))
            metadata["telescope_name"] = header_dict.get("TELESCOP")
            metadata["filter_name"] = header_dict.get("FILTER")
            metadata["observer"] = header_dict.get("OBSERVER")
            metadata["object_name"] = header_dict.get("OBJECT")
            
            # Site
            metadata["site_lat"] = fits_ext._parse_float(header_dict.get("SITELAT"))
            metadata["site_long"] = fits_ext._parse_float(header_dict.get("SITELONG"))
            
            # WCS Extraction
            wcs_info = fits_ext._extract_wcs(header_dict)
            if wcs_info:
                metadata["wcs"] = wcs_info
                metadata["is_plate_solved"] = True
                metadata["plate_solve_source"] = "HEADER"
            else:
                # Fallback to sidecar
                from app.extractors.ini_parser import SidecarParser
                from pathlib import Path
                sidecar_data = SidecarParser.parse(Path(self.file_path))
                if sidecar_data:
                    metadata["wcs"] = sidecar_data
                    metadata["is_plate_solved"] = True
                    metadata["plate_solve_source"] = "SIDECAR"
                else:
                    metadata["is_plate_solved"] = False
            
            metadata["raw_header"] = header_dict
            
        except Exception as e:
            logger.exception(f"Failed to extract metadata from XISF: {self.file_path}")
            
        return metadata

    def _convert_fits_keywords(self, fits_keywords: Any) -> Dict[str, Any]:
        """Convert XISF FITSKeywords to a flat dictionary."""
        header_dict = {}
        
        if isinstance(fits_keywords, dict):
            # If it's a dict, we just need to ensure values are flat if they are objects
            for name, content in fits_keywords.items():
                if isinstance(content, list):
                    # Usually entries are a list of objects like [{'value': ..., 'comment': ...}]
                    if name in ('COMMENT', 'HISTORY'):
                        header_dict[name] = [str(x.get('value', '')) if isinstance(x, dict) else str(x) for x in content]
                    else:
                        if len(content) > 0:
                            first = content[0]
                            header_dict[name] = first.get('value') if isinstance(first, dict) else first
                elif isinstance(content, dict):
                    header_dict[name] = content.get("value", content.get("content"))
                else:
                    header_dict[name] = content
        elif isinstance(fits_keywords, list):
            # Some versions of the library might return a list of dicts with 'name' and 'value'
            for kw in fits_keywords:
                if not isinstance(kw, dict):
                    continue
                name = kw.get("name")
                value = kw.get("value")
                if not name:
                    continue
                
                if name in ('COMMENT', 'HISTORY'):
                    if name not in header_dict:
                        header_dict[name] = []
                    header_dict[name].append(str(value))
                else:
                    header_dict[name] = value
                    
        return header_dict
