# Device Runbook

Use this when verifying the end-to-end path from iPhone and Watch to the local backend and database.

## 1. Start the local database

```bash
docker compose up -d db
```

## 2. Start the API server

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
```

Expected checks:

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{ "status": "ok" }
```

Important:

- The iOS client currently sends `X-API-Key: dev_key`.
- Set `INGEST_API_KEY=dev_key` in `.env` unless you also change `ios/WellbeingApp/Sources/APIClient.swift`.

## 3. Point the iPhone app at your machine

The app reads `APIBaseURL` from `ios/WellbeingApp/Resources/Info.plist`.

Current format:

```text
http://<your-mac-ip>:8000/ingest
```

If your local IP changes, update this value before building.

## 4. Build and run on iPhone

1. Open `ios/WellbeingApp/WellbeingApp.xcodeproj` in Xcode.
2. Select a physical iPhone target.
3. Ensure signing is configured.
4. Grant permissions when prompted:
   - Health
   - Motion
   - Location
   - Microphone

## 5. Run a study session

1. Tap `Start Study Session`.
2. Wait at least 30-60 seconds so the app can collect audio, motion, GPS, and vitals.
3. Tap `End Session & Upload`.

Current ingest behavior:

- `session_marker START` creates a row in `sessions`.
- readings between `START` and `END` are linked with `session_id`.
- `audio_context` events go to `audio_events`.
- motion context goes to `motion_events` and generic `events` only when applicable.
- numeric audio exposure is stored in `vitals` with `metric_code = 10`.

## 6. Watch logs during the session

```bash
tail -n 200 -F logs/ingest_audit.log logs/ingest_errors.log
```

Success looks like:

- new `ingest_success` lines in `logs/ingest_audit.log`
- no new traceback in `logs/ingest_errors.log`

## 7. Verify rows in Postgres

```bash
export PGPASSWORD=dev_password
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT COUNT(*) AS sessions FROM sessions;"
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT COUNT(*) AS vitals FROM vitals;"
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT COUNT(*) AS gps FROM gps;"
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT COUNT(*) AS motion_events FROM motion_events;"
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT COUNT(*) AS audio_events FROM audio_events;"
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT COUNT(*) AS events FROM events;"
unset PGPASSWORD
```

Inspect the most recent rows:

```bash
export PGPASSWORD=dev_password
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT id, user_id, device_id, started_at, ended_at FROM sessions ORDER BY started_at DESC LIMIT 5;"
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT time, device_id, metric_code, value, session_id FROM vitals ORDER BY time DESC LIMIT 10;"
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT time, device_id, context, session_id FROM motion_events ORDER BY time DESC LIMIT 10;"
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT time, device_id, label, db, ai_label, session_id FROM audio_events ORDER BY time DESC LIMIT 10;"
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT time, device_id, label, val_text, session_id FROM events ORDER BY time DESC LIMIT 10;"
unset PGPASSWORD
```

## 8. Check 10-minute audio exposure stats for the latest session

```bash
export PGPASSWORD=dev_password
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT id FROM sessions ORDER BY started_at DESC LIMIT 1;"
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT * FROM get_session_audio_exposure_10m_stats(<session_id>);"
unset PGPASSWORD
```

## Pass Criteria

- `/health` returns `{"status":"ok"}`
- the app successfully posts batches to `/ingest`
- a new row appears in `sessions`
- new rows appear in `vitals`, `gps`, and at least one of `motion_events` or `audio_events`
- inserted rows carry the expected `device_id`
- rows captured during the session have a non-null `session_id`
