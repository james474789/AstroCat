# PowerShell script to restore the AstroCat Postgres database from a backup file

param(
    [Parameter(Mandatory=$true)]
    [string]$BackupFile
)

$containerName = "AstroCat-postgres"

if (-not (Test-Path $BackupFile)) {
    Write-Host "Error: Backup file '$BackupFile' not found." -ForegroundColor Red
    exit 1
}

Write-Host "Starting restore to $containerName from $BackupFile..." -ForegroundColor Cyan
Write-Host "WARNING: This will overwrite existing data. Proceed? (Y/N)" -ForegroundColor Yellow
$confirmation = Read-Host
if ($confirmation -ne "Y" -and $confirmation -ne "y") {
    Write-Host "Restore cancelled."
    exit 0
}

try {
    # Extract and pipe to psql inside the container
    # We use -U AstroCat and assumes the database is already created or psql will handle it
    # Since it's a pg_dumpall, it should handle database creation if --clean was used, 
    # but usually we just want to restore to the existing DB.
    
    Write-Host "Cleaning existing database (dropping and recreating)..." -ForegroundColor Gray
    # Caution: This is a destructive action
    docker exec $containerName psql -U AstroCat -d postgres -c "DROP DATABASE IF EXISTS AstroCat;"
    docker exec $containerName psql -U AstroCat -d postgres -c "CREATE DATABASE AstroCat;"

    Write-Host "Restoring data..." -ForegroundColor Gray
    
    $tempFile = "$BackupFile.tmp.sql"
    if ($BackupFile.EndsWith(".zip")) {
        Expand-Archive -Path $BackupFile -DestinationPath "$env:TEMP\AstroCat_restore" -Force
        $sqlFile = Get-ChildItem -Path "$env:TEMP\AstroCat_restore\*.sql" | Select-Object -First 1
        Get-Content $sqlFile.FullName -Raw | docker exec -i $containerName psql -U AstroCat -d AstroCat
        Remove-Item "$env:TEMP\AstroCat_restore" -Recurse -Force
    } elseif ($BackupFile.EndsWith(".gz")) {
        # If user still has .gz files (e.g. from sidecar), we try to use docker to decompress
        cat $BackupFile | docker exec -i $containerName gunzip -c | docker exec -i $containerName psql -U AstroCat -d AstroCat
    } else {
        Get-Content $BackupFile -Raw | docker exec -i $containerName psql -U AstroCat -d AstroCat
    }

    if ($LASTEXITCODE -eq 0) {
        Write-Host "Restore successful!" -ForegroundColor Green
    } else {
        Write-Host "Restore failed with exit code $LASTEXITCODE" -ForegroundColor Red
    }
} catch {
    Write-Host "An error occurred: $_" -ForegroundColor Red
}
