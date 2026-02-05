# AstroCat - Astronomical Image Database
## Design Document v1.1

---

## 1. Executive Summary

**AstroCat** is a modern, containerized, single-user web application designed to catalog, index, and retrieve approximately 40,000 astronomical image files. The system will extract and store plate-solving metadata, exposure information, and celestial object associations to enable powerful search capabilities across Messier and NGC catalogs.

### Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|----------|
| Image Subtype Assignment | **Manual** | User classifies images as Sub/Master/Deprecated |
| Thumbnail Generation | **On-Demand** | Saves storage, generates when first requested |
| Authentication | **None (Single-User)** | Local application, no multi-user support needed |
| Image Locations | **Multiple Mount Points** | Images spread across multiple drives/folders |
| Data Migration | **Fresh Ingestion** | All metadata extracted from source files |

---

## 2. System Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              Docker Host                                 │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌─────────────────┐    ┌─────────────────┐    ┌─────────────────────┐  │
│  │   Frontend      │    │   Backend API   │    │     PostgreSQL      │  │
│  │   (React/Vite)  │◄──►│   (FastAPI)     │◄──►│     + PostGIS       │  │
│  │   Port: 8090    │    │   Port: 8089    │    │     Port: 8088      │  │
│  └─────────────────┘    └────────┬────────┘    └─────────────────────┘  │
│                                  │                                       │
│                                  ▼                                       │
│                         ┌─────────────────┐                             │
│                         │  Image Storage  │                             │
│                         │ (Docker Volume) │                             │
│                         │   /images       │                             │
│                         └─────────────────┘                             │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Technology Stack

### 3.1 Frontend
| Component | Technology | Rationale |
|-----------|------------|-----------|
| Framework | **React 18** with **Vite** | Fast development, excellent ecosystem |
| Styling | **Vanilla CSS** with CSS Variables | Full control, no build dependencies |
| State Management | **React Query (TanStack Query)** | Server state caching, automatic refetching |
| Routing | **React Router v6** | Standard routing solution |
| UI Components | Custom + **Recharts** for visualizations | Lightweight, customizable |

### 3.2 Backend
| Component | Technology | Rationale |
|-----------|------------|-----------|
| Framework | **FastAPI** (Python 3.11+) | Async support, automatic OpenAPI docs, type hints |
| FITS Processing | **Astropy** | Industry standard for astronomical data |
| Image Metadata | **Pillow**, **exifread** | CR2/JPG metadata extraction |
| INI Parsing | **configparser** | Native Python, handles sidecar files |
| ORM | **SQLAlchemy 2.0** with **asyncpg** | Async database operations |
| Migrations | **Alembic** | Schema version control |
| Task Queue | **Celery** with **Redis** | Background image indexing |

### 3.3 Database
| Component | Technology | Rationale |
|-----------|------------|-----------|
| Primary Database | **PostgreSQL 16** | Robust, excellent spatial support |
| Spatial Extension | **PostGIS** | Spherical geometry for RA/DEC queries |
| Full-Text Search | PostgreSQL native FTS | Integrated, no additional service |

### 3.4 Infrastructure
| Component | Technology | Rationale |
|-----------|------------|-----------|
| Containerization | **Docker** + **Docker Compose** | Full stack orchestration |
| Reverse Proxy | **Nginx** | Static file serving, API routing |
| Image Storage | **Docker Volume Mount** | Direct access to host filesystem |

---

## 4. Data Model

### 4.1 Entity Relationship Diagram

