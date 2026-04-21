# LAMP: Multimodal Mobile Sensing for Study Sessions

This repository contains:

- a FastAPI ingestion service in `backend/app`
- a PostgreSQL + TimescaleDB local database in `docker-compose.yml`
- the schema and helper SQL in `database/init/01_schema.sql`
- an iPhone + Apple Watch client in `ios/WellbeingApp`

The current flow is:

`iPhone / Watch -> JSON batch -> FastAPI /ingest -> Postgres`

Concentration flow (auto on session end):

`session_marker END -> session close -> concentration job queued -> worker runs LLM -> score/reason saved`

## Quick Start

### 1. Create local env

```bash
cp .env.example .env
```

Set at least these values in `.env`:

```env
POSTGRES_PASSWORD=dev_password
INGEST_API_KEY=dev_key
LLM_PROVIDER=snowflake
LLM_MODEL=claude-sonnet-4-5
```

Notes:

- The iOS app currently sends `X-API-Key: dev_key` from `ios/WellbeingApp/Sources/APIClient.swift`.
- Local DB defaults are already aligned with the app and backend:
  - `POSTGRES_HOST=localhost`
  - `POSTGRES_PORT=5433`
  - `POSTGRES_DB=sensing_db`
  - `POSTGRES_USER=postgres`

### 2. Start the database

```bash
docker compose up -d db
```

The schema is applied from `database/init/01_schema.sql` when the DB container is created.

If you need a clean rebuild of the local DB and schema:

```bash
docker compose down
docker compose up -d db
```

### 3. Create the Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 4. Start the API

```bash
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
```

