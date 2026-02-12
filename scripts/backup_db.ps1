# PowerShell script to trigger an immediate manual backup of the AstroCat Postgres database

# Get the directory where this script is located
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
# The library/backups folder is relative to the project root (one level up from scripts/)
$ProjectRoot = Resolve-Path "$ScriptDir\.."
$localBackupDir = "$ProjectRoot\library\backups"

# Get the container name
$containerName = "AstroCat-postgres"

# Get the current date for the filename prefix
$date = Get-Date -Format "yyyyMMdd_HHmmss"
$backupFile = "manual_backup_$date.sql"

# Ensure the backups directory exists locally
if (-not (Test-Path $localBackupDir)) {
    New-Item -ItemType Directory -Path $localBackupDir | Out-Null
}

Write-Host "Starting manual backup of $containerName..." -ForegroundColor Cyan
Write-Host "Target directory: $localBackupDir" -ForegroundColor Gray

try {
    # We use cmd /c for redirection to avoid PowerShell 5.1's UTF-16 conversion on >
    # This keeps the SQL dump in its native UTF-8/Binary format
    $tempSqlPath = Join-Path $localBackupDir $backupFile
    Write-Host "Dumping database to $backupFile..." -ForegroundColor Gray
    
    cmd /c "docker exec $containerName /usr/bin/pg_dumpall -U AstroCat > `"$tempSqlPath`""
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Backup successful! Saved temporarily to $tempSqlPath" -ForegroundColor Green
        
        # Use tar for compression (Windows 10/11 native)
        # We change directory to avoid including the full path in the archive
        Write-Host "Compressing backup using tar ($backupFile.gz)..." -ForegroundColor Gray
        
        $currentDir = Get-Location
        Set-Location $localBackupDir
        tar -czf "$backupFile.gz" "$backupFile"
        $tarExit = $LASTEXITCODE
        Set-Location $currentDir
        
        if ($tarExit -eq 0) {
            # Success - remove temp file with retry logic to handle file locks (AV/Indexers)
            Write-Host "Cleaning up temporary SQL file..." -ForegroundColor Gray
            $retryCount = 0
            $deleted = $false
            # Give it 30 seconds total (10 attempts * 3s) to release the file lock
            while (-not $deleted -and $retryCount -lt 10) {
                try {
                    if (Test-Path $tempSqlPath) {
                        Remove-Item $tempSqlPath -Force -ErrorAction Stop
                    }
                    $deleted = $true
                }
                catch {
                    $retryCount++
                    if ($retryCount -lt 10) {
                        Write-Host "File $backupFile is locked (likely by an indexer or antivirus), retrying cleanup ($retryCount/10)..." -ForegroundColor Yellow
                        Start-Sleep -Seconds 3
                    }
                }
            }
            
            if ($deleted) {
                Write-Host "Compressed backup saved to $localBackupDir\$backupFile.gz" -ForegroundColor Green
            }
            else {
                Write-Host "Could not delete $backupFile after 10 attempts. It may still be locked by system indexing." -ForegroundColor Yellow
                Write-Host "You can safely delete $backupFile manually once it is released." -ForegroundColor Gray
                Write-Host "Compressed backup is ready at $localBackupDir\$backupFile.gz" -ForegroundColor Green
            }
        }
        else {
            Write-Host "Compression failed (tar exit code $tarExit). Keeping uncompressed backup." -ForegroundColor Yellow
        }
    }
    else {
        Write-Host "Backup failed with exit code $LASTEXITCODE" -ForegroundColor Red
    }
}
catch {
    Write-Host "An error occurred: $_" -ForegroundColor Red
}
