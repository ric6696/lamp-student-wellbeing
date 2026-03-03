# Timescale Backup/Restore Checklist

This checklist is for the new LAMP schema (`users`, `devices`, `sessions`, `metric_catalog`, `vitals`, `gps`, `events`, `motion_events`, `audio_events`).

## 1) Pre-backup checks

- Confirm DB credentials in `.env` are correct.
- Confirm required extensions exist:

```sql
SELECT extname FROM pg_extension WHERE extname IN ('timescaledb','postgis');
```

- Confirm there are no active migration jobs or schema changes in progress.

## 2) Create backup

```bash
chmod +x scripts/backup_timescale.sh
./scripts/backup_timescale.sh
```

Optional custom output path:

```bash
./scripts/backup_timescale.sh backups/sensing_db_manual.dump
```

## 3) Restore into a separate database

```bash
chmod +x scripts/restore_timescale.sh
./scripts/restore_timescale.sh backups/<your_dump>.dump sensing_db_restore
```

## 4) Verify restored database

```bash
psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d sensing_db_restore -f scripts/verify_timescale_restore.sql
```

Pass criteria:

- `timescaledb` and `postgis` extensions present.
- All required tables present.
- `invalid_metric_rows = 0`.
- Orphan checks = `0`.
- Recent rows visible in all expected tables.

## 5) Recovery dry-run (optional)

- Point backend to `sensing_db_restore` in a staging environment.
- Call `/health` and `/ingest` with a small payload.
- Confirm new rows appear in restored tables.

## 6) Operational notes

- Keep at least one daily backup and one weekly backup retention window.
- Store backup files off-host (S3/object storage).
- Encrypt backups at rest and in transit.
- Document restore RTO/RPO and test quarterly.