```
┌───────────────────────────┐       ┌─────────────────────────────────┐
│      messier_catalog      │       │         ngc_catalog             │
├───────────────────────────┤       ├─────────────────────────────────┤
│ id: INTEGER (PK)          │       │ id: INTEGER (PK)                │
│ designation: VARCHAR(10)  │       │ designation: VARCHAR(20)        │
│ common_name: VARCHAR(100) │       │ common_name: VARCHAR(100)       │
│ object_type: VARCHAR(50)  │       │ object_type: VARCHAR(50)        │
│ ra_degrees: DOUBLE        │       │ ra_degrees: DOUBLE              │
│ dec_degrees: DOUBLE       │       │ dec_degrees: DOUBLE             │
│ angular_size: DOUBLE      │       │ angular_size: DOUBLE            │
│ constellation: VARCHAR(50)│       │ constellation: VARCHAR(50)      │
│ magnitude: DOUBLE         │       │ magnitude: DOUBLE               │
│ location: GEOGRAPHY(POINT)│       │ location: GEOGRAPHY(POINT)      │
└───────────────────────────┘       └─────────────────────────────────┘
            │                                       │
            │                                       │
            ▼                                       ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                           image_catalog_matches                          │
├─────────────────────────────────────────────────────────────────────────┤
│ id: INTEGER (PK)                                                         │
│ image_id: INTEGER (FK -> images.id)                                      │
│ catalog_type: ENUM('MESSIER', 'NGC')                                     │
│ catalog_id: INTEGER                                                      │
│ UNIQUE(image_id, catalog_type, catalog_id)                               │
└─────────────────────────────────────────────────────────────────────────┘
                                    ▲
                                    │
┌─────────────────────────────────────────────────────────────────────────┐
│                               images                                     │
├─────────────────────────────────────────────────────────────────────────┤
│ id: INTEGER (PK)                                                         │
│ file_path: VARCHAR(500) UNIQUE                                           │
│ file_name: VARCHAR(255)                                                  │
│ file_format: ENUM('FITS', 'CR2', 'JPG', 'JPEG', 'TIFF', 'PNG')          │
│ file_size_bytes: BIGINT                                                  │
│ file_hash: VARCHAR(64)  -- SHA-256 for deduplication                     │
│                                                                          │
│ -- Plate Solve Data                                                      │
│ is_plate_solved: BOOLEAN DEFAULT FALSE                                   │
│ ra_center_degrees: DOUBLE                                                │
│ dec_center_degrees: DOUBLE                                               │
│ field_width_degrees: DOUBLE                                              │
│ field_height_degrees: DOUBLE                                             │
│ rotation_angle_degrees: DOUBLE                                           │
│ pixel_scale_arcsec: DOUBLE                                               │
│ center_location: GEOGRAPHY(POINT)  -- PostGIS spherical point           │
│ field_boundary: GEOGRAPHY(POLYGON) -- PostGIS spherical polygon          │
│                                                                          │
│ -- Image Classification                                                  │
│ subtype: ENUM('SUB_FRAME', 'INTEGRATION_MASTER', 'INTEGRATION_DEPRECATED')│
│                                                                          │
│ -- Exposure Data                                                         │
│ exposure_time_seconds: DOUBLE                                            │
│ iso_speed: INTEGER                                                       │
│ gain: DOUBLE                                                             │
│ temperature_celsius: DOUBLE                                              │
│                                                                          │
│ -- Capture Metadata                                                      │
│ capture_date: TIMESTAMP WITH TIME ZONE                                   │
│ camera: VARCHAR(100)                                                     │
│ telescope: VARCHAR(100)                                                  │
│ filter_name: VARCHAR(50)                                                 │
│                                                                          │
│ -- Thumbnail                                                             │
│ thumbnail_path: VARCHAR(500)                                             │
│                                                                          │
│ -- System Fields                                                         │
│ created_at: TIMESTAMP WITH TIME ZONE DEFAULT NOW()                       │
│ updated_at: TIMESTAMP WITH TIME ZONE                                     │
│ indexed_at: TIMESTAMP WITH TIME ZONE                                     │
│ metadata_source: ENUM('EMBEDDED', 'SIDECAR', 'MANUAL')                   │
└─────────────────────────────────────────────────────────────────────────┘
```

### 4.2 Indexing Strategy

