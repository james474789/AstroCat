"""
FITS Extractor
Extracts metadata and WCS coordinates from FITS headers using Astropy.
"""

import warnings
from typing import Dict, Any
from datetime import datetime

from astropy.io import fits
from astropy.wcs import WCS
from astropy.utils.exceptions import AstropyWarning

from app.extractors.base import BaseExtractor


class FITSExtractor(BaseExtractor):
    """Extractor for FITS (Flexible Image Transport System) files."""

    def extract(self) -> Dict[str, Any]:
        """Extract metadata from FITS header."""
        
        metadata = {}
        
        # Suppress standard Astropy warnings for non-standard headers
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', AstropyWarning)
            
            with fits.open(self.file_path) as hdul:
                # Usually primary header has the info
                header = hdul[0].header
                
                # Check extension 1 if primary is empty (uncommon but possible)
                if len(header) < 10 and len(hdul) > 1:
                    header = hdul[1].header

                # Dimensions
                metadata["width_pixels"] = header.get("NAXIS1")
                metadata["height_pixels"] = header.get("NAXIS2")
                
                # Exposure
                metadata["exposure_time_seconds"] = self._get_exposure(header)
                metadata["capture_date"] = self._get_date(header)
                metadata["gain"] = float(header.get("GAIN", 0))
                
                # Equipment
                metadata["camera_name"] = header.get("INSTRUME", header.get("CAMERA"))
                metadata["telescope_name"] = header.get("TELESCOP")
                metadata["filter_name"] = header.get("FILTER")
                metadata["observer"] = header.get("OBSERVER")
                metadata["object_name"] = header.get("OBJECT")
                
                # Site
                metadata["site_lat"] = self._parse_coord(header.get("SITELAT"))
                metadata["site_long"] = self._parse_coord(header.get("SITELONG"))
                
                # WCS / Plate Solve Info
                wcs_info = self._extract_wcs(header)
                if wcs_info:
                    metadata["wcs"] = wcs_info
                    metadata["is_plate_solved"] = True
                    metadata["plate_solve_source"] = "HEADER"
                else:
                    # Fallback to sidecar files (.ini, .wcs)
                    from app.extractors.ini_parser import SidecarParser
                    from pathlib import Path
                    sidecar_data = SidecarParser.parse(Path(self.file_path))
                    if sidecar_data:
                        metadata["wcs"] = sidecar_data
                        metadata["is_plate_solved"] = True
                        metadata["plate_solve_source"] = "SIDECAR"
                    else:
                        metadata["is_plate_solved"] = False
                
                # Store full header for reference (convert to dict)
                # Astropy headers can contain non-serializable objects for COMMENT/HISTORY
                header_dict = {}
                try:
                    for k, v in header.items():
                        if k in ('COMMENT', 'HISTORY'):
                            # These accumulate; we'll handle them as a list of strings
                            if k not in header_dict:
                                header_dict[k] = []
                            header_dict[k].append(str(v))
                        else:
                            header_dict[k] = v
                except fits.verify.VerifyError:
                    # Header has issues (e.g. non-standard cards), attempt fix
                    try:
                        header.verify('fix')
                        # Retry extraction after fix
                        for k, v in header.items():
                            if k in ('COMMENT', 'HISTORY'):
                                if k not in header_dict:
                                    header_dict[k] = []
                                header_dict[k].append(str(v))
                            else:
                                header_dict[k] = v
                    except Exception:
                        # If still failing, fallback to raw card iteration which is safer
                        pass
                
                # Also assert that we caught all comments/history if items() didn't yield them all
                # (Astropy behavior can vary, explicit check is safer if needed, but items() usually works)
                # Alternative robust approach:
                if not header_dict:
                    clean_header = {}
                    for card in header.cards:
                        key = card.keyword
                        val = card.value
                        if key in ['COMMENT', 'HISTORY']:
                             if key not in clean_header:
                                 clean_header[key] = []
                             clean_header[key].append(str(val))
                        else:
                            clean_header[key] = val
                    metadata["raw_header"] = clean_header
                else:
                    metadata["raw_header"] = header_dict

        return metadata

    def _get_exposure(self, header) -> float:
        """Try multiple keywords for exposure time."""
        for key in ["EXPTIME", "EXPOSURE"]:
            if key in header:
                try:
                    return float(header[key])
                except (ValueError, TypeError):
                    continue
        return 0.0

    def _get_date(self, header) -> datetime:
        """Parse DATE-OBS or DATE."""
        date_str = header.get("DATE-OBS") or header.get("DATE")
        if not date_str:
            return None
            
        try:
            # Try ISO format (2023-01-01T12:00:00)
            return datetime.fromisoformat(date_str)
        except ValueError:
            try:
                # Try simple date (2023-01-01)
                return datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                return None

    def _extract_wcs(self, header) -> Dict[str, Any]:
        """Extract WCS coordinates if present with fallback to non-standard keywords."""
        
        # Initialize defaults
        ra_center, dec_center = None, None
        pixel_scale = None
        rotation = 0.0
        radius_degrees = 1.0
        wcs_type = "NONE"

        # 1. Try Standard WCS logic
        if "CRVAL1" in header and "CRVAL2" in header:
            try:
                w = WCS(header)
                # Check if scale is actually defined or just default 1.0
                # By default, Astropy WCS assumes identity if no CD/CDELT is found
                # Scale matrix will be Identity Matrix [[1, 0], [0, 1]] if missing.
                actual_scale = 0
                if "CDELT1" in header:
                    actual_scale = abs(header["CDELT1"]) * 3600
                elif "CD1_1" in header:
                    import math
                    cd11 = header["CD1_1"]
                    cd12 = header.get("CD1_2", 0)
                    actual_scale = math.sqrt(cd11**2 + cd12**2) * 3600
                
                # Center point calculation
                n1 = header.get("NAXIS1", 0)
                n2 = header.get("NAXIS2", 0)
                
                # IMPORTANT: Only use WCS transformation if we found an explicit scale keyword.
                # If actual_scale is 0, WCS transformation will use default 1.0 deg/pixel 
                # causing massive offsets in RA/Dec (e.g. 1.5 degrees at 45deg Lat for 1px offset).
                if actual_scale > 0 and n1 and n2:
                    # Using 0-based coordinate for pixel_to_world. 
                    # Note: n1/2 is slightly off from (n1-1)/2 center but consistent with existing logic.
                    center = w.pixel_to_world(n1/2, n2/2)
                    ra_center = center.ra.degree
                    dec_center = center.dec.degree
                    wcs_type = "HEADER_WCS"
                    
                    pixel_scale = actual_scale
                    # diagonal radius
                    corner = w.pixel_to_world(0, 0)
                    radius_degrees = center.separation(corner).degree
                    # rotation
                    if "CD1_1" in header:
                        import math
                        cd12 = header.get("CD1_2", 0)
                        cd22 = header.get("CD2_2", 0)
                        rotation = math.degrees(math.atan2(-cd12, cd22)) 
                    elif "CROTA2" in header:
                        rotation = float(header["CROTA2"])
                else:
                    # Fallback to direct CRVALs if WCS object is untrusted
                    ra_center = header.get("CRVAL1")
                    dec_center = header.get("CRVAL2")
                    if ra_center is not None and dec_center is not None:
                        wcs_type = "HEADER_CRVAL"
            except Exception:
                pass

        # 2. Fallback for coordinates if standard WCS failed or was incomplete
        if ra_center is None:
            # Try direct RA/DEC keywords
            ra_val = header.get("RA")
            if ra_val is None:
                ra_val = self._parse_hms_dms(header.get("OBJCTRA"), is_ra=True)
            
            dec_val = header.get("DEC")
            if dec_val is None:
                dec_val = self._parse_hms_dms(header.get("OBJCTDEC"), is_ra=False)
            
            # Final check/parse
            ra_center = self._parse_coord_or_hms(ra_val, is_ra=True)
            dec_center = self._parse_coord_or_hms(dec_val, is_ra=False)
            
            if ra_center is not None and dec_center is not None:
                if wcs_type == "NONE": wcs_type = "HEADER_FALLBACK"

        # 3. Fallback for Pixel Scale / Rotation if missing from standard WCS
        if ra_center is not None and dec_center is not None:
            if pixel_scale is None:
                ps = header.get("PIXSCALE") or header.get("SCALE") or header.get("RESOLUTN")
                if ps:
                    pixel_scale = float(ps)
                else:
                    # Calculate from focal/pixsize
                    focal = header.get("FOCALLEN")
                    pix_size = header.get("XPIXSZ") or header.get("PIXSIZE1")
                    if focal and pix_size:
                        try:
                            pixel_scale = (float(pix_size) / float(focal)) * 206.265
                        except: pass
            
            if rotation == 0:
                rot = (header.get("ROTATION") or header.get("POSANGLE") or 
                       header.get("ANGLE") or header.get("POSANG") or
                       header.get("ROTATANG") or header.get("ROTATOR"))
                if rot:
                    rotation = float(rot)
            
            # Recalculate radius if we have a better pixel scale
            # (Either radius is default 1.0 or suspiciously large due to 1deg/px default)
            if pixel_scale and (radius_degrees == 1.0 or radius_degrees > 20.0):
                n1 = header.get("NAXIS1", 0)
                n2 = header.get("NAXIS2", 0)
                if n1 and n2:
                    import math
                    diagonal = math.sqrt(n1**2 + n2**2)
                    radius_degrees = (diagonal / 2.0) * float(pixel_scale) / 3600.0
                elif radius_degrees > 20.0:
                    radius_degrees = 1.0 # Safe fallback

            return {
                "ra_center": float(ra_center),
                "dec_center": float(dec_center),
                "radius_degrees": float(radius_degrees),
                "pixel_scale": float(pixel_scale or 0),
                "rotation": float(rotation),
                "wcs_type": wcs_type
            }

        return None

    def _parse_coord_or_hms(self, val, is_ra: bool = True) -> float:
        """Parse a coordinate that might be float degrees or HMS/DMS string."""
        if val is None: return None
        if isinstance(val, (int, float)): return float(val)
        if isinstance(val, str):
            try:
                return float(val)
            except ValueError:
                return self._parse_hms_dms(val, is_ra=is_ra)
        return None

    def _parse_hms_dms(self, val: str, is_ra: bool = True) -> float:
        """Parse 'HH MM SS' or 'DD MM SS' strings to degrees."""
        if not val or not isinstance(val, str):
            return None
        try:
            parts = val.replace(':', ' ').split()
            if len(parts) < 3:
                try:
                    return float(val)
                except:
                    return None
            
            h_d = float(parts[0])
            m = float(parts[1])
            s = float(parts[2])
            
            sign = 1.0
            if parts[0].startswith('-'):
                sign = -1.0
                h_d = abs(h_d)
            
            deg = h_d + m/60.0 + s/3600.0
            if is_ra:
                # Hours to degrees
                return (deg * 15.0) % 360.0
            return sign * deg
        except Exception:
            return None

    def _parse_coord(self, val):
        """Parse string coordinates to float if needed."""
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            try:
                return float(val)
            except ValueError:
                return None
        return None
