#!/usr/bin/env bash
set -euo pipefail

# Creates a compressed custom-format PostgreSQL backup suitable for TimescaleDB.
# Usage:
#   ./scripts/backup_timescale.sh [output_file]
#
# Required env vars:
#   POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD
#
# Optional env vars:
#   BACKUP_DIR (default: ./backups)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

: "${POSTGRES_HOST:?POSTGRES_HOST is required}"
: "${POSTGRES_PORT:?POSTGRES_PORT is required}"
: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"

BACKUP_DIR="${BACKUP_DIR:-$REPO_ROOT/backups}"
mkdir -p "$BACKUP_DIR"

TS="$(date +%Y%m%d_%H%M%S)"
DEFAULT_OUT="$BACKUP_DIR/${POSTGRES_DB}_timescale_${TS}.dump"
OUT_FILE="${1:-$DEFAULT_OUT}"

export PGPASSWORD="$POSTGRES_PASSWORD"

echo "[backup] host=$POSTGRES_HOST port=$POSTGRES_PORT db=$POSTGRES_DB user=$POSTGRES_USER"
echo "[backup] output=$OUT_FILE"

# Optional: exclude Timescale internal schemas to make logical restore safer
EXCLUDE_INTERNALS=${EXCLUDE_INTERNALS:-true}
# Optional: run pg_dump inside the DB container to avoid client/server version mismatch
DOCKER_DUMP=${DOCKER_DUMP:-false}
CONTAINER_NAME=${CONTAINER_NAME:-sensing_app_db}

if [[ "$DOCKER_DUMP" == "true" ]]; then
  echo "[backup] running pg_dump inside container $CONTAINER_NAME (uses postgres user)"
  # Use the container's postgres superuser for dumping to avoid role mismatch.
  docker exec -i "$CONTAINER_NAME" sh -c "pg_dump -U postgres -F c -Z 9 \
    --exclude-schema=_timescaledb_catalog --exclude-schema=_timescaledb_internal --exclude-schema=_timescaledb_config ${POSTGRES_DB}" > "$OUT_FILE"
else
  if [[ "$EXCLUDE_INTERNALS" == "true" ]]; then
    pg_dump \
      --host "$POSTGRES_HOST" \
      --port "$POSTGRES_PORT" \
      --username "$POSTGRES_USER" \
      --dbname "$POSTGRES_DB" \
      --format=custom \
      --compress=9 \
      --verbose \
      --exclude-schema=_timescaledb_catalog \
      --exclude-schema=_timescaledb_internal \
      --exclude-schema=_timescaledb_config \
      --file "$OUT_FILE"
  else
    pg_dump \
      --host "$POSTGRES_HOST" \
      --port "$POSTGRES_PORT" \
      --username "$POSTGRES_USER" \
      --dbname "$POSTGRES_DB" \
      --format=custom \
      --compress=9 \
      --verbose \
      --file "$OUT_FILE"
  fi
fi

echo "[backup] completed: $OUT_FILE"