| Index Name | Columns | Type | Purpose |
|------------|---------|------|---------|
| `idx_images_subtype` | `subtype` | B-Tree | Filter by image subtype |
| `idx_images_plate_solved` | `is_plate_solved` | B-Tree | Quick filter for solved images |
| `idx_images_exposure` | `exposure_time_seconds` | B-Tree | Exposure time queries |
| `idx_images_center_location` | `center_location` | GIST (Spherical) | Spatial RA/DEC queries |
| `idx_images_field_boundary` | `field_boundary` | GIST (Spherical) | Containment queries |
| `idx_images_capture_date` | `capture_date` | B-Tree | Date-based filtering |
| `idx_messier_location` | `location` | GIST (Spherical) | Catalog spatial lookups |
| `idx_ngc_location` | `location` | GIST (Spherical) | Catalog spatial lookups |
| `idx_catalog_matches_image` | `image_id` | B-Tree | Fast join operations |

---

## 5. API Design

### 5.1 RESTful Endpoints

#### Images
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/images` | List/search images with filters |
| `GET` | `/api/images/{id}` | Get image details |
| `PATCH` | `/api/images/{id}` | Update image metadata (e.g., subtype) |
| `GET` | `/api/images/{id}/thumbnail` | Get image thumbnail |
| `GET` | `/api/images/{id}/preview` | Get full-size preview |
| `GET` | `/api/images/{id}/objects` | Get matched catalog objects |

#### Search
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/search/messier/{designation}` | Images containing Messier object |
| `GET` | `/api/search/ngc/{designation}` | Images containing NGC object |
| `GET` | `/api/search/coordinates` | Images by RA/DEC with radius |
| `GET` | `/api/search/exposure` | Images by exposure time range |

#### Catalogs
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/catalogs/messier` | List all Messier objects |
| `GET` | `/api/catalogs/ngc` | List NGC objects (paginated) |
| `GET` | `/api/catalogs/messier/{designation}` | Messier object details + images |
| `GET` | `/api/catalogs/ngc/{designation}` | NGC object details + images |

#### Statistics
| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/stats/overview` | Total images, exposure time, coverage |
| `GET` | `/api/stats/exposure` | Exposure time breakdown by subtype |
| `GET` | `/api/stats/coverage` | Sky coverage statistics |

#### Indexing
| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/indexer/scan` | Trigger full filesystem scan |
| `GET` | `/api/indexer/status` | Get indexing job status |
| `POST` | `/api/indexer/reindex/{id}` | Re-index specific image |

### 5.2 Query Parameters for Image Search

```
GET /api/images?
  subtype=SUB_FRAME|INTEGRATION_MASTER|INTEGRATION_DEPRECATED
  &messier=M31,M42
  &ngc=NGC7000,NGC2024
  &ra_center=83.633
  &dec_center=22.0145
  &radius_degrees=5.0
  &exposure_min=60
  &exposure_max=300
  &date_from=2024-01-01
  &date_to=2024-12-31
  &format=FITS,CR2
  &sort_by=capture_date|exposure_time|file_name
  &sort_order=asc|desc
  &page=1
  &page_size=50
```

---

## 6. Metadata Extraction Pipeline

### 6.1 Processing Flow

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  File Scanner   │────►│  Format Router  │────►│ Metadata        │
│  (Directory     │     │  (FITS/CR2/JPG) │     │ Extractor       │
│   Watcher)      │     │                 │     │                 │
└─────────────────┘     └─────────────────┘     └────────┬────────┘
                                                         │
         ┌───────────────────────────────────────────────┘
         │
         ▼
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│ Sidecar INI     │────►│  Catalog        │────►│  Database       │
│ Parser          │     │  Matcher        │     │  Writer         │
│ (if needed)     │     │  (Messier/NGC)  │     │                 │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                                                         │
                                                         ▼
                                                ┌─────────────────┐
                                                │  Thumbnail      │
                                                │  Generator      │
                                                └─────────────────┘
```

