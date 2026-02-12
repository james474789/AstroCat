# Thumbnail Generation Process

## Overview

The AstroCat thumbnail system generates JPEG preview images from various astronomy image formats (FITS, RAW, XISF, standard images) and stores them in a cache directory. Thumbnails are created on-demand and can be regenerated in bulk. The system applies intelligent image processing, including PixInsight-style STF (Stretch, Transfer Function) auto-stretching for astronomical subframes.

## Architecture

### Components

1. **ThumbnailGenerator Service** (`backend/app/services/thumbnails.py`)
   - Core image loading and processing logic
   - Handles multiple image formats (RAW, FITS, XISF, TIFF, JPEG, PNG, etc.)
   - Applies STF auto-stretching for subframes

2. **Thumbnail Tasks** (`backend/app/tasks/thumbnails.py`)
   - Celery background task for individual thumbnail generation
   - Single image regeneration with optional force flag

3. **Bulk Thumbnail Task** (`backend/app/tasks/bulk.py`)
   - Bulk regeneration for all images in a mount point/folder
   - Progress tracking via Redis

4. **Indexer Tasks** (`backend/app/tasks/indexer.py`)
   - Global regeneration task for all thumbnails
   - Statistics update task

5. **API Endpoints** (`backend/app/api/indexer.py`)
   - Retrieve thumbnail statistics
   - Clear thumbnail cache
   - Trigger regeneration
   - View/serve thumbnails

## How It Works

### 1. Image Loading and Format Detection

The `ThumbnailGenerator.load_source_image()` method loads images based on file extension:

#### RAW Files (.cr2, .nef, .arw, .dng, .raf, .cr3)
- Uses `rawpy` library for debayering
- Extracts linear RGB data with white balance applied
- Applies STF stretching if `is_subframe=True`

#### FITS Files (.fits, .fit)
- Uses `astropy.io.fits` to read FITS HDU data
- Extracts the first multi-dimensional data array
- Handles single channel and multi-channel data
- Applies STF for subframes, percentile normalization for others

#### XISF Files (.xisf)
- Uses `xisf` library for specialized astrophotography format
- Extracts and normalizes data similar to FITS
- Includes vertical flip (standard for XISF)

#### Standard Images (TIFF, JPEG, PNG, etc.)
- Uses PIL/Pillow for standard formats
- Fallback to `tifffile` for specialized TIFFs (32-bit float, etc.)
- High bit-depth modes (I, I;16, F, etc.) are normalized properly

### 2. Image Processing: STF Auto-Stretching

The STF (Stretch Transfer Function) stretching mimics PixInsight's auto-stretch algorithm for optimal visibility of astronomical subframes:

```
1. Normalization: Scale data to [0, 1] range
2. Statistics: Calculate median and median absolute deviation (MAD)
3. Shadow Clipping: Clip shadows at (median - 1.25 * MAD), minimum 0
4. Highlight Clipping: Clip highlights at 1.0
5. Midtones Transfer: Apply MTF curve to map median to a target background level (0.25)
6. Final Clipping: Ensure output is in [0, 1] and convert to uint8
```

**When is STF applied?**
- Images marked as `SUBFRAME` subtype: Always apply STF
- Other images: Simple percentile normalization (1st-99th percentile) or no stretch

### 3. Thumbnail Generation

The `ThumbnailGenerator.generate()` method:

1. **Path Hashing**: Creates unique filename using MD5 hash of source path
   - Format: `{stem}_{path_hash}_thumb.jpg`
   - Example: `image_abc12345_thumb.jpg`
   - Prevents collisions when same filename exists in different folders

2. **Image Processing**:
   - Load source image using `load_source_image()`
   - Apply Lanczos resampling to fit within max_size (default 800x800)
   - Save as JPEG with quality=85

3. **Storage**: Saves thumbnail to cache directory
   - Default path: `/data/thumbnails/` (configurable via `THUMBNAIL_CACHE_PATH`)
   - File exists check: Returns path on success, None on failure

### 4. Database Integration

Once thumbnail is generated:
1. Task updates `Image.thumbnail_path` in database
2. Path stored as absolute filesystem path
3. Used by API to serve cached files

## Generation Triggers

### Individual Thumbnail Generation

**On-Demand via API** (when user views an image):
- Triggered if thumbnail_path is null or file doesn't exist
- API endpoint: `GET /api/images/{id}/thumbnail`
- If missing, returns 404 (user may trigger manual regeneration)

**Manual Batch Fix**:
- Use the Admin panel's "Regenerate All" button
- Or call `POST /api/indexer/thumbnails/regenerate`
- Finds all images and queues regeneration for each using Celery

### Bulk Regeneration

**Per Mount Point**:
- API: `POST /api/indexer/batch/thumbnails`
- Task: `bulk_thumbnail_task(mount_path)`
- Finds all images matching path pattern
- Queues individual regeneration tasks with `force=True`
- Progress tracked in Redis with key: `bulk:thumbnails:{mount_hash}`

