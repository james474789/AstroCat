# AstroCat Authentication

## Overview

AstroCat supports optional authentication controlled by the `AUTH_REQUIRED` environment variable.

- **Default**: `AUTH_REQUIRED=true` — All API endpoints require a valid session.
- **Disabled**: `AUTH_REQUIRED=false` — Endpoints are accessible without login (suitable for trusted local networks).

## Login Flow

### First-Time Setup
On first launch, there is no admin user. One must be created using the setup page or a script:

```bash
docker exec AstroCat-backend python -m app.scripts.create_admin
```

### Login
- **Endpoint**: `POST /api/auth/login`
- **Credentials**: Email and password.
- **Password Hashing**: `bcrypt` via passlib.
- **Session**: On success, an `access_token` cookie is set (HttpOnly, SameSite=Lax).

### Session Persistence
- Uses JWT Bearer tokens stored in an HttpOnly cookie (`access_token`).
- Cookie lifetime is configurable via `COOKIE_MAX_AGE` (default: 2 days).
- `COOKIE_SECURE` should be `true` when using HTTPS.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `AUTH_REQUIRED` | `true` | Enable/disable authentication |
| `SECRET_KEY` | *(must set)* | JWT signing key. Generate with `python -c "import secrets; print(secrets.token_hex(32))"` |
| `COOKIE_SECURE` | `false` | Set `true` for HTTPS |
| `COOKIE_MAX_AGE` | `172800` | Cookie lifetime in seconds (2 days) |
| `COOKIE_SAMESITE` | `lax` | SameSite cookie policy |

## Architecture
- **Framework**: FastAPI (Python)
- **Auth Endpoint**: `backend/app/api/auth.py`
- **Dependencies**: `backend/app/api/dependencies.py` — provides `get_current_user` dependency.
- **Service**: `backend/app/services/auth_service.py`
