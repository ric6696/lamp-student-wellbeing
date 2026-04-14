# AWS EC2 Migration Runbook (LAMP)

This guide moves your current local setup to AWS EC2 with Ubuntu.

For team onboarding and repeatable host setup, also use:

- TEAM_EC2_ONBOARDING.md
- scripts/ec2_setup_on_host.sh

Scope covered:

- FastAPI backend deployment on EC2
- Timescale/PostgreSQL deployment on EC2 using your existing Docker Compose service
- iOS client cutover to AWS endpoint
- Backup/restore and smoke checks

## 0. Recommended target architecture

For your current repo, the fastest migration is:

- One Ubuntu EC2 instance hosting:
  - FastAPI app (systemd service)
  - TimescaleDB container from docker-compose

Later, you can split DB to RDS/Timescale Cloud.

## 1. Create AWS resources

## 1.1 Launch EC2 (Ubuntu 22.04 or 24.04)

Suggested instance size for small team testing:

- t3.medium (minimum practical)

Disk:

- 30 GB+ gp3

Attach an Elastic IP so endpoint stays stable.

## 1.2 Security Group rules

Inbound:

- 22/tcp from your current public IP only
- 80/tcp from 0.0.0.0/0 (if using Nginx)
- 443/tcp from 0.0.0.0/0 (if using TLS)
- 8000/tcp from your IP only (temporary direct API testing)
- 5432/tcp from your IP only (temporary DB admin) or remove after setup
- 3000/tcp from your IP only (Grafana, optional)

Outbound:

- Allow all (default)

Important:

- Do not expose 5432 publicly long-term.
- Do not leave 8000 public long-term; prefer 443 with reverse proxy.

## 2. Connect and prepare EC2

SSH:

```bash
ssh -i /path/to/your-key.pem ubuntu@<EC2_PUBLIC_IP>
```

Base packages:

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git curl ca-certificates gnupg lsb-release unzip
```

Install Docker + Compose plugin:

```bash
sudo apt install -y docker.io docker-compose-v2
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
newgrp docker
```

## 3. Pull repository and set production env

```bash
cd ~
git clone <your-repo-url> lamp
cd lamp
```

Create env file from your local template:

```bash
cp .env.example .env
```

Edit .env for production values:

```env
POSTGRES_PASSWORD=<strong_db_password>
POSTGRES_HOST=localhost
POSTGRES_PORT=5433
POSTGRES_DB=sensing_db
POSTGRES_USER=postgres

INGEST_API_KEY=<new_long_random_key>

LLM_PROVIDER=<openai_or_snowflake>
LLM_API_KEY=<if_openai>
LLM_MODEL=<model_name>

