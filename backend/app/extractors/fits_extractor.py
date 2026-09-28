"""
FITS Extractor
Extracts metadata and WCS coordinates from FITS headers using Astropy.

Hardened against malformed/non-standard header cards: astropy parses card
values lazily, so a single unparsable card (e.g. 'CCD-TEMP = -19.80C' written
by some all-sky camera software) raises VerifyError only when its value is
accessed. All header reads in this module go through _safe_get() so that one
bad card can never abort the extraction of the remaining metadata.
"""

import logging
import math
import re
import warnings
from typing import Dict, Any
from datetime import datetime

from astropy.io import fits
from astropy.wcs import WCS
from astropy.utils.exceptions import AstropyWarning

from app.extractors.base import BaseExtractor
from app.utils.header_values import parse_sexagesimal
from app.utils.plate_scale import header_pixel_scale, wcs_frame, wcs_matrix_scale

logger = logging.getLogger(__name__)


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

                # Attempt silent repair of fixable non-standard cards.
                # Unfixable cards raise VerifyError here; those are tolerated
                # per-key by _safe_get() below.
                try:
                    header.verify('silentfix')
                except Exception:
                    pass

                # Dimensions
                metadata["width_pixels"] = self._parse_int(self._safe_get(header, "NAXIS1"))
                metadata["height_pixels"] = self._parse_int(self._safe_get(header, "NAXIS2"))
                
                # Exposure
                metadata["exposure_time_seconds"] = self._get_exposure(header)
                metadata["capture_date"] = self._get_date(header)
                metadata["gain"] = self._parse_float(self._safe_get(header, "GAIN"))
                metadata["binning"] = self._get_binning(header)
                metadata["iso_speed"] = self._parse_int(self._safe_get(header, "ISOSPEED", "ISO"))
                metadata["temperature_celsius"] = self._parse_float(self._safe_get(header, "CCD-TEMP", "TEMP", "SET-TEMP"))
                
                # Equipment
                metadata["camera_name"] = self._safe_get(header, "INSTRUME", "CAMERA")
                metadata["telescope_name"] = self._safe_get(header, "TELESCOP")
                metadata["filter_name"] = self._safe_get(header, "FILTER")
                metadata["observer"] = self._safe_get(header, "OBSERVER")
                metadata["object_name"] = self._safe_get(header, "OBJECT")
                
                # Site (decimal or sexagesimal, e.g. "56d0m0.000s N")
                metadata["site_lat"] = parse_sexagesimal(self._safe_get(header, "SITELAT"))
                metadata["site_long"] = parse_sexagesimal(self._safe_get(header, "SITELONG"))
                metadata["site_name"] = self._safe_get(header, "SITENAME", "OBSERVAT")
                
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
                
                # Store full header for reference (convert to dict).
                # Iterate card-by-card because:
                #   - value access can raise VerifyError for unparsable cards
                #   - values can be non-JSON-serializable (Undefined sentinel,
                #     datetime objects) which would break the JSONB raw_header
                #     column and poison the whole record
                header_dict = {}
                for card in header.cards:
                    try:
                        key = card.keyword
                        if key in ('COMMENT', 'HISTORY'):
                            # These accumulate; store them as a list of strings
                            try:
                                header_dict.setdefault(key, []).append(str(card.value))
                            except Exception:
                                header_dict.setdefault(key, []).append(self._raw_card_value(card) or "")
                        else:
                            try:
                                val = card.value
                            except Exception:
                                # Unparsable card - salvage the raw text payload
                                val = self._raw_card_value(card)
                            # Normalize values that would break JSONB serialization
                            if isinstance(val, fits.card.Undefined):
                                val = None
                            elif isinstance(val, datetime):
                                val = val.isoformat()
                            header_dict[key] = val
                    except Exception as e:
                        logger.debug(f"Skipping unreadable card '{getattr(card, 'keyword', '?')}' in {self.file_path}: {e}")
                        continue
                metadata["raw_header"] = header_dict

        return metadata

    def _safe_get(self, header, *keys, default=None):
        """
        Read the first available, parsable value from a FITS header.

        Astropy parses card values lazily, so a single malformed card (e.g.
        'CCD-TEMP = -19.80C' written by some all-sky cameras) raises
        VerifyError only when its value is accessed. This helper catches those
        errors per-key and falls back to the next keyword, so one bad card can
        no longer abort the whole extraction.
        """
        for key in keys:
            try:
                value = header.get(key)
            except Exception as e:
                logger.warning(f"FITS extractor: unreadable card '{key}' in {self.file_path}: {e}")
                continue
            # Some astropy versions surface the parse error as a returned
            # exception rather than raising it - treat both as failures.
            if isinstance(value, Exception):
                logger.warning(f"FITS extractor: unreadable card '{key}' in {self.file_path}: {value}")
                continue
            if value is None or isinstance(value, fits.card.Undefined):
                # Missing or blank card - try the next fallback keyword
                continue
            return value
        return default

    @staticmethod
    def _raw_card_value(card):
        """
        Best-effort recovery of a value from a card that astropy cannot parse,
        by reading the raw 80-character card image (e.g. 'CCD-TEMP = -19.80C').
        Returns the string payload, or None if it cannot be recovered.
        """
        try:
            image = card.image or ""
        except Exception:
            return None
        if "=" not in image:
            return None
        value = image.split("=", 1)[1].strip()
        if value.startswith("'"):
            # Quoted string: take everything up to the closing quote
            end = value.find("'", 1)
            if end == -1:
                return value.lstrip("'").rstrip("' ").strip()
            return value[1:end].replace("''", "'").strip()
        # Unquoted value: strip an inline comment introduced by '/'
        if "/" in value:
            value = value.split("/", 1)[0].strip()
        return value or None

    def _get_binning(self, header):
        """"NxM" from XBINNING/YBINNING (handles 2, 2.0, "2"); YBINNING defaults to XBINNING."""
        x = self._parse_int(self._safe_get(header, "XBINNING"))
        if not x:
            return None
        y = self._parse_int(self._safe_get(header, "YBINNING")) or x
        return f"{x}x{y}"

    def _get_exposure(self, header) -> float:
        """Try multiple keywords for exposure time."""
        value = self._safe_get(header, "EXPTIME", "EXPOSURE")
        try:
            return float(value) if value is not None else 0.0
        except (ValueError, TypeError):
            return 0.0

    def _get_date(self, header) -> datetime:
        """Parse DATE-OBS or DATE."""
        date_str = self._safe_get(header, "DATE-OBS", "DATE")
        if not date_str:
            return None
            
        if not isinstance(date_str, str):
            return None

        # Clean up string
        date_str = date_str.strip()
        
        # Handle 60 seconds case (some FITS writers use this instead of rolling over minutes)
        if "T" in date_str and ":60" in date_str:
            try:
                # Replace :60 with :59 and add 1 second later, or just use 00 mapping
                # Simple hack: replace :60 with :00 (approximate enough for capture date)
                # Better: parse manually and handle rollover
                parts = date_str.split("T")
                time_parts = parts[1].split(":")
                if len(time_parts) >= 3 and time_parts[2].startswith("60"):
                    # Just cap it at 59 for easy parsing without full rollover logic
                    time_parts[2] = "59" + time_parts[2][2:]
                    date_str = parts[0] + "T" + ":".join(time_parts)
            except:
                pass

        try:
            # Try ISO format (2023-01-01T12:00:00)
            return datetime.fromisoformat(date_str)
        except ValueError:
            # Try common variations
            formats = [
                "%Y-%m-%d",
                "%Y/%m/%d",
                "%d/%m/%Y",
                "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S",
            ]
            for fmt in formats:
                try:
                    return datetime.strptime(date_str, fmt)
                except ValueError:
                    continue
            return None

    def _extract_wcs(self, header) -> Dict[str, Any]:
        """Extract WCS coordinates if present with fallback to non-standard keywords."""
        
        # Initialize defaults
        ra_center, dec_center = None, None
        rotation = 0.0
        radius_degrees = 1.0
        wcs_type = "NONE"

        # Plate scale is derived from keywords alone (not the astropy WCS
        # object) so a WCS that fails to parse can't push us onto a bogus
        # fallback, and every source is cross-checked against the optics.
        pixel_scale, _ = header_pixel_scale(header)

        # 1. Try Standard WCS logic
        if "CRVAL1" in header and "CRVAL2" in header:
            try:
                # Only use the WCS transformation if the header defines a scale.
                # Without CD/CDELT astropy assumes 1 deg/pixel, causing massive
                # offsets in RA/Dec (e.g. 1.5 degrees at 45deg Lat for 1px offset).
                # The WCS may refer to a downsampled solve grid (ASIAIR IMAGEW x
                # IMAGEH), so the center and corner are taken on that grid.
                fw, fh = wcs_frame(header)
                if wcs_matrix_scale(header) and fw and fh:
                    w = WCS(self._wcs_header(header))
                    # Note: n/2 is slightly off from (n-1)/2 center but consistent with existing logic.
                    center = w.pixel_to_world(fw / 2, fh / 2)
                    ra_center = center.ra.degree
                    dec_center = center.dec.degree
                    wcs_type = "HEADER_WCS"
                    
                    # diagonal radius
                    corner = w.pixel_to_world(0, 0)
                    radius_degrees = center.separation(corner).degree
                    # rotation
                    if "CD1_1" in header:
                        cd12 = self._parse_float(self._safe_get(header, "CD1_2", default=0)) or 0.0
                        cd22 = self._parse_float(self._safe_get(header, "CD2_2", default=0)) or 0.0
                        rotation = math.degrees(math.atan2(-cd12, cd22)) 
                    elif "CROTA2" in header:
                        crota2 = self._parse_float(self._safe_get(header, "CROTA2"))
                        if crota2 is not None:
                            rotation = float(crota2)
                else:
                    # Fallback to direct CRVALs if WCS object is untrusted
                    ra_center = self._parse_float(self._safe_get(header, "CRVAL1"))
                    dec_center = self._parse_float(self._safe_get(header, "CRVAL2"))
                    if ra_center is not None and dec_center is not None:
                        wcs_type = "HEADER_CRVAL"
            except Exception as e:
                logger.debug(f"FITS extractor: unusable header WCS in {self.file_path}: {e}")
                ra_center, dec_center, rotation, radius_degrees = None, None, 0.0, 1.0

        # 2. Fallback for coordinates if standard WCS failed or was incomplete
        if ra_center is None:
            # Try direct RA/DEC keywords
            ra_val = self._safe_get(header, "RA")
            if ra_val is None:
                ra_val = self._parse_hms_dms(self._safe_get(header, "OBJCTRA"), is_ra=True)
            
            dec_val = self._safe_get(header, "DEC")
            if dec_val is None:
                dec_val = self._parse_hms_dms(self._safe_get(header, "OBJCTDEC"), is_ra=False)
            
            # Final check/parse
            ra_center = self._parse_coord_or_hms(ra_val, is_ra=True)
            dec_center = self._parse_coord_or_hms(dec_val, is_ra=False)
            
            if ra_center is not None and dec_center is not None:
                if wcs_type == "NONE": wcs_type = "HEADER_FALLBACK"

        # 3. Fallback for Rotation / radius if missing from standard WCS
        if ra_center is not None and dec_center is not None:
            if rotation == 0:
                rot = self._safe_get(header, "ROTATION", "POSANGLE", "ANGLE", "POSANG", "ROTATANG", "ROTATOR")
                if rot:
                    rotation = self._parse_float(rot) or 0.0
            
            # Recalculate radius if we have a better pixel scale
            # (Either radius is default 1.0 or suspiciously large due to 1deg/px default)
            if pixel_scale and (radius_degrees == 1.0 or radius_degrees > 20.0):
                n1 = self._parse_int(self._safe_get(header, "NAXIS1", default=0)) or 0
                n2 = self._parse_int(self._safe_get(header, "NAXIS2", default=0)) or 0
                if n1 and n2:
                    diagonal = math.sqrt(n1**2 + n2**2)
                    radius_degrees = (diagonal / 2.0) * pixel_scale / 3600.0
                elif radius_degrees > 20.0:
                    radius_degrees = 1.0 # Safe fallback

            return {
                "ra_center": float(ra_center),
                "dec_center": float(dec_center),
                "radius_degrees": float(radius_degrees),
                "pixel_scale": float(pixel_scale) if pixel_scale else None,
                "rotation": float(rotation),
                "wcs_type": wcs_type
            }

        return None

    _WCS_KEY = re.compile(
        r"^(NAXIS[12]?|WCSAXES|CTYPE[12]|CRVAL[12]|CRPIX[12]|CDELT[12]|CUNIT[12]|CROTA[12]"
        r"|CD[12]_[12]|PC[12]_[12]|LONPOLE|LATPOLE|EQUINOX|EPOCH|RADESYS|RADECSYS"
        r"|(A|B|AP|BP)_ORDER|(A|B|AP|BP)_\d+_\d+)$"
    )

    def _wcs_header(self, header) -> fits.Header:
        """
        A clean astropy Header holding only the WCS cards, for WCS(). Works for
        both an astropy Header and a raw_header dict (XISF keywords, stored
        rows), whose COMMENT/HISTORY lists and odd values WCS() can't take.
        """
        clean = fits.Header()
        for key in list(header.keys()):
            if not isinstance(key, str) or not self._WCS_KEY.match(key):
                continue
            value = self._safe_get(header, key)
            if isinstance(value, str):
                num = self._parse_float(value)
                if num is not None and not key.startswith(("CTYPE", "CUNIT", "RADESYS", "RADECSYS")):
                    value = num
            if isinstance(value, (int, float, str)) and not isinstance(value, bool):
                clean[key] = value
        return clean

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
        return self._parse_float(val)
