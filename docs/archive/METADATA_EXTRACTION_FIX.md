# Photography Metadata Extraction Fix

## Problem
Non-FITS/FITS/XISF image types (JPG, CR2, etc.) had incomplete metadata extraction. While all EXIF tags were stored in the `raw_header` JSONB field, important photography metadata fields were not being extracted and stored as discrete database columns. Most notably, the **rating field** was missing.

## Root Cause
The EXIF extractor was only extracting a minimal set of fields:
- exposure_time_seconds
- gain
- capture_date
- camera_name

All other EXIF data was stored only in the raw_header, making it difficult to query and filter by common photography metadata.

## Solution

### 1. Extended Image Model (image.py)
Added 8 new columns to store photography metadata:
- **rating** (Integer): User rating from EXIF metadata (0-5 scale)
- **aperture** (Float): F-number (e.g., 2.8, 5.6)
- **focal_length** (Float): Focal length in mm
- **focal_length_35mm** (Float): 35mm equivalent focal length
- **white_balance** (String): Auto, Daylight, Tungsten, etc.
- **metering_mode** (String): Matrix, Center-weighted, Spot, etc.
- **flash_fired** (Boolean): Whether flash was used
- **lens_model** (String): Lens model name

### 2. Enhanced EXIF Extractor (exif_extractor.py)
Extended both extraction methods (ExifRead and Pillow) to extract:
- Rating from 'Image Rating' or 'EXIF Rating' tags
- Aperture from 'EXIF FNumber'
- Focal length from 'EXIF FocalLength'
- Focal length 35mm equivalent from 'EXIF FocalLengthIn35mmFilm'
- White balance from 'EXIF WhiteBalance'
- Metering mode from 'EXIF MeteringMode'
- Flash status from 'EXIF Flash' (checks bit 0)
- Lens model from 'EXIF LensModel'

#### Key Implementation Details:
- **Dual extraction methods**: Uses ExifRead for RAW files (CR2, NEF, ARW) and Pillow as fallback
- **Safe parsing**: Handles various EXIF value formats (Ratio objects, tuples, direct values)
- **Rating validation**: Only accepts ratings in 0-5 range
- **Flash bit detection**: Uses bitwise AND to check if flash fired bit is set
- **Fallback chains**: Each field has multiple possible EXIF tag names to check

### 3. Updated Indexer Task (indexer.py)
Modified both create and update paths in `process_image()` to store all new photography fields extracted by the EXIF extractor.

### 4. Updated API Schema (image.py - schemas)
Added all 8 new fields to both `ImageBase` and derived schemas so they are returned in API responses.

### 5. Database Migration
Created migration `2026_01_28_1000-add_photography_metadata_fields.py` to add the 8 new columns to the `images` table.

## Testing
All modified Python files pass syntax validation:
- ✓ app/models/image.py
- ✓ app/extractors/exif_extractor.py
- ✓ app/tasks/indexer.py
- ✓ app/schemas/image.py

## Migration Steps
1. Apply the database migration: `alembic upgrade head`
2. Re-index images to extract new metadata: Call `/api/indexer/scan`
3. New API responses will include all photography metadata fields

## Backward Compatibility
- All new columns are nullable (default NULL)
- Existing images will have NULL values until re-indexed
- API responses maintain backward compatibility (new fields just appear in JSON)
- No breaking changes to existing API contract

## Benefits
- **Better search/filtering**: Can now filter by rating, aperture, focal length, etc.
- **Complete metadata**: All important camera settings are now stored as database columns
- **Faster queries**: Photography metadata is indexed separately, not buried in JSONB
- **Improved frontend**: UI can display and filter by these photography properties
- **Consistency**: Both EXIF methods extract the same fields reliably