# If using Snowflake mode:
SNOWFLAKE_ACCOUNT=<...>
SNOWFLAKE_USER=<...>
SNOWFLAKE_USER_PASSWORD=<...>
SNOWFLAKE_ROLE=<...>
SNOWFLAKE_DATABASE=<...>
SNOWFLAKE_SCHEMA=<...>
SNOWFLAKE_WAREHOUSE=<...>
```

## 4. Start database

Use your existing compose file:

```bash
docker compose up -d db
```

Check status:

```bash
docker compose ps
docker logs --tail=100 sensing_app_db
```

## 5. Restore your existing local database dump

Your repo already includes:

- backups/sensing_db_timescale_20260303_195015.dump
- scripts/restore_timescale.sh

Run restore on EC2:

```bash
cd ~/lamp
chmod +x scripts/restore_timescale.sh
export POSTGRES_HOST=localhost
export POSTGRES_PORT=5433
export POSTGRES_USER=postgres
export POSTGRES_PASSWORD=<strong_db_password>
./scripts/restore_timescale.sh backups/sensing_db_timescale_20260303_195015.dump sensing_db
```

If that dump is empty in your checkout, pick a non-empty dump first:

```bash
ls -lh backups/*.dump
# example usable file from this repo:
./scripts/restore_timescale.sh backups/sensing_db_timescale_exclude_internal_20260303_195042.dump sensing_db
```

Verify core objects:

```bash
export PGPASSWORD=<strong_db_password>
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT extname FROM pg_extension WHERE extname IN ('timescaledb','postgis') ORDER BY extname;"
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT COUNT(*) FROM sessions;"
unset PGPASSWORD
```

If API startup later fails with an error about sessions foreign keys or missing unique constraints,
restore post-data from the same dump (constraints and indexes):

```bash
export PGPASSWORD=<strong_db_password>
pg_restore --host localhost --port 5433 --username postgres --dbname sensing_db \
  --section=post-data --no-owner --no-privileges \
  backups/sensing_db_timescale_exclude_internal_20260303_195042.dump -v
unset PGPASSWORD
```

## 6. Deploy FastAPI backend as a systemd service

Install Python and dependencies:

```bash
cd ~/lamp
sudo apt install -y python3 python3-venv python3-pip
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Create systemd service:

```bash
sudo tee /etc/systemd/system/lamp-api.service > /dev/null <<'EOF'
[Unit]
Description=LAMP FastAPI Service
After=network.target docker.service
Requires=docker.service

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/lamp
EnvironmentFile=/home/ubuntu/lamp/.env
ExecStart=/home/ubuntu/lamp/.venv/bin/uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now lamp-api
sudo systemctl status lamp-api --no-pager
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

## 7. (Recommended) Put Nginx in front with HTTPS

Install Nginx:

```bash
sudo apt install -y nginx
```

Basic reverse proxy config:

```bash
sudo tee /etc/nginx/sites-available/lamp-api > /dev/null <<'EOF'
server {
    listen 80;
    server_name <YOUR_DOMAIN_OR_EC2_DNS>;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF

# If you use shell variables in proxy_set_header, keep EOF single-quoted
# so values like $host are not expanded by the shell.
```

Enable site:

```bash
sudo ln -s /etc/nginx/sites-available/lamp-api /etc/nginx/sites-enabled/lamp-api
sudo nginx -t
sudo systemctl restart nginx
```

TLS with Certbot (if domain is set):

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d <YOUR_DOMAIN>
```

## 8. Update iOS app endpoint and API key

Change API endpoint in local config:

- ios/WellbeingApp/Configs/Local.xcconfig

Set:

```text
API_BASE_URL = https://<YOUR_DOMAIN>/ingest
```

If no domain/TLS yet (temporary):

```text
API_BASE_URL = http://<EC2_PUBLIC_IP>/ingest
```

Also ensure client API key matches backend INGEST_API_KEY.

## 9. Smoke test end-to-end

Backend check:

```bash
curl https://<YOUR_DOMAIN>/health
```

Ingest check:

```bash
curl -X POST https://<YOUR_DOMAIN>/ingest \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <your_ingest_api_key>" \
  -d @fyp_test.json
```

DB row check:

```bash
export PGPASSWORD=<strong_db_password>
psql -h localhost -p 5433 -U postgres -d sensing_db -c "SELECT id, device_id, started_at, ended_at FROM sessions ORDER BY started_at DESC LIMIT 5;"
unset PGPASSWORD
```

## 10. Hardening checklist (do this before production)

- Change all default secrets (DB password, API key).
- Restrict Security Group 22 to your fixed IP.
- Remove direct 5432 public access after initial setup.
- Use HTTPS only (443).
- Set up EC2 snapshots and routine DB backups with scripts/backup_timescale.sh.
- Add CloudWatch or log shipping for service visibility.
- Add fail2ban and unattended upgrades on Ubuntu.

## 11. Operations quick commands

Service logs:

```bash
sudo journalctl -u lamp-api -n 200 --no-pager
sudo journalctl -u lamp-api -f
```

Restart API:

```bash
sudo systemctl restart lamp-api
```

Restart DB container:

```bash
docker compose restart db
```

Create DB backup:

```bash
export POSTGRES_HOST=localhost
export POSTGRES_PORT=5433
export POSTGRES_DB=sensing_db
export POSTGRES_USER=postgres
export POSTGRES_PASSWORD=<strong_db_password>
./scripts/backup_timescale.sh
```

## 12. Cutover and rollback

Cutover:

- Deploy EC2 stack
- Verify health and ingest on EC2
- Point iOS API_BASE_URL to EC2/domain
- Monitor for 24 hours

Rollback:

- Revert iOS API_BASE_URL to local endpoint
- Keep local backend+db running as fallback

---

If you want, the next step can be a second runbook for "split architecture" (EC2 app + managed DB) so the DB is no longer on the same VM.
