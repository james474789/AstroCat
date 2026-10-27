"""
Native desktop app runtime (D1, docs/design/20261027-D1-desktop-apps.md).

The Tauri shell starts ``python -m app.desktop.launcher``, which plays the part docker-compose
and supervisord play in the container: it runs a private Postgres + PostGIS and Redis, the same
init scripts as the compose ``command``, then uvicorn, the Celery workers and beat.

Standard library only: the launcher must run before (and regardless of) the app's dependencies.
"""
