# Restore Database from Backup
# This script finds the latest backup in library/backups, copies it to the container, and restores it
# Usage: .\restore_backup.ps1 [BackupFilePath]
# If no path is provided, uses the latest backup from library/backups

param(
    [string]$BackupFilePath = ""
)

# Load .env file
$envFile = Join-Path (Split-Path -Parent $PSScriptRoot) ".env"
if (-not (Test-Path $envFile)) {
    Write-Error ".env file not found at $envFile"
    exit 1
}

# Parse .env file
$env_vars = @{}
Get-Content $envFile | Where-Object { $_ -and -not $_.StartsWith("#") } | ForEach-Object {
    $key, $value = $_ -split "=", 2
    if ($key -and $value) {
        $env_vars[$key.Trim()] = $value.Trim()
    }
}

$dbUser = $env_vars["POSTGRES_USER"]
$dbPassword = $env_vars["POSTGRES_PASSWORD"]
$dbName = $env_vars["POSTGRES_DB"]
$backupDir = Join-Path (Split-Path -Parent $PSScriptRoot) "library\backups"

Write-Host "=== Database Restore Script ===" -ForegroundColor Green
Write-Host "Database User: $dbUser"
Write-Host "Database Name: $dbName"
Write-Host "Backup Directory: $backupDir"
Write-Host ""

# Find or validate backup file
if ($BackupFilePath) {
    # User provided a backup file path
    if (-not (Test-Path $BackupFilePath)) {
        Write-Error "Backup file not found at: $BackupFilePath"
        exit 1
    }
    $backupFile = Get-Item $BackupFilePath
    Write-Host "Using specified backup: $($backupFile.Name)" -ForegroundColor Yellow
}
else {
    # Find latest backup in backups directory
    $backupFile = Get-ChildItem -Path $backupDir -Filter "*.sql.gz" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    
    if (-not $backupFile) {
        Write-Error "No backup files (*.sql.gz) found in $backupDir"
        exit 1
    }
    Write-Host "Latest backup: $($backupFile.Name)" -ForegroundColor Yellow
}

Write-Host "File size: $([math]::Round($backupFile.Length / 1GB, 2)) GB"
Write-Host ""

# Confirm restore
$confirm = Read-Host "Do you want to restore from this backup? This will drop and recreate the database. (y/n)"
if ($confirm -ne "y") {
    Write-Host "Restore cancelled."
    exit 0
}

# Copy backup to DB container
Write-Host "Copying backup to container..." -ForegroundColor Cyan
docker cp $backupFile.FullName astrocat-test-db-1:/tmp/backup.sql.gz
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to copy backup to container"
    exit 1
}

# Stop backend to close all database connections
Write-Host "Stopping backend container..." -ForegroundColor Cyan
docker-compose stop backend 2>&1 | Out-Null

# Drop and recreate database
Write-Host "Dropping and recreating database..." -ForegroundColor Cyan

# Drop the database with FORCE
$dropOutput = docker exec -e PGPASSWORD="$dbPassword" astrocat-test-db-1 psql -U "$dbUser" -d postgres -c "DROP DATABASE IF EXISTS `"$dbName`" WITH (FORCE);" 2>&1
if ($LASTEXITCODE -ne 0 -and $dropOutput -notmatch "does not exist") {
    Write-Error "Failed to drop database: $dropOutput"
    exit 1
}

# Create the database
$createOutput = docker exec -e PGPASSWORD="$dbPassword" astrocat-test-db-1 psql -U "$dbUser" -d postgres -c "CREATE DATABASE `"$dbName`";" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error "Failed to create database: $createOutput"
    exit 1
}

# Restore from backup
Write-Host "Restoring data from backup (this may take a while)..." -ForegroundColor Cyan
$restoreOutput = docker exec -e PGPASSWORD="$dbPassword" astrocat-test-db-1 bash -c "gunzip -c /tmp/backup.sql.gz | psql -U `"$dbUser`" -d `"$dbName`" --quiet" 2>&1

# Check for critical errors (FATAL or connection errors)
$criticalErrors = $restoreOutput | Where-Object { 
    $_ -match "FATAL" -or $_ -match "connection.*failed"
}

if ($criticalErrors) {
    Write-Error "Restore encountered critical errors:`n$($criticalErrors -join "`n")"
    docker-compose start backend 2>&1 | Out-Null
    exit 1
}

# Verify restore
Write-Host "Verifying restore..." -ForegroundColor Cyan
$result = docker exec -e PGPASSWORD="$dbPassword" astrocat-test-db-1 psql -U "$dbUser" -d "$dbName" -t -c "SELECT COUNT(*) as table_count FROM information_schema.tables WHERE table_schema='public';"

$tableCount = [int]($result -split "`n" | Where-Object { $_ -match '\d+' } | Select-Object -First 1)

# Restart backend
Write-Host "Restarting backend container..." -ForegroundColor Cyan
docker-compose start backend 2>&1 | Out-Null

Write-Host ""
Write-Host "=== Restore Complete ===" -ForegroundColor Green
Write-Host "Tables restored: $tableCount"
Write-Host "Database: $dbName"
Write-Host "User: $dbUser"
