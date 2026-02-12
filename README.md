# 🌌 AstroCat

**Astronomical Image Database** - A modern web application for cataloging, indexing, and searching astronomical images.

![AstroCat](https://img.shields.io/badge/version-0.1.0-blue.svg)
![License](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)

## ✨ Features

- **Image Indexing**: Automatically extract metadata from FITS, XISF, RAW (CR2/CR3/NEF/ARW/DNG), and standard image formats
- **Blind Solve**: Automatically attempt blind plate solving if initial solve fails
- **Catalog Matching**: Match images to Messier, NGC, and Named Star catalogs by coordinates
- **Advanced Search**: Search by object name, filename, coordinates, exposure time, and more
- **Keyboard Navigation**: Use arrow keys to navigate between images in detail view
- **Thumbnail Generation**: On-demand thumbnail generation with adjustable size slider
- **Modern UI**: Responsive React frontend with dark theme

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         Frontend (React)                         │
│                     Vite + React Query + Router                  │
├─────────────────────────────────────────────────────────────────┤
│                         REST API (FastAPI)                       │
├─────────────────────────────────────────────────────────────────┤
│      PostgreSQL + PostGIS      │      Redis + Celery             │
│        (Spatial Queries)       │    (Background Tasks)           │
└─────────────────────────────────────────────────────────────────┘
```

## 🚀 Quick Start

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose (v2.0+)
  - **Windows**: [Docker Desktop](https://www.docker.com/products/docker-desktop/) with WSL 2 backend is recommended.
- (Optional) Python 3.12+ for helper scripts

There are two ways to get AstroCat up and running: using pre-built images from Docker Hub (recommended for most users) or building from source.

### Option 1: Pull from Docker Hub (Fastest)

This method uses pre-built production images and doesn't require cloning the entire source code.

#### 🪟 Windows (PowerShell)
1. **Download required files**:
   ```powershell
   # Create a directory for your installation
   mkdir AstroCat; cd AstroCat

   # Download the compose file
   Invoke-WebRequest -Uri "https://raw.githubusercontent.com/james474789/AstroCat/main/docker-compose-example.yml" -OutFile "docker-compose.yml"

   # Download the environment template
   Invoke-WebRequest -Uri "https://raw.githubusercontent.com/james474789/AstroCat/main/.env.example" -OutFile ".env"

   # Download the backup and restore scripts
   mkdir scripts
   Invoke-WebRequest -Uri "https://raw.githubusercontent.com/james474789/AstroCat/main/scripts/backup_db.ps1" -OutFile "scripts/backup_db.ps1"
   Invoke-WebRequest -Uri "https://raw.githubusercontent.com/james474789/AstroCat/main/scripts/restore_backup.ps1" -OutFile "scripts/restore_backup.ps1"
   ```

#### 🐧 Linux / 🍎 macOS
1. **Download required files**:
   ```bash
   # Create a directory for your installation
   mkdir AstroCat; cd AstroCat

   # Download the compose file
   curl -L https://raw.githubusercontent.com/james474789/AstroCat/main/docker-compose-example.yml -o docker-compose.yml

   # Download the environment template
   curl -L https://raw.githubusercontent.com/james474789/AstroCat/main/.env.example -o .env

   # Download the backup and restore scripts
   mkdir -p scripts
   curl -L https://raw.githubusercontent.com/james474789/AstroCat/main/scripts/backup_db.ps1 -o scripts/backup_db.ps1
   curl -L https://raw.githubusercontent.com/james474789/AstroCat/main/scripts/restore_backup.ps1 -o scripts/restore_backup.ps1
   ```

2. **Configure Environment**:
   - Generate a `SECRET_KEY`: `python -c "import secrets; print(secrets.token_hex(32))"`
   - Edit `.env` and set your `IMAGE_PATHS`, `NAS_USERNAME`, `NAS_PASSWORD`, etc.

3. **Start the Application**:
   ```bash
   docker compose up -d
   ```

---

### Option 2: Build from Source

Recommended if you want to modify the code or contribute.

#### 🪟 Windows (PowerShell)
1. **Clone and Configure**:
   ```powershell
   git clone https://github.com/james474789/AstroCat.git
   cd AstroCat
   copy .env.example .env
   ```

#### 🐧 Linux / 🍎 macOS
1. **Clone and Configure**:
   ```bash
   git clone https://github.com/james474789/AstroCat.git
   cd AstroCat
   cp .env.example .env
   ```

2. **Configure Environment**:
   - Generate a `SECRET_KEY` as shown above.
   - Edit `.env` with your settings.

3. **Start with Docker Compose**:
   ```bash
   # Production mode (builds local images)
   docker compose up -d

   # Development mode (with hot reload)
   docker compose -f docker-compose.yml -f docker-compose.dev.yml up
   ```

### 3. Database Initialization (Automatic)

AstroCat handles database migrations and astronomical catalog seeding automatically on first run within the `backend` service. You don't need to run any manual scripts.

The automatic process populates:
- **Messier Catalog** - 110 deep-sky objects
- **NGC Catalog** - 7,840+ objects from the New General Catalogue
- **Named Stars** - Common star names for reference

#### 🔄 Manual Reseed (Optional)
If you ever need to manually force a reseed or refresh the catalogs, you can use:

**Windows (PowerShell)**:
```powershell
# If running from source:
.\rebuild_and_seed.ps1

# If running from Docker Hub:
docker compose exec backend python -m app.data.seed
docker compose exec backend python -m app.scripts.seed_named_stars
```

**Linux / macOS**:
```bash
docker compose exec backend python -m app.data.seed
docker compose exec backend python -m app.scripts.seed_named_stars
```

### 4. Access the Application

- **Frontend**: http://localhost:8090 (Default, configurable via `FRONTEND_PORT`)
- **API Docs**: http://localhost:8089/api/docs (Default, configurable via `BACKEND_PORT`)
- **Health Check**: http://localhost:8089/api/health

## 📁 Project Structure

```
AstroCat/
├── backend/               # FastAPI application
│   ├── app/
│   │   ├── api/           # REST API endpoints
│   │   ├── data/          # Seed data and catalogs
│   │   ├── extractors/    # Metadata extraction (FITS, EXIF)
│   │   ├── models/        # SQLAlchemy models
│   │   ├── schemas/       # Pydantic schemas
│   │   ├── services/      # Business logic
│   │   ├── tasks/         # Celery background tasks
│   │   └── worker.py      # Celery worker
│   ├── alembic/           # Database migrations
│   └── Dockerfile
├── frontend/              # React application
│   ├── src/               # JSX components and logic
│   ├── Dockerfile
│   └── package.json
├── library/               # Persistent data and logs
│   ├── backups/           # Database backups
│   ├── logs/              # Application logs
│   ├── temp/              # Temporary processing files
│   └── thumbnails/        # Generated thumbnails
├── scripts/               # Utility scripts
│   ├── backup_db.ps1      # Manual backup script
│   └── restore_backup.ps1     # Database restore script
├── docker-compose.yml     # Core compose file (Build from Source)
├── docker-compose-example.yml # Template for Docker Hub installation
└── .env.example           # Environment template
```

## 🔧 Development

### Backend (FastAPI)

```bash
cd backend

# Create virtual environment
python -m venv venv

# Activate virtual environment
source venv/bin/activate  # Linux / macOS
.\venv\Scripts\Activate.ps1   # Windows (PowerShell)

# Install dependencies
pip install -r requirements.txt

# Run development server (using default port)
uvicorn app.main:app --reload --port 8089
```

### Frontend (React/Vite)

```bash
cd frontend

# Install dependencies
npm install

# Run development server
npm run dev
```

### Database Migrations

```bash
cd backend

# Create a new migration
alembic revision --autogenerate -m "Description"

# Apply migrations
alembic upgrade head
```

## 📚 Documentation

Detailed documentation is available in the `docs/` directory:

- [**Architecture Overview**](docs/core/ARCHITECTURE.md): System design and technology stack.
- [**Backend Guide**](docs/development/BACKEND.md): FastAPI structure, extraction pipeline, and background tasks.
- [**Frontend Guide**](docs/development/FRONTEND.md): React components, design system, and state management.
- [**Database Schema**](docs/core/DATABASE_SCHEMA.md): Detailed table definitions and spatial query logic.
- [**Development Guide**](docs/development/DEVELOPMENT_GUIDE.md): Setup instructions and common development workflows.
- [**Backup & Restore**](docs/infrastructure/BACKUP_RESTORE.md): Database backup and recovery procedures.
- [**Security Audit**](docs/infrastructure/SECURITY_AUDIT.md): Findings from recent security audits.

## 🗄️ Database Schema

### Images Table
- File information (path, format, size, hash)
- WCS coordinates (RA, DEC, field radius)
- PostGIS geography columns for spatial queries
- Exposure metadata (time, date, camera, telescope)
- Subtype classification (sub-frame, master, deprecated)

### Catalogs
- **Messier**: 110 deep-sky objects
- **NGC**: 7,840+ objects (New General Catalogue)
- **Named Stars**: Common star names and positions
- Spatial indexing for coordinate matching

## 🔍 API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/health` | Health check |
| `GET /api/images` | List images with filtering |
| `GET /api/images/{id}` | Get image details |
| `GET /api/images/{id}/thumbnail` | Get image thumbnail |
| `GET /api/images/export_csv` | Export images as CSV |
| `POST /api/search/coordinates` | Search by RA/DEC |
| `GET /api/search/messier/{designation}` | Search by Messier object |
| `GET /api/catalogs/messier` | List Messier catalog |
| `GET /api/catalogs/ngc` | List NGC catalog |
| `POST /api/indexer/scan` | Trigger directory scan |
| `GET /api/stats/overview` | Get statistics |
| `GET /api/stats/fits` | FITS-specific analytics |
| `GET /api/admin/stats` | Admin statistics dashboard |
| `GET /api/filesystem/list` | Browse filesystem directories |
| `GET /api/settings` | Application settings |

## 📊 Supported File Formats

| Format | Metadata Extraction | Thumbnail Generation |
|--------|--------------------|--------------------|
| FITS   | ✅ Full WCS support | ✅ |
| XISF   | ✅ PixInsight native | ✅ |
| CR2    | ✅ EXIF + sidecar  | ✅ |
| CR3    | ✅ EXIF + sidecar  | ✅ |
| ARW    | ✅ EXIF + sidecar  | ✅ |
| NEF    | ✅ EXIF + sidecar  | ✅ |
| DNG    | ✅ EXIF + sidecar  | ✅ |
| JPG    | ✅ EXIF           | ✅ |
| TIFF   | ✅ EXIF           | ✅ |
| PNG    | ✅ Basic          | ✅ |

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📄 License

This project is licensed under the GNU Affero General Public License v3.0 - see the [LICENSE](LICENSE) file for details.

---

*Built for astronomers who want to organize their imaging data.*
