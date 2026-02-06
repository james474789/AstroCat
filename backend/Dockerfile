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
    supervisor \
    && mkdir -p /data/thumbnails /data/images /data/mount2 /data/mount3 \
    && chown -R 1000:1000 /data/thumbnails /data/images /data/mount2 /data/mount3 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install --no-cache-dir watchfiles

# Copy application code
COPY . .
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Expose port
EXPOSE 8089

# Development command (can be overridden)
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]

# -------------------------------------------
# Production Stage
# -------------------------------------------
FROM python:3.12-slim AS production

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    supervisor \
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
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Switch to non-root user
USER AstroCat

# Expose port
EXPOSE 8089

# Production command
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