**Global Regeneration**:
- API: `POST /api/indexer/thumbnails/regenerate`
- Task: `regenerate_thumbnails()`
- Regenerates ALL thumbnails in database
- Queues each image as a separate Celery task
- Updates statistics after queuing completes

### Cache Management

**Clear All Thumbnails**:
- API: `POST /api/indexer/thumbnails/clear`
- Deletes all files from disk cache directory
- Clears `thumbnail_path` column for all images in database
- Recalculates statistics

**View Statistics**:
- API: `GET /api/indexer/thumbnails/stats`
- Returns: count, size_bytes, size_mb from SystemStats table

## Data Model

### Image Model Fields
- `thumbnail_path` (String, nullable): Absolute filesystem path to cached JPEG
- `is_plate_solved` (Boolean): Used to determine if STF stretching should apply
- `subtype` (Enum): ImageSubtype.SUB_FRAME determines STF application

### Thumbnail File Structure
```
/data/thumbnails/
├── image001_a1b2c3d4_thumb.jpg     (from /data/mount1/image001.fits)
├── image001_e5f6g7h8_thumb.jpg     (from /data/mount2/image001.fits)
├── deepsky_12345678_thumb.jpg      (from /data/mount3/deepsky.cr2)
└── annotated_{image_id}.jpg        (overlay annotations, if any)
```

## Processing Flow Diagram

```
User/Admin Action
       ↓
    API Endpoint
       ↓
    Celery Task (queued)
       ↓
    Load Image File
       ↓
    Detect Format
       ↓
    Process Image Data
    ├─ RAW: Use rawpy
    ├─ FITS: Use astropy
    ├─ XISF: Use xisf
    └─ Standard: Use PIL
       ↓
    Apply STF (if subframe)
       ↓
    Resize to Max Size
       ↓
    Save as JPEG (quality=85)
       ↓
    Update Database
       ↓
    Return Status
```

## Configuration

### Environment Variables
- `THUMBNAIL_CACHE_PATH`: Directory for cached thumbnails (default: `/data/thumbnails`)

### Task Settings
- `max_size`: Default (800, 800) pixels for thumbnail dimensions
- `quality`: JPEG quality 85 (good balance of quality vs size)
- `is_subframe`: Determined automatically from `image.subtype == ImageSubtype.SUB_FRAME`

## Performance Considerations

### Redis Progress Tracking
- Bulk operations track progress in Redis with 1-hour TTL
- Progress key format: `bulk:thumbnails:{mount_hash}` or `bulk:rescan:{mount_hash}`
- Fields: status, processed, total, updated_at, errors

### Task Queuing Strategy
- Individual generation tasks are queued immediately
- Large libraries may queue thousands of Celery tasks
- Workers process in parallel (depends on worker pool size)
- Stats update triggered after queuing completes

### Caching Directory Size
- JPEG thumbnails typically 50-200KB each
- 10,000 images ≈ 1-2 GB cache
- Can be cleared and regenerated as needed

## Error Handling

### Generation Failures
- File not found: Returns error status
- Unsupported format: Returns None/error
- Processing error: Logs exception, returns error status
- Database error: Rolls back transaction, logs error

### Recovery
- Use the Admin panel "Regenerate All" button for bulk recovery
- Manual API calls can regenerate individual images
- Clear cache and regenerate for full recovery

## Frontend Integration

### UI Controls
1. **Admin Panel** (`frontend/src/pages/Admin.jsx`):
   - Display thumbnail cache statistics
   - "Clear Cache" button (clears all files and database paths)
   - "Regenerate All" button (regenerates all thumbnails)
   - Disabled during operations (loading state)

2. **Search Results** (`frontend/src/pages/Search.jsx`):
   - Adjustable thumbnail size slider (user preference stored in localStorage)
   - Request URL: `/api/images/{id}/thumbnail`
   - Fallback: returns 404 if thumbnail unavailable

### API Client Methods
- `fetchImageThumbnail(id)`: Get thumbnail URL
- `fetchThumbnailStats()`: Get cache statistics
- `clearThumbnailCache()`: Trigger cache clear
- `regenerateThumbnails()`: Trigger global regeneration
- `triggerBulkThumbnails(path)`: Trigger mount-point regeneration

## Typical Workflows

### Adding New Images
1. Images indexed via metadata extraction
2. Thumbnail generated on-demand when user views image
3. Or queued via bulk process after indexing

### Updating Image Format Handling
1. Modify `ThumbnailGenerator.load_source_image()` for format
2. Test with sample image
3. Clear cache and regenerate affected images
4. Update file type support in documentation

### Troubleshooting Missing Thumbnails
1. Check if file exists in cache directory
2. Verify database has correct `thumbnail_path`
3. Check image source file exists
4. Try manual regeneration: `POST /api/indexer/thumbnails/regenerate`
5. Use "Regenerate All" from the Admin panel for bulk recovery

## Future Enhancements (Phase 3)

- More efficient batch processing (chunked queuing vs individual tasks)
- Configurable thumbnail sizes for different UI contexts
- WebP format support for smaller file sizes
- Thumbnail preview for failed/error cases
- Caching of STF parameters for faster regeneration
