#!/bin/bash

set -e



echo "Check: RUN_DB_INIT is '$RUN_DB_INIT'"

if [ "$RUN_DB_INIT" = "true" ] || [ "$RUN_DB_INIT" = "TRUE" ]; then

    echo "ðŸš€ AstroCat Backend Starting..."

    echo "ðŸ“¦ Running Database Initialization..."



    echo "  -> 1/3 Initializing Database Schema..."

    python -m app.scripts.initialize_db



    echo "  -> 2/3 Seeding Messier and NGC Catalogs..."

    python -m app.data.seed



    echo "  -> 3/3 Seeding Named Stars..."

    python -m app.scripts.seed_named_stars



    echo "âœ… Database Initialization Complete!"

else

    echo "â© Skipping Database Initialization (RUN_DB_INIT is not 'true')"

fi



exec "$@"