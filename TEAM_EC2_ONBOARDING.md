# Team EC2 Onboarding (4-Person Setup)

This guide is for team members who need to use the shared EC2 backend and database.

Use this together with AWS_EC2_MIGRATION_RUNBOOK.md.

## 1. Team roles

- Platform owner (1 person): Maintains EC2 host, security group, service health, backups.
- App developers (3+ people): Build iOS app, upload data, run feature tests.

Only platform owner should run host-level setup commands unless coordinated.

## 2. One-time host bootstrap (platform owner)

Run on EC2 after SSH login:

```bash
cd ~/lamp
chmod +x scripts/ec2_setup_on_host.sh
POSTGRES_PASSWORD='<strong_password>' \
INGEST_API_KEY='<shared_ingest_key>' \
REPO_URL='https://github.com/ric6696/lamp-student-wellbeing.git' \
DOMAIN_OR_IP='18.166.106.91' \
ENABLE_NGINX='true' \
START_GRAFANA='false' \
./scripts/ec2_setup_on_host.sh
```

Optional restore in same command:

```bash
DUMP_FILE='backups/sensing_db_timescale_exclude_internal_20260303_195042.dump'
```

If API fails at startup due to missing constraints after restore, rerun with:

```bash
FORCE_POST_DATA_RESTORE='true'
```

## 3. Team member local app setup

Each teammate should set endpoint to shared EC2 backend:

- ios/WellbeingApp/Configs/Local.xcconfig

Set:

```text
API_BASE_URL = http://18.166.106.91/ingest
```

Ensure iOS API key matches backend INGEST_API_KEY in:

- ios/WellbeingApp/Sources/APIClient.swift

## 4. Shared validation flow (all teammates)

1. Confirm backend health:

```bash
curl http://18.166.106.91/health
```

2. Run app on iPhone:

- Tap Start Study Session
- Wait 30-60 seconds
- Tap End Session & Upload

3. Platform owner verifies on EC2:

```bash
sudo journalctl -u lamp-api -n 100 --no-pager
```

```bash
export PGPASSWORD='<strong_password>'
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT id, user_id, device_id, started_at, ended_at FROM sessions ORDER BY id DESC LIMIT 10;"
unset PGPASSWORD
```

## 5. Operating rules for team of 4

- Use one shared EC2 endpoint for uploads during integration testing.
- Do not change security groups without notifying the team.
- Do not run schema-changing SQL directly in production-like EC2 without review.
- Keep API key out of screenshots, chat, and git commits.
- Rotate INGEST_API_KEY whenever a teammate leaves the group.

## 6. Weekly owner checklist

- Check service health and restart count:

```bash
systemctl is-active lamp-api
sudo journalctl -u lamp-api -n 200 --no-pager
```

- Verify disk usage:

```bash
df -h
```

- Create DB backup:

```bash
cd ~/lamp
export POSTGRES_HOST=localhost
export POSTGRES_PORT=5433
export POSTGRES_DB=sensing_db
export POSTGRES_USER=postgres
export POSTGRES_PASSWORD='<strong_password>'
./scripts/backup_timescale.sh
```

- Confirm backup files exist under backups/.

## 7. Updating EC2 when code changes

Use this flow whenever backend code changes are merged to main.

1. Pre-check on local machine (before deploy):

```bash
git checkout main
git pull
```

2. SSH into EC2:

```bash
ssh -i /path/to/key.pem ubuntu@18.166.106.91
```

3. Pull latest code on EC2:

```bash
cd ~/lamp
git checkout main
git pull --ff-only
```

4. Update Python dependencies if requirements changed:

```bash
cd ~/lamp
source .venv/bin/activate
pip install -r requirements.txt
deactivate
```

5. Restart API service:

```bash
sudo systemctl restart lamp-api
sudo systemctl status lamp-api --no-pager
```

6. Verify health from EC2 and from your laptop:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1/health
```

```bash
curl http://18.166.106.91/health
```

7. Verify logs after restart:

```bash
sudo journalctl -u lamp-api -n 120 --no-pager
```

8. Quick ingest smoke test (recommended):

```bash
curl -X POST http://18.166.106.91/ingest \
	-H "Content-Type: application/json" \
	-H "X-API-Key: <shared_ingest_key>" \
	-d '{"metadata":{"user_id":"deploy-smoke","device_id":"33333333-3333-3333-3333-333333333333"},"data":[{"t":"2026-04-14T03:00:00Z","type":"event","label":"session_marker","val_text":"START"},{"t":"2026-04-14T03:00:05Z","type":"event","label":"session_marker","val_text":"END"}]}'
```

Rollback if deployment fails:

```bash
cd ~/lamp
git log --oneline -n 5
git checkout <previous_commit_sha>
sudo systemctl restart lamp-api
curl http://127.0.0.1:8000/health
```

Notes:

- If database schema changed, coordinate migration SQL first, then deploy app.
- If .env values changed, update /home/ubuntu/lamp/.env before restarting lamp-api.
- Avoid running docker compose down on shared EC2 during team testing.

## 8. Recommended next improvements

- Move from HTTP to HTTPS with domain + certbot.
- Replace hardcoded iOS API key with build-config value per environment.
- Separate app and database onto different AWS resources when project load grows.
