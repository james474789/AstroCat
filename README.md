# 🌌 AstroCat

**Astronomical Image Database** - A modern web application for cataloging, indexing, and searching astronomical images.

![AstroCat](https://img.shields.io/badge/version-0.1.0-blue.svg)
![License](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)

## ✨ Features

- **Image Indexing**: Automatically extract metadata from FITS, CR2, and JPEG files
- **Blind Solve**: Automatically attempt blind plate solving if initial solve fails
- **Catalog Matching**: Match images to Messier and NGC objects by coordinates
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

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose
- (Optional) Node.js 20+ and Python 3.12+ for local development

### 1. Clone and Configure

```bash
git clone https://github.com/james474789/AstroCat.git
cd AstroCat

# Copy environment template
cp .env.example .env

# Generate a secure SECRET_KEY
python -c "import secrets; print(secrets.token_hex(32))"
# Or using openssl:
# openssl rand -hex 32

# Edit .env with your settings
# - Replace SECRET_KEY with the generated value
# - Update IMAGE_PATHS to point to your images
# - Change database passwords for production
```

### 2. Start with Docker Compose

```bash
# Production mode
docker-compose up -d

# Development mode (with hot reload)
docker-compose -f docker-compose.yml -f docker-compose.dev.yml up
```

### 3. Seed the Database (First-Time Setup)

After the containers are running for the first time, seed the database with astronomical catalogs:

```powershell
# Windows PowerShell
.\rebuild_and_seed.ps1
```

This script populates the database with:
- **Messier Catalog** - 110 deep-sky objects
- **NGC Catalog** - 7,840+ objects from the New General Catalogue
- **Named Stars** - Common star names for reference

> **Note**: The script waits for the backend to start before seeding. If you encounter errors, ensure the backend container is fully running (`docker-compose logs backend`).

### 4. Access the Application

- **Frontend**: http://localhost:8090
- **API Docs**: http://localhost:8089/api/docs
- **Health Check**: http://localhost:8089/api/health

## 📁 Project Structure

```
AstroCat/
├── backend/
│   ├── app/
│   │   ├── api/           # REST API endpoints
│   │   ├── data/          # Seed data and catalogs
│   │   ├── extractors/    # Metadata extraction (FITS, EXIF)
│   │   ├── models/        # SQLAlchemy models
│   │   ├── schemas/       # Pydantic schemas
│   │   ├── services/      # Business logic
│   │   ├── tasks/         # Celery background tasks
│   │   ├── config.py      # Configuration
│   │   ├── database.py    # Database setup
│   │   ├── main.py        # FastAPI app
│   │   └── worker.py      # Celery worker
│   ├── alembic/           # Database migrations
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── api/           # API client
│   │   ├── components/    # React components
│   │   ├── pages/         # Page components
│   │   ├── App.jsx
│   │   └── index.css      # Styles
│   ├── Dockerfile
│   └── package.json
├── docker-compose.yml
├── docker-compose.dev.yml
└── .env.example
```

## 🔧 Development

### Backend (FastAPI)

```bash
cd backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt

# Run development server
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

- [**Architecture Overview**](docs/ARCHITECTURE.md): System design and technology stack.
- [**Backend Guide**](docs/BACKEND.md): FastAPI structure, extraction pipeline, and background tasks.
- [**Frontend Guide**](docs/FRONTEND.md): React components, design system, and state management.
- [**Database Schema**](docs/DATABASE_SCHEMA.md): Detailed table definitions and spatial query logic.
- [**Development Guide**](docs/DEVELOPMENT_GUIDE.md): Setup instructions and common development workflows.
- [**Synology Setup**](docs/SYNOLOGY_SETUP.md): Instructions for deploying on Synology NAS.
- [**Backup & Restore**](docs/BACKUP_RESTORE.md): Database backup and recovery procedures.

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
| CR2    | ✅ EXIF + sidecar  | ✅ |
| CR3    | ✅ EXIF + sidecar  | ✅ |
| ARW    | ✅ EXIF + sidecar  | ✅ |
| NEF    | ✅ EXIF + sidecar  | ✅ |
| JPG    | ✅ EXIF           | ✅ |
| TIFF   | ✅ EXIF           | ✅ |

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
