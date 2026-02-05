# ===========================================
# AstroCat Backend Dockerfile
# ===========================================

# -------------------------------------------
# Development Stage
# -------------------------------------------
FROM python:3.12-slim AS development

WORKDIR /app

# Install system dependencies for PostGIS, FITS processing, and raw images
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libffi-dev \
    libraw-dev \
    && mkdir -p /data/thumbnails /data/images /data/mount2 /data/mount3 \
    && chown -R 1000:1000 /data/thumbnails /data/images /data/mount2 /data/mount3 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir watchfiles

# Copy application code
COPY . .
RUN chmod +x /app/scripts/start_backend.sh
ENTRYPOINT ["/app/scripts/start_backend.sh"]

# Expose port
EXPOSE 8089

# Development command (can be overridden)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8089", "--reload"]

# -------------------------------------------
# Production Stage
# -------------------------------------------
FROM python:3.12-slim AS production

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd --create-home --shell /bin/bash AstroCat

# Create required directories and set permissions
RUN mkdir -p /data/thumbnails /data/images /data/mount2 /data/mount3 \
    && chown -R AstroCat:AstroCat /data/thumbnails /data/images /data/mount2 /data/mount3

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY --chown=AstroCat:AstroCat . .
RUN chmod +x /app/scripts/start_backend.sh

# Switch to non-root user
USER AstroCat
ENTRYPOINT ["/app/scripts/start_backend.sh"]

# Expose port
EXPOSE 8089

# Production command
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8089", "--workers", "4"]
