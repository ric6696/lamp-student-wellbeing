# Team DB Onboarding

Use this checklist when a developer needs database access for local work or the shared environment.

## 1. Local-first development

For most development work, use the local Docker DB instead of the shared instance.

Local defaults:

```env
POSTGRES_HOST=localhost
POSTGRES_PORT=5433
POSTGRES_DB=sensing_db
POSTGRES_USER=postgres
POSTGRES_PASSWORD=dev_password
INGEST_API_KEY=dev_key
```

Start the local DB:

```bash
docker compose up -d db
```

List current tables:

```bash
export PGPASSWORD=dev_password
psql -h localhost -p 5433 -U postgres -d sensing_db -c "\dt"
unset PGPASSWORD
```

Expected core tables include:

- `users`
- `devices`
- `sessions`
- `vitals`
- `gps`
- `motion_events`
- `audio_events`
- `events`
- `metric_catalog`

## 2. Shared DB access prerequisites

Only use the shared DB when the task actually requires collaboration on shared data.

Before connecting, you need:

- repo access
- project `.env` configured for the shared host
- credentials received via a secure channel
- security-group allowlisting if the DB is hosted behind EC2 rules

Suggested shared env template:

```env
POSTGRES_HOST=<shared-host>
POSTGRES_PORT=<shared-port>
POSTGRES_DB=sensing_db
POSTGRES_USER=<shared-user>
POSTGRES_PASSWORD=<shared-password>
INGEST_API_KEY=<shared-api-key>
```

## 3. Install `psql`

### macOS

```bash
brew install libpq
echo 'export PATH="/opt/homebrew/opt/libpq/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

Verify:

```bash
psql --version
```

## 4. Shared DB connectivity check

```bash
export PGPASSWORD='<shared-password>'
psql -h <shared-host> -p <shared-port> -U <shared-user> -d sensing_db -c "\dt"
unset PGPASSWORD
```

## 5. Smoke test the repo ingestion path

From the repo root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/test_ingest.py
```

Then verify rows:

```bash
export PGPASSWORD=dev_password
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT COUNT(*) FROM sessions;"
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT COUNT(*) FROM vitals;"
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT COUNT(*) FROM gps;"
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT COUNT(*) FROM motion_events;"
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT COUNT(*) FROM audio_events;"
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT COUNT(*) FROM events;"
unset PGPASSWORD
```

## 6. Session-specific checks

Recent sessions:

```bash
export PGPASSWORD=dev_password
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT id, user_id, device_id, started_at, ended_at FROM sessions ORDER BY started_at DESC LIMIT 10;"
unset PGPASSWORD
```

10-minute audio exposure stats for one session:

```bash
export PGPASSWORD=dev_password
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT * FROM get_session_audio_exposure_10m_stats(<session_id>);"
unset PGPASSWORD
```

## 7. Collaboration rules

- Do not change the shared schema first and then backfill git later.
- Put schema changes in `database/init/01_schema.sql` or a reviewed migration flow.
- Coordinate any shared restore or backup action before running it.
- Avoid using the `postgres` superuser outside local development unless the task requires it.

## 8. Troubleshooting

### `psql: command not found`

Install `libpq` and update your `PATH`.

### Connection timeout

Common causes:

- DB host or port is wrong
- Docker DB is not running locally
- shared DB security rules do not include your current IP

### `401 Unauthorized` from `/ingest`

The backend `INGEST_API_KEY` does not match the client header.

### No rows in `sessions`

Confirm the request includes both:

- `event(label=session_marker, val_text=START)`
- `event(label=session_marker, val_text=END)`
