# AstroCat Database Schema

AstroCat uses PostgreSQL 16 with the PostGIS extension to handle standard metadata and complex spatial data for astronomical coordinates.

## Tables

### 1. `images`
The central table storing all indexed image metadata.

| Column | Type | Description |
|--------|------|-------------|
| `id` | Integer | Primary key |
| `file_path` | String | Unique path to the file |
| `file_hash` | String | SHA-256 for deduplication |
| `is_plate_solved`| Boolean| True if WCS data was successfully extracted |
| `ra_center_degrees`| Double | Center Right Ascension |
| `dec_center_degrees`| Double | Center Declination |
| `center_location`| Geography(Point)| PostGIS point for spatial queries |
| `field_boundary` | Geography(Polygon)| Precise field of view on the sky |
| `exposure_time_seconds`| Double | Total duration of exposure |
| `subtype` | Enum | SUB_FRAME, INTEGRATION_MASTER, PLANETARY, etc. |
| `astrometry_status`| String | Plate solving status (SUBMITTED, SOLVED, etc) |

### 2. `messier_catalog`
Static catalog of the 110 Messier objects.

| Column | Type | Description |
|--------|------|-------------|
| `id` | Integer | Primary key |
| `designation` | String | e.g., "M31" |
| `common_name` | String | e.g., "Andromeda Galaxy" |
| `location` | Geography(Point)| Celestial coordinates |

### 3. `ngc_catalog`
Static catalog of ~7,840 New General Catalogue objects.

### 4. `image_catalog_matches`
Junction table linking images to the objects they contain.

| Column | Type | Description |
|--------|------|-------------|
| `id` | Integer | Primary key |
| `image_id` | Integer | Foreign key to `images.id` |
| `catalog_type` | Enum | MESSIER, NGC, IC, or NAMED_STAR |
| `catalog_designation`| String | Object name from catalog |
| `angular_separation_degrees`| Double | Distance from image center to object |

## Spatial Logic

AstroCat uses the PostGIS `GEOGRAPHY` type which treats the sky as a sphere. This is critical for:

- **ST_Contains**: Finding which objects fall inside an image's `field_boundary`.
- **ST_DWithin**: Searching for images within a certain angular radius of a point.
- **ST_Distance**: Calculating separation between objects.

### 5. `named_star_catalog`
Reference table for common star names and positions.

| Column | Type | Description |
|--------|------|-------------|
| `id` | Integer | Primary key |
| `name` | String | Common star name (e.g., "Vega", "Polaris") |
| `ra_degrees` | Float | J2000 Right Ascension |
| `dec_degrees` | Float | J2000 Declination |
| `location` | Geography(Point) | PostGIS Point for spatial indexing |
| `magnitude` | Float | Apparent visual magnitude |
| `constellation` | String | Constellation containing the star |

## Recent Schema Additions

The following columns were added to the `images` table for photography metadata:
- `rating` (Integer): User rating 0-5
- `aperture` (Float): F-number
- `focal_length` (Float): Focal length in mm
- `focal_length_35mm` (Float): 35mm equivalent
- `white_balance` (String): White balance mode
- `metering_mode` (String): Metering mode
- `flash_fired` (Boolean): Flash status
- `lens_model` (String): Lens identification

## Indexing Strategy

To maintain performance with large datasets, the following indexes are used:
- **Spatial Indexes (GIST)**: On `center_location` and `field_boundary`.
- **B-Tree Indexes**: On `file_path`, `file_hash`, and search criteria like `exposure_time_seconds` and `capture_date`.

