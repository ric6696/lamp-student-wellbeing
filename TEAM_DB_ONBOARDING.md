# Team DB Onboarding (EC2 PostgreSQL)

Use this checklist for every developer joining the project.

---

## 1) Access prerequisites

- Have project repository access.
- Have local Python environment set up for this repo.
- Receive DB credentials via secure channel (1Password/Bitwarden), **not chat**:
  - `POSTGRES_HOST` (current: `18.166.106.91`)
  - `POSTGRES_PORT` (current: `5432`)
  - `POSTGRES_DB` (current: `sensing_db`)
  - `POSTGRES_USER` (e.g. `app_ingest`)
  - `POSTGRES_PASSWORD`

---

## 2) AWS security group allowlist

For each developer machine, add inbound rule to the EC2 instance security group:

- Type: `PostgreSQL`
- Port: `5432`
- Source: `<developer_public_ip>/32`

Get current public IP:

```bash
curl -4 ifconfig.me
```

Notes:

- IPs can change (home/mobile networks). If DB suddenly fails, re-check this first.
- Only add `SSH 22` for developers who actually need server administration.

---

## 3) Local project env setup

Update local `.env` in repo root:

```env
POSTGRES_HOST=18.166.106.91
POSTGRES_PORT=5432
POSTGRES_DB=sensing_db
POSTGRES_USER=app_ingest
POSTGRES_PASSWORD=<REAL_PASSWORD>

INGEST_API_KEY=dev_key
CORS_ORIGINS=*
```

---

## 4) Install and verify psql client

### macOS

```bash
brew install libpq
echo 'export PATH="/opt/homebrew/opt/libpq/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
```

### Verify connectivity

```bash
export PGPASSWORD='<REAL_PASSWORD>'
psql -h 18.166.106.91 -p 5432 -U app_ingest -d sensing_db -c "\dt"
unset PGPASSWORD
```

Expected output includes:

- `devices`
- `sensor_vitals`
- `sensor_location`
- `user_events`
- `daily_summaries`

---

## 5) Project smoke test

Run ingestion test from repo root:

```bash
python scripts/test_ingest.py
```

Then verify row counts:

```bash
export PGPASSWORD='<REAL_PASSWORD>'
psql -h 18.166.106.91 -p 5432 -U app_ingest -d sensing_db -c "SELECT COUNT(*) FROM sensor_vitals;"
psql -h 18.166.106.91 -p 5432 -U app_ingest -d sensing_db -c "SELECT COUNT(*) FROM sensor_location;"
unset PGPASSWORD
```

---

## 6) Team collaboration rules

- Never edit schema directly in production-like DB first.
- Schema changes must be committed to git (migration SQL or migration tool output), reviewed, then applied.
- Keep one person responsible for applying schema changes to shared EC2 DB.
- Avoid using `postgres` superuser in application code.

---

## 7) Minimum operational hygiene

- Restrict security group rules to developer `/32` IPs.
- Rotate DB passwords when team membership changes.
- Keep backups/snapshots enabled.
- Document current credential owner and rotation date.

---

## 8) Troubleshooting quick map

### Timeout (`Operation timed out`)

Usually network path issue:

- Security group missing `5432` from your current IP.
- Wrong EC2 public IP.
- Route table/NACL issue.

Quick check:

```bash
nc -vz 18.166.106.91 5432
```

### Authentication failed

Usually password mismatch:

- Confirm `.env` password is correct.
- Reset DB user password on EC2 if needed.

### `psql` not found

Install `libpq` and ensure PATH updated (see section 4).