Verify health:

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{ "status": "ok" }
```

## Current Schema

The active tables are:

- `users`
- `devices`
- `sessions`
- `vitals`
- `gps`
- `motion_events`
- `audio_events`
- `events`
- `metric_catalog`

Important details:

- `vitals.metric_code = 10` stores numeric audio exposure / noise samples.
- `audio_events` stores categorical audio context labels like `quiet`, `busy`, or SoundAnalysis labels.
- `events` stores generic markers such as `session_marker`.
- `sessions` are now created from `session_marker` `START` / `END` events during ingest.
- `session_concentration_analysis` is auto-created on backend startup and stores concentration `status`, `score`, and `reason`.

## Auto Concentration Analysis

- Trigger: ingest receives `event(label=session_marker, val_text=END)`.
- Worker: backend picks pending jobs and computes features from `audio_events`, `vitals`, `gps`, and `motion_events`.
- LLM: provider is controlled by `LLM_PROVIDER` (`snowflake` or `openai`).
- Result endpoint: `GET /sessions/{session_id}/concentration`

Snowflake mode env:

```env
LLM_PROVIDER=snowflake
LLM_MODEL=claude-sonnet-4-5
SNOWFLAKE_ACCOUNT=...
SNOWFLAKE_USER=...
SNOWFLAKE_USER_PASSWORD=...
SNOWFLAKE_ROLE=...
SNOWFLAKE_DATABASE=...
SNOWFLAKE_SCHEMA=...
SNOWFLAKE_WAREHOUSE=...
```

## Switch Between Local And Cloud DB

Use DB profile files so you can switch environments without manually editing `.env` each time.

### One-time setup (for each teammate)

1. Create the active env file:

```bash
cp .env.example .env
```

2. Create profile files:

```bash
cp .env.example .env.localdb
cp .env.example .env.clouddb
```

3. Edit `.env.localdb` for local Docker DB:
  - `POSTGRES_HOST=localhost`
  - `POSTGRES_PORT=5433`
  - `POSTGRES_DB=sensing_db`
  - `POSTGRES_USER=postgres`
  - `POSTGRES_PASSWORD=dev_password` (or your local password)

4. Edit `.env.clouddb` for cloud DB:
  - `POSTGRES_HOST=<cloud db host>`
  - `POSTGRES_PORT=<cloud db port>`
  - `POSTGRES_DB=<cloud db name>`
  - `POSTGRES_USER=<cloud db user>`
  - `POSTGRES_PASSWORD=<cloud db password>`

5. Keep shared app settings consistent in both profiles unless intentionally different:
  - `INGEST_API_KEY`
  - `LLM_PROVIDER`
  - `LLM_MODEL`
  - Snowflake keys when `LLM_PROVIDER=snowflake`

### Daily switching commands

Switch to local DB:

```bash
scripts/use_db_profile.sh local
```

Switch to cloud DB:

```bash
scripts/use_db_profile.sh cloud
```

Optional (skip automatic backup of current `.env`):

```bash
scripts/use_db_profile.sh local --no-backup
scripts/use_db_profile.sh cloud --no-backup
```

The switch script copies the chosen profile into `.env` and, by default, writes a backup file like `.env.backup.YYYYMMDD_HHMMSS`.

### After switching profile

Restart the backend so new environment values are loaded:

```bash
uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
```

Quick verification:

```bash
grep -E '^(POSTGRES_HOST|POSTGRES_PORT|POSTGRES_DB|POSTGRES_USER)=' .env
curl http://localhost:8000/health
```

Dummy E2E test:

```bash
python3 scripts/test_end_session_concentration.py
```

Dummy test output:

- `scripts/output/dummy_concentration_result_session_<session_id>.json`

Backend latest-result output (written/overwritten when a session ends):

- `llm/CCoT/output/concentration_analysis_results.json`

## Supported Payload

Clients send:

- `metadata.device_id`: required
- `metadata.user_id`: optional; if omitted, backend currently uses `device_id` as `user_id`
- `metadata.version`: optional
- `metadata.model_name`: optional
- `data[]`: array of `vital`, `gps`, or `event` readings

Current Pydantic models live in `backend/app/models.py`.

### Example payload

```json
{
  "metadata": {
    "device_id": "11111111-1111-1111-1111-111111111111",
    "version": "1.0",
    "model_name": "iPhone 15 Pro"
  },
  "data": [
    {
      "t": "2026-03-06T04:19:06Z",
      "type": "event",
      "label": "session_marker",
      "val_text": "START"
    },
    {
      "t": "2026-03-06T04:19:10Z",
      "type": "vital",
      "code": 10,
      "val": 63.2
    },
    {
      "t": "2026-03-06T04:19:12Z",
      "type": "event",
      "label": "audio_context",
      "val_text": "busy",
      "metadata": {
        "db": "-37.20",
        "confidence": "0.71",
        "label_source": "sound_analysis"
      }
    },
    {
      "t": "2026-03-06T04:29:22Z",
      "type": "event",
      "label": "session_marker",
      "val_text": "END"
    }
  ]
}
```

## Running The iOS App

See `ios/WellbeingApp/README.md` and `DEVICE_RUNBOOK.md` for the full device workflow.

Before building:

- update `APIBaseURL` in `ios/WellbeingApp/Resources/Info.plist` if your machine IP changed
- make sure the API is reachable from the phone on port `8000`
- keep `INGEST_API_KEY=dev_key` locally unless you also change `APIClient.swift`

## Useful Local Checks

### Tail ingest logs

```bash
tail -n 200 -F logs/ingest_audit.log logs/ingest_errors.log
```

### Smoke-test ingestion without the phone

```bash
source .venv/bin/activate
python scripts/test_ingest.py
```

### Inspect latest rows

```bash
export PGPASSWORD=dev_password
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT id, device_id, started_at, ended_at FROM sessions ORDER BY started_at DESC LIMIT 10;"
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT time, device_id, metric_code, value, session_id FROM vitals ORDER BY time DESC LIMIT 10;"
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT time, device_id, label, db, ai_label, session_id FROM audio_events ORDER BY time DESC LIMIT 10;"
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT time, device_id, label, val_text, session_id FROM events ORDER BY time DESC LIMIT 10;"
unset PGPASSWORD
```

### Check 10-minute audio stats for a session

```bash
export PGPASSWORD=YOUR_PASSWORD
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT * FROM get_session_audio_exposure_10m_stats(<session_id>);"
unset PGPASSWORD
```

## File Guide

- `backend/app/main.py`: FastAPI app, auth, `/ingest`, `/health`
- `backend/app/ingest.py`: DB write path, session creation, row linking
- `backend/app/models.py`: request contract
- `database/init/01_schema.sql`: schema + helper SQL functions
- `metrics.md`: metric code reference
- `DEVICE_RUNBOOK.md`: phone-to-backend verification workflow
- `IOS_INTEGRATION.md`: payload examples and curl checks
- `TEAM_DB_ONBOARDING.md`: shared DB onboarding notes

## Common Pitfalls

- If `/health` fails, verify the DB container is running and `.env` matches `localhost:5433`.
- If iPhone uploads fail with `401`, your server `INGEST_API_KEY` does not match the hardcoded `dev_key` in `APIClient.swift`.
- If no new tables appear after schema edits, recreate the DB container so `database/init/01_schema.sql` runs again.
- If session rows are missing, confirm the client sent both `session_marker START` and `session_marker END` events.
