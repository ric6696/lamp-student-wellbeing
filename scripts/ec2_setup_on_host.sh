#!/usr/bin/env bash
set -euo pipefail

# EC2 host bootstrap for this repository.
# Run this script on the Ubuntu EC2 host after SSH login.
#
# Example:
#   POSTGRES_PASSWORD='change-me' \
#   INGEST_API_KEY='change-me-too' \
#   REPO_URL='https://github.com/ric6696/lamp-student-wellbeing.git' \
#   DOMAIN_OR_IP='18.166.106.91' \
#   ENABLE_NGINX='true' \
#   ./scripts/ec2_setup_on_host.sh

REPO_DIR="${REPO_DIR:-$HOME/lamp}"
REPO_URL="${REPO_URL:-}"
DOMAIN_OR_IP="${DOMAIN_OR_IP:-}"
ENABLE_NGINX="${ENABLE_NGINX:-true}"
START_GRAFANA="${START_GRAFANA:-false}"
RUN_APT_UPGRADE="${RUN_APT_UPGRADE:-true}"
DUMP_FILE="${DUMP_FILE:-}"
FORCE_POST_DATA_RESTORE="${FORCE_POST_DATA_RESTORE:-false}"

POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"
INGEST_API_KEY="${INGEST_API_KEY:-}"

LLM_PROVIDER="${LLM_PROVIDER:-openai}"
LLM_API_KEY="${LLM_API_KEY:-}"
LLM_MODEL="${LLM_MODEL:-gpt-5-mini}"

usage() {
  cat <<'USAGE'
Usage:
  POSTGRES_PASSWORD=... INGEST_API_KEY=... [options] ./scripts/ec2_setup_on_host.sh

Required env vars:
  POSTGRES_PASSWORD    Postgres password for .env
  INGEST_API_KEY       Backend API key for /ingest auth

Optional env vars:
  REPO_DIR                 Default: $HOME/lamp
  REPO_URL                 Used when REPO_DIR does not exist yet
  DOMAIN_OR_IP             Nginx server_name; when empty uses underscore (_)
  ENABLE_NGINX             true|false (default: true)
  START_GRAFANA            true|false (default: false)
  RUN_APT_UPGRADE          true|false (default: true)
  DUMP_FILE                e.g. backups/sensing_db_timescale_exclude_internal_20260303_195042.dump
  FORCE_POST_DATA_RESTORE  true|false (default: false)

  LLM_PROVIDER             Default: openai
  LLM_API_KEY              Optional; set when provider needs it
  LLM_MODEL                Default: gpt-5-mini
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ -z "$POSTGRES_PASSWORD" || -z "$INGEST_API_KEY" ]]; then
  echo "[error] POSTGRES_PASSWORD and INGEST_API_KEY are required"
  usage
  exit 1
fi

set_env() {
  local key="$1"
  local value="$2"
  local file="$3"
  if grep -q "^${key}=" "$file"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$file"
  else
    echo "${key}=${value}" >> "$file"
  fi
}

echo "[1/8] Installing base packages"
sudo apt update
if [[ "$RUN_APT_UPGRADE" == "true" ]]; then
  sudo DEBIAN_FRONTEND=noninteractive apt upgrade -y
fi
sudo apt install -y git curl ca-certificates gnupg lsb-release unzip docker.io docker-compose-v2 postgresql-client python3 python3-venv python3-pip

if [[ "$ENABLE_NGINX" == "true" ]]; then
  sudo apt install -y nginx
fi

echo "[2/8] Enabling Docker"
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER" || true

echo "[3/8] Preparing repository at $REPO_DIR"
if [[ -d "$REPO_DIR/.git" ]]; then
  echo "[info] Repo exists, pulling latest changes"
  git -C "$REPO_DIR" pull --ff-only
elif [[ -n "$REPO_URL" ]]; then
  git clone "$REPO_URL" "$REPO_DIR"
else
  echo "[error] Repo missing and REPO_URL not provided"
  exit 1
fi

cd "$REPO_DIR"

if [[ ! -f .env ]]; then
  cp .env.example .env
fi

echo "[4/8] Updating .env"
set_env POSTGRES_PASSWORD "$POSTGRES_PASSWORD" .env
set_env POSTGRES_DB sensing_db .env
set_env POSTGRES_USER postgres .env
set_env POSTGRES_HOST localhost .env
set_env POSTGRES_PORT 5433 .env
set_env INGEST_API_KEY "$INGEST_API_KEY" .env
set_env LLM_PROVIDER "$LLM_PROVIDER" .env
set_env LLM_MODEL "$LLM_MODEL" .env
if [[ -n "$LLM_API_KEY" ]]; then
  set_env LLM_API_KEY "$LLM_API_KEY" .env
fi

echo "[5/8] Starting database container"
if [[ "$START_GRAFANA" == "true" ]]; then
  docker compose up -d db grafana
else
  docker compose up -d db
fi

if [[ -n "$DUMP_FILE" ]]; then
  echo "[6/8] Restoring dump: $DUMP_FILE"
  export POSTGRES_HOST=localhost
  export POSTGRES_PORT=5433
  export POSTGRES_USER=postgres
  export POSTGRES_PASSWORD
  chmod +x scripts/restore_timescale.sh
  ./scripts/restore_timescale.sh "$DUMP_FILE" sensing_db

  if [[ "$FORCE_POST_DATA_RESTORE" == "true" ]]; then
    echo "[info] Restoring post-data section"
    export PGPASSWORD="$POSTGRES_PASSWORD"
    pg_restore --host localhost --port 5433 --username postgres --dbname sensing_db --section=post-data --no-owner --no-privileges "$DUMP_FILE" -v || true
    unset PGPASSWORD
  fi
else
  echo "[6/8] No DUMP_FILE provided, skipping restore"
fi

echo "[7/8] Installing Python dependencies"
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "[8/8] Configuring systemd service"
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

sudo systemctl daemon-reload
sudo systemctl enable --now lamp-api

if [[ "$ENABLE_NGINX" == "true" ]]; then
  server_name="${DOMAIN_OR_IP:-_}"
  sudo tee /etc/nginx/sites-available/lamp-api > /dev/null <<'EOF'
server {
    listen 80;
    server_name __SERVER_NAME__;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF
  sudo sed -i "s|__SERVER_NAME__|${server_name}|g" /etc/nginx/sites-available/lamp-api
  sudo rm -f /etc/nginx/sites-enabled/default
  sudo ln -sf /etc/nginx/sites-available/lamp-api /etc/nginx/sites-enabled/lamp-api
  sudo nginx -t
  sudo systemctl enable --now nginx
  sudo systemctl restart nginx
fi

echo "[done] Service status"
systemctl --no-pager --full status lamp-api | head -n 20 || true

echo "[done] Health checks"
curl -sS http://127.0.0.1:8000/health || true
if [[ "$ENABLE_NGINX" == "true" ]]; then
  curl -sS http://127.0.0.1/health || true
fi

echo "[next] If this is public-facing, lock down security groups and rotate secrets."