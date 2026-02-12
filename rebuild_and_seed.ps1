Write-Host "Rebuilding AstroCat Backend..."
docker-compose up -d --build backend

Write-Host "Waiting for backend to start..."
Start-Sleep -Seconds 10

Write-Host "Seeding Messier and NGC Catalogs..."
docker-compose exec -T backend python -m app.data.seed

Write-Host "Seeding Named Stars..."
docker-compose exec -T backend python -m app.scripts.seed_named_stars

Write-Host "Done! Please refresh the application."
