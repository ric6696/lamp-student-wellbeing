# LAMP: Multimodal Mobile Sensing Systems for Enhancing Student Mental Health and Concentration

## Developer Hand-off

### Setup (5 minutes)

1. Copy env template:
   - `cp .env.example .env`
2. Start the database sandbox:
   - `docker compose up -d`
3. The schema auto-applies from [database/init/01_schema.sql](database/init/01_schema.sql).
4. Metric codes for clients are documented in [metrics.md](metrics.md).

**Default DB connection (local):**

- Host: `localhost`
- Port: `5433` (mapped from container 5432)
- DB: `sensing_db`
- User: `postgres`
- Password: from `.env`

## The Goal

We are building a batch-processing system. iOS collects locally $\rightarrow$ flushes JSON batch to API $\rightarrow$ API inserts into Postgres.

## The Schema

We use TimescaleDB hypertables for high-frequency vitals and PostGIS for location data.
Daily aggregates (e.g., steps, sleep) live in `daily_summaries`; see [metrics.md](metrics.md) for the metric code list.

## Data Contract (Batch JSON)

Clients send a batch with device metadata and a list of readings:

- `metadata.device_id`: UUID string
- `data[].type`: `vital` | `gps` | `event`
- `data[].t`: ISO 8601 timestamp

See [scripts/mock_generator.py](scripts/mock_generator.py) for a concrete example payload.

For iOS specifics (Swift structs + curl), see [IOS_INTEGRATION.md](IOS_INTEGRATION.md).

## Integration Manifest

### JSON Schema (keys and types)

- `metadata.device_id`: string (UUID)
- `metadata.user_id`: string (UUID, optional)
- `metadata.model_name`: string (optional)
- `metadata.version`: string (optional)
- `data[]`: array of readings
  - `type`: `vital` | `gps` | `event`
  - `t`: string (ISO 8601 timestamp)
  - `vital`: `code` (int), `val` (number)
  - `gps`: `lat` (number), `lon` (number), `acc` (number, optional)
  - `event`: `label` (string), `val_text` (string, optional), `metadata` (object, optional)

### Metric Codes (`sensor_vitals.metric_type`)

| Metric             | Code |
| ------------------ | ---- |
| Heart Rate         | 1    |
| HRV (SDNN)         | 2    |
| Ambient Noise (dB) | 10   |
| Step Count         | 20   |

Daily summaries (e.g., steps) are stored in `daily_summaries`. Step Count can also be sent
as `metric_type` 20 when hourly aggregates are needed.

### Batch Limit

- Send a batch every 5 minutes or every 100 records, whichever comes first.

### Hourly Summary (Materialized View)

The hourly summary view powers fast charts in dev:

- `REFRESH MATERIALIZED VIEW sensor_hourly_summary;`

Example cron (every 15 minutes):

```cron
*/15 * * * * docker exec -i sensing_app_db psql -U postgres -d sensing_db -c "REFRESH MATERIALIZED VIEW sensor_hourly_summary;"
```

## Ingestion Logic

The routing logic is implemented in [scripts/ingest_logic.py](scripts/ingest_logic.py). It parses each reading and inserts into:

- `sensor_vitals` (TimescaleDB hypertable)
- `sensor_location` (PostGIS geography)
- `user_events` (discrete events)

## Ingestion API (FastAPI)

The `/ingest` endpoint validates payloads, checks an API key, and enqueues DB insertion in the background.

1. Install dependencies:
   - `pip install -r requirements.txt`
2. Ensure `.env` contains `INGEST_API_KEY` and DB settings.
3. Run the API:
   - `uvicorn backend.app.main:app --reload --port 8000`

One-liner (DB + API):

`docker compose up -d && uvicorn backend.app.main:app --reload --port 8000`

### /docs usage example (iOS)

Open the interactive docs at `https://<your-ngrok-domain>.ngrok-free.app/docs` and use:

- Header: `X-API-Key: <INGEST_API_KEY>`
- Endpoint: `POST /ingest`

Sample request body:

```json
{
  "metadata": {
    "device_id": "11111111-1111-1111-1111-111111111111",
    "version": "1.0",
    "model_name": "iPhone 15 Pro"
  },
  "data": [
    {
      "t": "2026-02-10T14:57:07Z",
      "type": "gps",
      "lat": 34.0522,
      "lon": -118.2437,
      "acc": 5.0
    },
    {
      "t": "2026-02-10T14:57:02Z",
      "type": "vital",
      "code": 1,
      "val": 72
    },
    {
      "t": "2026-02-10T14:56:57Z",
      "type": "event",
      "label": "motion_state",
      "val_text": "walking"
    }
  ]
}
```

## Expose API for teammates (ngrok)

1. Install ngrok (macOS):
   - `brew install ngrok/ngrok/ngrok`
2. Add your authtoken:
   - `ngrok config add-authtoken <YOUR_TOKEN>`
3. Start the tunnel:
   - `ngrok http 8000`
4. Share the HTTPS URL shown (e.g., `https://xxxxx.ngrok-free.app/docs`).

## How to run the test

1. Install the Postgres driver:
   - `pip install psycopg2-binary`
2. Run the script:
   - `python scripts/test_ingest.py`

## Quick Verification (SQL)

- `SELECT * FROM sensor_vitals;`
- `SELECT time, ST_AsText(coords) FROM sensor_location;`
- `SELECT * FROM user_events;`

## Troubleshooting

- Docker paused: unpause Docker Desktop and re-run `docker compose up -d`.
- 401 Unauthorized: ensure `X-API-Key` matches `INGEST_API_KEY` in `.env`.
- ngrok URL changes: use the current “Forwarding” URL from the ngrok terminal.
