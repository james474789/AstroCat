#!/bin/bash
set -e

if [ "$RUN_DB_INIT" = "true" ]; then
    echo "🚀 AstroCat Backend Starting..."
    echo "📦 Running Database Initialization..."

    echo "  -> 1/3 Initializing Database Schema..."
    python -m app.scripts.initialize_db

    echo "  -> 2/3 Seeding Messier and NGC Catalogs..."
    python -m app.data.seed

    echo "  -> 3/3 Seeding Named Stars..."
    python -m app.scripts.seed_named_stars

    echo "✅ Database Initialization Complete!"
fi

exec "$@"
