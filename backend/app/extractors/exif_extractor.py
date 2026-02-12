"""
EXIF Extractor
Extracts metadata from standard image formats (JPG, TIFF) and Raw files (CR2, NEF, ARW).
Uses Pillow for standard images and basic Raw parsing (or rawpy/exifread if needed).
"""

from typing import Dict, Any
from datetime import datetime
from PIL import Image, ExifTags

from app.extractors.base import BaseExtractor


class ExifExtractor(BaseExtractor):
    """Extractor for consumer camera images (DSLR/Mirrorless)."""

    def extract(self) -> Dict[str, Any]:
        metadata = {}
        raw_header = {}
        
        # Method 1: Try ExifRead (Better for RAW files)
        try:
            import exifread
            with open(self.file_path, 'rb') as f:
                tags = exifread.process_file(f, details=False)
                
                # --- Exposure Time ---
                if 'EXIF ExposureTime' in tags:
                    val = tags['EXIF ExposureTime'].values[0]
                    try:
                        metadata['exposure_time_seconds'] = float(val.num) / float(val.den)
                    except (ValueError, ZeroDivisionError, TypeError, AttributeError):
                        pass
                
                # --- ISO / Gain ---
                if 'EXIF ISOSpeedRatings' in tags:
                    val = tags['EXIF ISOSpeedRatings'].values[0]
                    metadata['iso_speed'] = self._parse_int(val)
                    metadata['gain'] = self._parse_float(val)
                
                # --- Date ---
                date_tag = tags.get('EXIF DateTimeOriginal') or tags.get('Image DateTime')
                if date_tag:
                    try:
                        metadata['capture_date'] = datetime.strptime(str(date_tag), "%Y:%m:%d %H:%M:%S")
                    except ValueError:
                        pass

                # --- Camera Info ---
                make = str(tags.get('Image Make', '')).strip()
                model = str(tags.get('Image Model', '')).strip()
                metadata['camera_name'] = f"{make} {model}".strip()
                
                # --- Lens Model ---
                lens_tag = tags.get('EXIF LensModel')
                if lens_tag:
                    metadata['lens_model'] = str(lens_tag).strip()
                
                # --- Aperture (F-number) ---
                if 'EXIF FNumber' in tags:
                    val = tags['EXIF FNumber'].values[0]
                    try:
                        metadata['aperture'] = self._parse_float(val.num) / self._parse_float(val.den) if hasattr(val, 'num') else self._parse_float(val)
                    except (ValueError, ZeroDivisionError, TypeError, AttributeError):
                        pass
                
                # --- Focal Length ---
                if 'EXIF FocalLength' in tags:
                    val = tags['EXIF FocalLength'].values[0]
                    try:
                        metadata['focal_length'] = self._parse_float(val.num) / self._parse_float(val.den) if hasattr(val, 'num') else self._parse_float(val)
                    except (ValueError, ZeroDivisionError, TypeError, AttributeError):
                        pass
                
                # --- Focal Length in 35mm equivalent ---
                if 'EXIF FocalLengthIn35mmFilm' in tags:
                    val = tags['EXIF FocalLengthIn35mmFilm'].values[0]
                    try:
                        metadata['focal_length_35mm'] = self._parse_float(val) if isinstance(val, (int, float)) else self._parse_float(str(val).split()[0])
                    except (ValueError, AttributeError, TypeError):
                        pass
                
                # --- Rating ---
                # Note: Rating will be extracted from XMP first (higher priority)
                # This EXIF rating extraction is just for storage in raw_header
                rating_tag = tags.get('Image Rating') or tags.get('EXIF Rating')
                if rating_tag:
                    try:
                        rating_val = int(rating_tag.values[0]) if hasattr(rating_tag, 'values') else int(rating_tag)
                        # Note: Not setting metadata['rating'] here - XMP has priority
                    except (ValueError, IndexError, AttributeError):
                        pass
                
                # --- White Balance ---
                if 'EXIF WhiteBalance' in tags:
                    val = tags['EXIF WhiteBalance'].values[0]
                    metadata['white_balance'] = str(val).strip()
                
                # --- Metering Mode ---
                if 'EXIF MeteringMode' in tags:
                    val = tags['EXIF MeteringMode'].values[0]
                    metadata['metering_mode'] = str(val).strip()
                
                # --- Flash ---
                if 'EXIF Flash' in tags:
                    val = tags['EXIF Flash'].values[0]
                    # Flash value: if bit 0 is set (odd number), flash fired
                    try:
                        flash_val = int(val) if isinstance(val, int) else int(str(val).split()[0])
                        metadata['flash_fired'] = bool(flash_val & 0x1)  # Check if bit 0 is set
                    except (ValueError, AttributeError):
                        pass

                # Store all exifread tags in raw_header
                for tag_name, tag_value in tags.items():
                    raw_header[f"EXIF:{tag_name}"] = self._make_serializable(tag_value)
        
        except Exception as e:
            print(f"ExifRead failed for {self.file_path}: {e}")
        
        # Try to extract XMP rating with priority chain:
        # 1. XMP Sidecar file (highest - external editor like Lightroom)
        # 2. Embedded XMP (user-controlled metadata)
        try:
            # First check for XMP sidecar file
            sidecar_rating = self._extract_xmp_sidecar_rating()
            if sidecar_rating is not None:
                metadata['rating'] = sidecar_rating
            else:
                # Fall back to embedded XMP
                xmp_rating = self._extract_xmp_rating()
                if xmp_rating is not None:
                    metadata['rating'] = xmp_rating
        except Exception as e:
            pass  # XMP parsing is optional
        
        # If no XMP rating found, try standard EXIF sources as fallback
        if 'rating' not in metadata or metadata['rating'] is None:
            try:
                import exifread
                with open(self.file_path, 'rb') as f:
                    tags = exifread.process_file(f, details=False)
                    # Check for Rating tag from EXIF sources (fallback)
                    rating_tag = (tags.get('Image Rating') or 
                                 tags.get('EXIF Rating') or 
                                 tags.get('MakerNote Rating'))
                    if rating_tag:
                        try:
                            rating_val = self._parse_int(rating_tag.values[0]) if hasattr(rating_tag, 'values') else self._parse_int(rating_tag)
                            if rating_val is not None and 0 <= rating_val <= 5:
                                metadata['rating'] = rating_val
                        except (ValueError, IndexError, AttributeError):
                            pass
            except Exception:
                pass  # Fallback failed, leave rating as extracted from XMP

        # Method 2: Pillow (Fallback & Dimensions)
        try:
            with Image.open(self.file_path) as img:
                # Always trust Pillow for dimensions if it can open the file
                metadata["width_pixels"] = img.width
                metadata["height_pixels"] = img.height
                
                # If ExifRead failed to get date/exposure, try Pillow's EXIF
                exif = img._getexif()
                if exif:
                    exif_data = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}
                    
                    # Store all Pillow EXIF tags in raw_header
                    for tag_name, tag_value in exif_data.items():
                        key = f"PIL:{tag_name}"
                        # Don't overwrite if EXIF already has it (optional, but PIL handles some things differently)
                        raw_header[key] = self._make_serializable(tag_value)

                    if "capture_date" not in metadata:
                        # Date
                        date_str = exif_data.get("DateTimeOriginal") or exif_data.get("DateTime")
                        if date_str:
                            try:
                                metadata["capture_date"] = datetime.strptime(date_str, "%Y:%m:%d %H:%M:%S")
                            except (ValueError, TypeError):
                                pass
                        
                    if "exposure_time_seconds" not in metadata:
                        # Exposure
                        exp_time = exif_data.get("ExposureTime")
                        if exp_time:
                            if isinstance(exp_time, tuple):
                                metadata["exposure_time_seconds"] = float(exp_time[0]) / float(exp_time[1])
                            elif isinstance(exp_time, (int, float)):
                                metadata["exposure_time_seconds"] = float(exp_time)
                    
                    # Extract additional fields from Pillow EXIF if not already extracted
                    if "gain" not in metadata:
                        iso = exif_data.get("ISOSpeedRatings")
                        if iso:
                            metadata["gain"] = self._parse_float(iso) if isinstance(iso, (int, float)) else self._parse_float(str(iso).split()[0])
                    
                    if "aperture" not in metadata:
                        fnum = exif_data.get("FNumber")
                        if fnum:
                            try:
                                metadata["aperture"] = self._parse_float(fnum[0]) / self._parse_float(fnum[1]) if isinstance(fnum, tuple) else self._parse_float(fnum)
                            except (ValueError, ZeroDivisionError, TypeError):
                                pass
                    
                    if "focal_length" not in metadata:
                        fl = exif_data.get("FocalLength")
                        if fl:
                            try:
                                metadata["focal_length"] = self._parse_float(fl[0]) / self._parse_float(fl[1]) if isinstance(fl, tuple) else self._parse_float(fl)
                            except (ValueError, ZeroDivisionError, TypeError):
                                pass
                    
                    if "focal_length_35mm" not in metadata:
                        fl35 = exif_data.get("FocalLengthIn35mmFilm")
                        if fl35:
                            metadata["focal_length_35mm"] = self._parse_float(fl35)
                    
                    if "rating" not in metadata:
                        rating = exif_data.get("Rating")
                        if rating:
                            rating_val = self._parse_int(rating) if isinstance(rating, (int, float)) else self._parse_int(str(rating).split()[0])
                            if rating_val is not None and 0 <= rating_val <= 5:
                                metadata["rating"] = rating_val
                    
                    if "white_balance" not in metadata:
                        wb = exif_data.get("WhiteBalance")
                        if wb:
                            metadata["white_balance"] = str(wb).strip()
                    
                    if "metering_mode" not in metadata:
                        mm = exif_data.get("MeteringMode")
                        if mm:
                            metadata["metering_mode"] = str(mm).strip()
                    
                    if "flash_fired" not in metadata:
                        flash = exif_data.get("Flash")
                        if flash is not None:
                            try:
                                flash_val = int(flash) if isinstance(flash, int) else int(str(flash).split()[0])
                                metadata["flash_fired"] = bool(flash_val & 0x1)
                            except (ValueError, TypeError):
                                pass

        except Exception as e:
            # Pillow failed (likely RAW file it doesn't support)
            pass

        # Method 3: rawpy (Best for RAW files like CR3, CR2, NEF etc.)
        if "width_pixels" not in metadata or "height_pixels" not in metadata:
            try:
                import rawpy
                with rawpy.imread(str(self.file_path)) as raw:
                    metadata["width_pixels"] = raw.sizes.raw_width
                    metadata["height_pixels"] = raw.sizes.raw_height
            except Exception:
                pass

        # Method 4: tifffile (Best for complex TIFFs)
        if "width_pixels" not in metadata or "height_pixels" not in metadata:
            try:
                import tifffile
                with tifffile.TiffFile(str(self.file_path)) as tif:
                    page = tif.pages[0]
                    metadata["width_pixels"] = page.imagewidth
                    metadata["height_pixels"] = page.imagelength
            except Exception:
                pass
                    
        # Check for sidecar WCS data (Astrometry.net .ini, etc.)
        try:
            from app.extractors.ini_parser import SidecarParser
            sidecar_data = SidecarParser.parse(self.file_path)
            if sidecar_data:
                metadata["wcs"] = sidecar_data
                metadata["is_plate_solved"] = True
                metadata["plate_solve_source"] = "SIDECAR"
                # Add sidecar data to raw_header too
                raw_header["SIDECAR"] = sidecar_data
        except Exception as e:
            print(f"Error parsing sidecar for {self.file_path}: {e}")

        metadata["raw_header"] = raw_header
        return metadata

    def _make_serializable(self, obj: Any) -> Any:
        """Helper to ensure EXIF values are JSON serializable."""
        if isinstance(obj, (str, int, float, bool, type(None))):
            return obj
        if isinstance(obj, (list, tuple)):
            return [self._make_serializable(i) for i in obj]
        if isinstance(obj, dict):
            return {str(k): self._make_serializable(v) for k, v in obj.items()}
        
        # Handle ExifRead types
        try:
            import exifread
            if isinstance(obj, exifread.classes.IfdTag):
                # If it's a list, return the list of values
                if len(obj.values) > 1:
                    return [self._make_serializable(v) for v in obj.values]
                # If it's a single value, return it
                return self._make_serializable(obj.values[0])
            if isinstance(obj, exifread.utils.Ratio):
                if obj.den == 0: return 0
                return float(obj.num) / float(obj.den)
        except (ImportError, Exception):
            pass

        # Fallback to string representation
        return str(obj)

    def _extract_xmp_sidecar_rating(self) -> int:
        """
        Extract rating from XMP sidecar file (.xmp) alongside the image.
        Sidecar files are commonly used by Lightroom, Bridge, darktable, etc.
        """
        try:
            import re
            
            # Check for common XMP sidecar naming patterns
            # Pattern 1: image.jpg.xmp (Lightroom default)
            # Pattern 2: image.xmp (same basename, different extension)
            sidecar_paths = [
                self.file_path.with_suffix(self.file_path.suffix + '.xmp'),  # image.jpg.xmp
                self.file_path.with_suffix('.xmp'),  # image.xmp
            ]
            
            for sidecar_path in sidecar_paths:
                if sidecar_path.exists():
                    with open(sidecar_path, 'rb') as f:
                        data = f.read()
                    
                    rating = self._parse_xmp_rating_from_data(data)
                    if rating is not None:
                        return rating
            
            return None
        except Exception:
            return None

    def _extract_xmp_rating(self) -> int:
        """Extract rating from XMP metadata embedded in the file."""
        try:
            with open(self.file_path, 'rb') as f:
                data = f.read()
            
            # Look for XMP data (could be in various formats)
            if b'xmp:Rating' not in data and b'<x:xmpmeta' not in data:
                return None
            
            return self._parse_xmp_rating_from_data(data)
        except Exception:
            return None

    def _parse_xmp_rating_from_data(self, data: bytes) -> int:
        """
        Parse XMP rating from raw bytes data.
        Handles multiple XMP rating formats used by different software.
        """
        try:
            import re
            
            # Try multiple XMP rating formats
            # Format 1: <xmp:Rating>5</xmp:Rating> (element format - common)
            match = re.search(rb'<xmp:Rating>(\d+)</xmp:Rating>', data)
            if match:
                rating_val = int(match.group(1))
                if 0 <= rating_val <= 5:
                    return rating_val
            
            # Format 2: xmp:Rating="5" (attribute format - TIFF/Photoshop)
            match = re.search(rb'xmp:Rating="(\d+)"', data)
            if match:
                rating_val = int(match.group(1))
                if 0 <= rating_val <= 5:
                    return rating_val
            
            # Format 3: <Rating>5</Rating> (without namespace)
            match = re.search(rb'<Rating>(\d+)</Rating>', data)
            if match:
                rating_val = int(match.group(1))
                if 0 <= rating_val <= 5:
                    return rating_val
            
            # Format 4: exif:Rating="5" (EXIF namespace in XMP)
            match = re.search(rb'exif:Rating="(\d+)"', data)
            if match:
                rating_val = int(match.group(1))
                if 0 <= rating_val <= 5:
                    return rating_val
            
            return None
        except Exception:
            return None