### 6.2 Metadata Sources by Format

| Format | Plate Solve Source | Exposure Source |
|--------|-------------------|-----------------|
| **FITS** | FITS Header (WCS Keywords) | FITS Header (`EXPTIME`, `EXPOSURE`) |
| **CR2** | Sidecar `.ini` file | EXIF (`ExposureTime`) |
| **JPG/JPEG** | Sidecar `.ini` file | EXIF (`ExposureTime`) |
| **TIFF** | Sidecar `.ini` file | EXIF or TIFF tags |
| **PNG** | Sidecar `.ini` file | XMP metadata if available |

### 6.3 FITS Header Keywords to Extract

```python
PLATE_SOLVE_KEYWORDS = {
    'CRVAL1': 'ra_center_degrees',      # RA at reference pixel
    'CRVAL2': 'dec_center_degrees',     # DEC at reference pixel
    'CD1_1', 'CD1_2', 'CD2_1', 'CD2_2': 'rotation/scale matrix',
    'CDELT1', 'CDELT2': 'pixel_scale',
    'NAXIS1', 'NAXIS2': 'image dimensions',
    'CTYPE1', 'CTYPE2': 'coordinate type',
}

EXPOSURE_KEYWORDS = {
    'EXPTIME': 'exposure_time_seconds',
    'EXPOSURE': 'exposure_time_seconds',
    'DATE-OBS': 'capture_date',
    'INSTRUME': 'camera',
    'TELESCOP': 'telescope',
    'FILTER': 'filter_name',
    'CCD-TEMP': 'temperature_celsius',
    'GAIN': 'gain',
    'ISO': 'iso_speed',
}
```

### 6.4 Sidecar INI File Format (Astrometry.net)

