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
