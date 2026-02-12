# EXIF Field Mapping Reference

## Extracted Photography Metadata Fields

This document lists all EXIF fields now being extracted for non-FITS image types (JPG, CR2, ARW, NEF, etc.).

### Rating
| Field | EXIF Tags Checked | Type | Range | Notes |
|-------|------------------|------|-------|-------|
| `rating` | `Image Rating`, `EXIF Rating` | Integer | 0-5 | Common in Windows Photo Gallery, Adobe, some camera firmware |

### Aperture
| Field | EXIF Tags Checked | Type | Example | Notes |
|-------|------------------|------|---------|-------|
| `aperture` | `EXIF FNumber` | Float | 2.8, 5.6, 11.0 | Extracted as ratio (num/den) or direct value |

### Focal Length
| Field | EXIF Tags Checked | Type | Example | Notes |
|-------|------------------|------|---------|-------|
| `focal_length` | `EXIF FocalLength` | Float | 50.0, 24.0, 200.0 | In millimeters |
| `focal_length_35mm` | `EXIF FocalLengthIn35mmFilm` | Float | 75.0 | 35mm equivalent focal length |

### White Balance
| Field | EXIF Tags Checked | Type | Example Values | Notes |
|-------|------------------|------|---|-------|
| `white_balance` | `EXIF WhiteBalance` | String | Auto, Daylight, Cloudy, Tungsten, Fluorescent, Flash | Camera white balance mode used |

### Metering Mode
| Field | EXIF Tags Checked | Type | Example Values | Notes |
|-------|------------------|------|---|-------|
| `metering_mode` | `EXIF MeteringMode` | String | Matrix, CenterWeighted, Spot, Partial | Exposure metering method used |

### Flash
| Field | EXIF Tags Checked | Type | Values | Notes |
|-------|------------------|------|--------|-------|
| `flash_fired` | `EXIF Flash` | Boolean | true/false/null | Detected via bit 0 of Flash value (0x0001) |

### Lens Model
| Field | EXIF Tags Checked | Type | Example | Notes |
|-------|------------------|------|---------|-------|
| `lens_model` | `EXIF LensModel` | String | "Canon EF 70-200mm f/4L IS USM" | Lens information if available |

### Existing Fields (Already Extracted)
| Field | EXIF Tags | Type | Notes |
|-------|-----------|------|-------|
| `gain` | `EXIF ISOSpeedRatings` | Float | Already extracted |
| `exposure_time_seconds` | `EXIF ExposureTime` | Float | Already extracted |
| `capture_date` | `EXIF DateTimeOriginal`, `Image DateTime` | DateTime | Already extracted |
| `camera_name` | `Image Make`, `Image Model` | String | Already extracted |

## Extraction Methods

### Method 1: ExifRead (Primary)
- Used for all image types
- Particularly good for RAW files (CR2, NEF, ARW, etc.)
- Provides comprehensive EXIF tag access
- Tags are read with full namespace: `EXIF ExposureTime`, `Image Rating`, etc.

### Method 2: Pillow (Fallback)
- Used when ExifRead fails (rare)
- Handles standard formats (JPG, TIFF, PNG)
- Falls back to PIL EXIF parsing
- Used for dimensions (width, height) as primary source

## Data Type Handling

### Ratio Handling
EXIF often stores numeric values as fractions (Ratio objects):
```python
val = tags['EXIF FNumber'].values[0]
if hasattr(val, 'num'):  # Ratio object
    aperture = float(val.num) / float(val.den)
else:  # Direct value
    aperture = float(val)
```

### Flash Detection
Flash status is encoded in bits. Bit 0 indicates flash fired:
```python
flash_val = int(val)
flash_fired = bool(flash_val & 0x1)  # Check bit 0
```

### Rating Validation
Only ratings in 0-5 range are accepted:
```python
if 0 <= rating_val <= 5:
    metadata['rating'] = rating_val
```

## Camera/Lens Support

These fields are supported across:
- **DSLR**: Canon, Nikon, Sony
- **Mirrorless**: Canon, Nikon, Sony (via EXIF)
- **Raw Formats**: CR2, CR3 (Canon), NEF (Nikon), ARW (Sony), DNG (Adobe)
- **Standard**: JPG, TIFF, PNG

Note: Some fields may be empty for images without complete EXIF data (e.g., lens_model may not be available for all RAW files).

## Database Storage

All fields are stored as nullable columns in the `images` table:
- Existing images will have NULL values until re-indexed
- New images indexed after migration will have values extracted
- No data loss on downgrade migration