The sidecar `.ini` files are generated by [Astrometry.net](https://nova.astrometry.net/) and contain two sections:

```ini
[Astrometry]
job_id = 21
sub_id = 21
url = https://nova.astrometry.net/status/21
image_url = 
thumbnail_url = https://nova.astrometry.net/user_images/21
image_id = 
user_image_id = 21
ra = 303.9577058001051           # Center RA in degrees
dec = 38.77656479262137          # Center DEC in degrees
radius = 3.940063920439382       # Field radius in degrees
pixscale = 4.5532379787659805    # Arcsec per pixel
orientation = 337.4377911675575  # Position angle in degrees
width_arcmin =                   # May be empty
height_arcmin =                  # May be empty
parity = 1.0                     # Image parity (1.0 or -1.0)

[WCS]
crpix1 = 2154.93103027           # Reference pixel X
crpix2 = 3355.39208984           # Reference pixel Y
crval1 = 303.598353099           # RA at reference pixel
crval2 = 36.6649223091           # DEC at reference pixel
cd1_1 = -0.0011673371126         # CD matrix element
cd1_2 = -0.000486284103024       # CD matrix element
cd2_1 = 0.000484280256476        # CD matrix element
cd2_2 = -0.00116863561379        # CD matrix element
```

**Key Fields Mapping:**

| INI Field | Database Column | Notes |
|-----------|-----------------|-------|
| `[Astrometry].ra` | `ra_center_degrees` | Primary center coordinate |
| `[Astrometry].dec` | `dec_center_degrees` | Primary center coordinate |
| `[Astrometry].radius` | Used to calculate `field_width/height` | Field radius in degrees |
| `[Astrometry].pixscale` | `pixel_scale_arcsec` | Plate scale |
| `[Astrometry].orientation` | `rotation_angle_degrees` | Position angle |
| `[WCS].*` | Used for precise polygon calculation | Full WCS solution |

**Field Boundary Calculation:**

Using the WCS CD matrix, we can calculate the precise image boundary polygon:
1. Get image dimensions from the original file (NAXIS1, NAXIS2 for FITS, or image size for others)
2. Transform corner pixels (0,0), (W,0), (W,H), (0,H) to celestial coordinates using the CD matrix
3. Create a spherical polygon from the 4 corner coordinates

---

## 7. Catalog Object Matching

### 7.1 Matching Algorithm

1. **Extract image field boundary** from plate solve data (RA, DEC, field dimensions, rotation)
2. **Convert to spherical polygon** using PostGIS `GEOGRAPHY` type
3. **Query catalogs** for objects whose locations fall within the image boundary:

```sql
SELECT m.* 
FROM messier_catalog m
WHERE ST_Contains(
    image.field_boundary::geography,
    m.location::geography
);
```

4. **Store matches** in `image_catalog_matches` junction table

### 7.2 Pre-loaded Catalog Data

**Messier Catalog**: 110 objects (static, shipped with application)
**NGC Catalog**: ~7,840 objects (static, shipped with application)

Catalog data will be stored as seed SQL files and loaded during database initialization.

---

## 8. Frontend Design

### 8.1 Page Structure

```
┌─────────────────────────────────────────────────────────────────┐
│                         Navigation Bar                           │
│  [Logo]  [Dashboard]  [Search]  [Catalogs]  [Settings]          │
└─────────────────────────────────────────────────────────────────┘

Pages:
├── /                    Dashboard (overview stats, recent images)
├── /search              Advanced search with filters
├── /images/:id          Image detail view
├── /catalogs            Catalog browser (Messier/NGC tabs)
├── /catalogs/messier/:m Image gallery for Messier object
├── /catalogs/ngc/:ngc   Image gallery for NGC object
├── /settings            Indexer controls, preferences
└── /stats               Exposure statistics, coverage map
```

### 8.2 Key UI Components

| Component | Description |
|-----------|-------------|
| **ImageGrid** | Responsive masonry grid with lazy-loaded thumbnails |
| **ImageCard** | Thumbnail + overlay with key metadata |
| **ImageViewer** | Full-screen view with pan/zoom, metadata panel |
| **SearchFilters** | Collapsible sidebar with all filter options |
| **CoordinateInput** | RA/DEC input with format validation (HMS/DMS or degrees) |
| **ExposureChart** | Recharts visualization of cumulative exposure |
| **SkyCoverageMap** | Interactive celestial map showing image coverage |
| **CatalogBrowser** | Tabbed view of Messier/NGC with search |
| **IndexerStatus** | Real-time progress of background indexing |

### 8.3 Design System

```css
:root {
  /* Color Palette - Deep Space Theme */
  --color-background: #0a0e17;
  --color-surface: #141b2d;
  --color-surface-elevated: #1a2435;
  --color-primary: #5b8dee;
  --color-primary-glow: rgba(91, 141, 238, 0.3);
  --color-secondary: #8b5cf6;
  --color-accent: #22d3ee;
  --color-text-primary: #f1f5f9;
  --color-text-secondary: #94a3b8;
  --color-border: #2d3a4f;
  --color-success: #22c55e;
  --color-warning: #eab308;
  --color-error: #ef4444;
  
  /* Typography */
  --font-family: 'Inter', system-ui, sans-serif;
  --font-mono: 'JetBrains Mono', monospace;
  
  /* Spacing */
  --spacing-xs: 4px;
  --spacing-sm: 8px;
  --spacing-md: 16px;
  --spacing-lg: 24px;
  --spacing-xl: 32px;
  
  /* Borders & Shadows */
  --border-radius: 8px;
  --shadow-glow: 0 0 20px var(--color-primary-glow);
}
```

---

## 9. Docker Configuration

### 9.1 Container Architecture

```yaml
# docker-compose.yml structure
services:
  frontend:
    build: ./frontend
    ports: ["8090:80"]
    depends_on: [backend]
    
  backend:
    build: ./backend
    ports: ["8089:8089"]
    volumes:
      # Multiple image mount points (configured via .env)
      - ${IMAGE_PATH_1}:/images/mount1:ro
      - ${IMAGE_PATH_2}:/images/mount2:ro
      - ${IMAGE_PATH_3}:/images/mount3:ro
      # Thumbnail cache
      - thumbnails:/app/thumbnails
    depends_on: [db, redis]
    environment:
      - DATABASE_URL=postgresql+asyncpg://...
      - REDIS_URL=redis://redis:6379
      - IMAGE_MOUNT_PATHS=/images/mount1,/images/mount2,/images/mount3
      
  db:
    image: postgis/postgis:16-3.4
    ports: ["8088:5432"]
    volumes:
      - postgres_data:/var/lib/postgresql/data
    environment:
      - POSTGRES_DB=AstroCat
      - POSTGRES_USER=AstroCat
      - POSTGRES_PASSWORD=${DB_PASSWORD}
      
  redis:
    image: redis:7-alpine
    volumes:
      - redis_data:/data
      
  celery_worker:
    build: ./backend
    command: celery -A app.worker worker -l info
    volumes:
      - /path/to/images:/images:ro
      - thumbnails:/app/thumbnails
    depends_on: [db, redis]

volumes:
  postgres_data:
  redis_data:
  thumbnails:
```

### 9.2 Multiple Mount Point Strategy

Since images are spread across multiple locations, the system supports configurable mount points:

**Configuration (`.env` file):**
```env
# Define up to 10 image source locations
IMAGE_PATH_1=D:\Astronomy\DeepSky
IMAGE_PATH_2=E:\Astrophotos\2024
IMAGE_PATH_3=F:\Archive\Galaxies
# Leave unused paths empty or remove them
IMAGE_PATH_4=
```

**Path Storage Strategy:**
- Database stores paths as: `mount1/subfolder/image.fits`
- The mount prefix (`mount1`, `mount2`, etc.) identifies which source location
- This allows images to be moved between host paths without database updates
- API translates stored paths to actual container paths at runtime

**Adding New Mount Points:**
1. Add new path to `.env` file
2. Update `docker-compose.yml` to include new volume
3. Restart containers
4. Trigger re-scan to discover new images

---

## 10. Indexing Strategy

### 10.1 Initial Bulk Indexing

1. **Scan Phase**: Walk directory tree, collect file metadata (path, size, hash)
2. **Deduplication**: Skip files already indexed (by hash comparison)
3. **Queue Phase**: Add new files to Celery task queue in batches
4. **Processing Phase**: Workers extract metadata in parallel (4-8 workers)
5. **Matching Phase**: After metadata extraction, run catalog matching

### 10.2 Performance Considerations

| Metric | Target |
|--------|--------|
| Initial scan (40K files) | < 5 minutes |
| Metadata extraction rate | ~50 files/second/worker |
| Full initial index | < 15 minutes (4 workers) |
| Incremental re-scan | < 1 minute |

### 10.3 On-Demand Thumbnail Generation

Thumbnails are generated lazily when first requested, not during indexing:

**Workflow:**
1. Frontend requests thumbnail via `/api/images/{id}/thumbnail`
2. Backend checks if cached thumbnail exists
3. If not cached: generate, save to cache, return
4. If cached: return immediately

**Specifications:**
- **Size**: 400px longest edge
- **Format**: WebP (quality 80)
- **FITS Handling**: Auto-stretch using ZScale algorithm (Astropy)
- **Cache Path**: `/app/thumbnails/{hash[0:2]}/{hash[2:4]}/{hash}.webp`
- **Cache Invalidation**: Manual clear via settings, or automatic on re-index

**Benefits:**
- No upfront storage cost for 40K thumbnails
- Faster initial indexing (metadata only)
- Only frequently-viewed images get thumbnails cached

---

## 11. Search Implementation

### 11.1 Spatial Queries with PostGIS

**Point-in-Polygon (Find images containing a point):**
```sql
SELECT i.* FROM images i
WHERE ST_Contains(
    i.field_boundary,
    ST_SetSRID(ST_MakePoint(:ra, :dec), 4326)::geography
);
```

**Radius Search (Find images near coordinates):**
```sql
SELECT i.*, 
       ST_Distance(
           i.center_location,
           ST_SetSRID(ST_MakePoint(:ra, :dec), 4326)::geography
       ) / 3600 as distance_degrees
FROM images i
WHERE ST_DWithin(
    i.center_location,
    ST_SetSRID(ST_MakePoint(:ra, :dec), 4326)::geography,
    :radius_degrees * 3600  -- Convert to meters (approx)
)
ORDER BY distance_degrees;
```

### 11.2 Catalog Object Search

```sql
-- Find all images containing M31
SELECT DISTINCT i.*
FROM images i
JOIN image_catalog_matches icm ON i.id = icm.image_id
JOIN messier_catalog m ON icm.catalog_id = m.id 
    AND icm.catalog_type = 'MESSIER'
WHERE m.designation = 'M31';
```

### 11.3 Exposure Time Aggregation

```sql
-- Total exposure by subtype
SELECT 
    subtype,
    COUNT(*) as image_count,
    SUM(exposure_time_seconds) as total_exposure,
    SUM(exposure_time_seconds) / 3600 as total_hours
FROM images
GROUP BY subtype;

-- Cumulative exposure for specific object
SELECT 
    SUM(i.exposure_time_seconds) as total_exposure
FROM images i
JOIN image_catalog_matches icm ON i.id = icm.image_id
WHERE icm.catalog_type = 'MESSIER' 
  AND icm.catalog_id = (SELECT id FROM messier_catalog WHERE designation = 'M42');
```

---

## 12. Project Directory Structure

```
AstroCat/
├── docker-compose.yml
├── docker-compose.dev.yml
├── .env.example
├── README.md
│
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── alembic.ini
│   ├── alembic/
│   │   └── versions/
│   └── app/
│       ├── __init__.py
│       ├── main.py                 # FastAPI app entry
│       ├── config.py               # Settings management
│       ├── database.py             # DB connection
│       ├── worker.py               # Celery app
│       │
│       ├── models/                 # SQLAlchemy models
│       │   ├── image.py
│       │   ├── catalog.py
│       │   └── matches.py
│       │
│       ├── schemas/                # Pydantic schemas
│       │   ├── image.py
│       │   ├── catalog.py
│       │   └── search.py
│       │
│       ├── api/                    # Route handlers
│       │   ├── images.py
│       │   ├── search.py
│       │   ├── catalogs.py
│       │   ├── stats.py
│       │   └── indexer.py
│       │
│       ├── services/               # Business logic
│       │   ├── indexer.py
│       │   ├── metadata_extractor.py
│       │   ├── catalog_matcher.py
│       │   └── thumbnail_generator.py
│       │
│       ├── extractors/             # Format-specific extractors
│       │   ├── base.py
│       │   ├── fits_extractor.py
│       │   ├── raw_extractor.py    # CR2, etc.
│       │   ├── jpeg_extractor.py
│       │   └── ini_parser.py
│       │
│       └── data/                   # Static catalog data
│           ├── messier_catalog.json
│           └── ngc_catalog.json
│
├── frontend/
│   ├── Dockerfile
│   ├── nginx.conf
│   ├── package.json
│   ├── vite.config.js
│   ├── index.html
│   │
│   ├── public/
│   │   └── favicon.ico
│   │
│   └── src/
│       ├── main.jsx
│       ├── App.jsx
│       ├── index.css               # Design system
│       │
│       ├── components/
│       │   ├── layout/
│       │   ├── images/
│       │   ├── search/
│       │   ├── catalogs/
│       │   └── common/
│       │
│       ├── pages/
│       │   ├── Dashboard.jsx
│       │   ├── Search.jsx
│       │   ├── ImageDetail.jsx
│       │   ├── Catalogs.jsx
│       │   ├── Stats.jsx
│       │   └── Settings.jsx
│       │
│       ├── hooks/
│       │   ├── useImages.js
│       │   ├── useCatalogs.js
│       │   └── useSearch.js
│       │
│       ├── api/
│       │   └── client.js           # API client wrapper
│       │
│       └── utils/
│           ├── coordinates.js      # RA/DEC formatting
│           └── formatters.js
│
└── scripts/
    ├── setup.sh                    # Initial setup
    ├── seed_catalogs.py            # Load Messier/NGC data
    └── backup.sh                   # Database backup
```

---

## 13. Development Phases

### Phase 1: Foundation (Week 1-2)
- [ ] Set up Docker Compose infrastructure
- [ ] Create PostgreSQL + PostGIS database schema
- [ ] Implement basic FastAPI backend structure
- [ ] Create Alembic migrations
- [ ] Seed Messier and NGC catalog data

### Phase 2: Metadata Extraction (Week 2-3)
- [ ] Implement FITS metadata extractor
- [ ] Implement CR2/JPG EXIF extractor
- [ ] Implement INI sidecar parser
- [ ] Create Celery background worker
- [ ] Build directory scanner service
- [ ] Implement thumbnail generator

### Phase 3: Search & Matching (Week 3-4)
- [ ] Implement PostGIS spatial queries
- [ ] Create catalog matching algorithm
- [ ] Build search API endpoints
- [ ] Implement aggregation queries

### Phase 4: Frontend (Week 4-6)
- [ ] Set up React + Vite project
- [ ] Create design system and components
- [ ] Build Dashboard page
- [ ] Build Search page with filters
- [ ] Build Image detail view
- [ ] Build Catalog browser
- [ ] Build Statistics page

### Phase 5: Polish & Deployment (Week 6-7)
- [ ] Add error handling and validation
- [ ] Performance optimization
- [ ] Documentation
- [ ] Production Docker configuration
- [ ] User testing and refinement

---

## 14. Future Enhancements (Out of Scope v1)

- **Stacking Queue**: Integration with stacking software for automated processing
- **Cloud Backup**: S3/Azure Blob storage for image backup
- **Mobile App**: Responsive PWA or native companion app
- **Astrometry.net Integration**: Auto plate-solve unsolved images
- **Equipment Tracking**: Telescope, camera, mount metadata management
- **Session Planning**: Integration with imaging session planners
- **Public Sharing**: Shareable galleries and direct links

---

## 15. Confirmed Design Decisions

| # | Question | Decision |
|---|----------|----------|
| 1 | INI Sidecar Format | ✅ Astrometry.net format with `[Astrometry]` and `[WCS]` sections |
| 2 | Image Subtype Assignment | ✅ **Manual** - User assigns via UI |
| 3 | Thumbnail Generation | ✅ **On-Demand** - Generated when first viewed |
| 4 | Authentication | ✅ **None** - Single-user local application |
| 5 | Image Locations | ✅ **Multiple Mount Points** - Configurable via `.env` |
| 6 | Data Migration | ✅ **Fresh Ingestion** - All metadata from source files |

---

## 16. Subtype Management

Since subtypes are manually assigned, the UI will provide:

### Bulk Assignment
- Select multiple images in grid view
- Right-click context menu or toolbar button
- Choose subtype from dropdown
- Batch update via single API call

### Single Image Assignment
- Dropdown selector on image detail page
- Changes saved immediately

### Default Behavior
- New images default to `NULL` (unclassified)
- Filter option to show "Unclassified" images for easy triage
- Dashboard widget showing count of unclassified images

---

*Document Version: 1.1*  
*Created: 2026-01-22*  
*Last Updated: 2026-01-22*  
*Status: Ready for Implementation*
