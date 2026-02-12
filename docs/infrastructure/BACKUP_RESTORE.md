# Database Backup and Restore

AstroCat includes both automated and manual solutions for backing up the PostgreSQL database.

## Automated Backups

A sidecar container (`db-backup`) runs alongside the database and performs regular backups.

- **Schedule**: Defined by `BACKUP_SCHEDULE` in `.env` (default is `@daily`).
- **Retention**: Defined by `BACKUP_RETENTION_DAYS` (default is 7 days).
- **Storage**: Backups are stored in the `./backups` directory on the host.
- **Format**: Compressed SQL files (`.sql.gz`).

### Monitoring Automated Backups
You can check the logs of the backup container:
```powershell
docker compose logs db-backup
```

## Manual Backups

If you need to take an immediate backup before a major change:

1. Open PowerShell.
2. Run the backup script:
   ```powershell
   .\scripts\backup_db.ps1
   ```
3. The backup will be saved in `.\backups\manual_backup_YYYYMMDD_HHMMSS.sql.gz`.

## Restoring the Database

> [!WARNING]
> Restoring the database will overwrite all existing data. Ensure you have a current backup before proceeding.

1. Open PowerShell.
2. Run the restore script, providing the path to the backup file:
   ```powershell
   .\scripts\restore_backup.ps1 -BackupFile .\backups\your_backup_file.sql.gz
   ```
3. Type `Y` when prompted to confirm the restore.

### If the Database Container is Lost

If the `AstroCat-postgres` container or its volume is deleted:
1. Re-run `docker compose up -d` to recreate the containers.
2. Use the `restore_backup.ps1` script as described above to restore your latest backup.
