# Device Runbook (Phone → Backend → DB)

## 1) Start local services

```bash
docker compose -f docker-compose.yml up -d --build db
```

## 2) Start API server

```bash
source .venv/bin/activate
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
```

Expected:

- `GET /health` responds with `{\"status\": \"ok\"}`
- API key is read from `.env` (`INGEST_API_KEY=dev_key`)

## 3) iOS app backend target

Configured in `ios/WellbeingApp/Resources/Info.plist`:

- `APIBaseURL = http://10.89.132.230:8000/ingest`

If your IP changes, update that value before building.

## 4) Build + run on iPhone

1. Open Xcode project from `ios/WellbeingApp`.
2. Select your iPhone as run target.
3. Ensure Signing Team is set.
4. Run app and grant requested permissions (Health, Motion, Location, Microphone).

## 5) Collect and upload sample session

1. Tap **Start Study Session**.
2. Walk around for 1-2 minutes.
3. Tap **End Study Session** to flush final upload.

## 6) Verify ingestion

In a new terminal, tail logs:

```bash
tail -n 200 -F logs/ingest_audit.log logs/ingest_errors.log
```

Check DB row counts:

```bash
export PGPASSWORD={PGPASSWORD}
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT count(*) AS vitals FROM vitals;"
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT count(*) AS gps FROM gps;"
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT count(*) AS events FROM events;"
```

Check most recent rows:

```bash
export PGPASSWORD={PGPASSWORD}
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT ts, device_id, code, value FROM vitals ORDER BY ts DESC LIMIT 10;"
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT ts, device_id, geom, acc FROM gps ORDER BY ts DESC LIMIT 10;"
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT ts, device_id, label, value_text FROM events ORDER BY ts DESC LIMIT 10;"
```

## 7) Pass criteria

- API returns accepted ingest responses
- `logs/ingest_errors.log` stays empty (or no new traceback)
- Counts in `vitals`, `gps`, `events` increase after each session
- Latest rows contain current timestamps and your test device ID
