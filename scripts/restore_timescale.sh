#!/usr/bin/env bash
set -euo pipefail

# Restores a custom-format dump into a target database and runs post-restore checks.
# Usage:
#   ./scripts/restore_timescale.sh <dump_file> <target_db>
#
# Required env vars:
#   POSTGRES_HOST, POSTGRES_PORT, POSTGRES_USER, POSTGRES_PASSWORD
#
# Optional env vars:
#   DROP_TARGET_DB=true|false (default: true)

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
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"

if [[ $# -lt 2 ]]; then
  echo "Usage: $0 <dump_file> <target_db>"
  exit 1
fi

DUMP_FILE="$1"
TARGET_DB="$2"
DROP_TARGET_DB="${DROP_TARGET_DB:-true}"

if [[ ! -f "$DUMP_FILE" ]]; then
  echo "[restore] dump file not found: $DUMP_FILE"
  exit 1
fi

export PGPASSWORD="$POSTGRES_PASSWORD"

echo "[restore] host=$POSTGRES_HOST port=$POSTGRES_PORT user=$POSTGRES_USER"
echo "[restore] dump=$DUMP_FILE"
echo "[restore] target_db=$TARGET_DB"

# Optionally run restore commands inside the DB container to avoid client/server version mismatches
DOCKER_RESTORE=${DOCKER_RESTORE:-false}
CONTAINER_NAME=${CONTAINER_NAME:-sensing_app_db}

if [[ "$DROP_TARGET_DB" == "true" ]]; then
  echo "[restore] dropping target db if exists"
  if [[ "${DOCKER_RESTORE:-false}" == "true" ]]; then
    docker exec -e PGPASSWORD="$POSTGRES_PASSWORD" ${CONTAINER_NAME} sh -c "dropdb --if-exists -U postgres $TARGET_DB || true"
  else
    dropdb --if-exists --host "$POSTGRES_HOST" --port "$POSTGRES_PORT" --username "$POSTGRES_USER" "$TARGET_DB"
  fi
fi

echo "[restore] creating target db"
if [[ "${DOCKER_RESTORE:-false}" == "true" ]]; then
  docker exec -e PGPASSWORD="$POSTGRES_PASSWORD" ${CONTAINER_NAME} sh -c "createdb -U postgres $TARGET_DB"
else
  createdb --host "$POSTGRES_HOST" --port "$POSTGRES_PORT" --username "$POSTGRES_USER" "$TARGET_DB"
fi

# Helper to run pg_restore via local client or inside the sensing_app_db container to avoid
# client/server version mismatch. Set DOCKER_RESTORE=true to force container-based restore.
DOCKER_RESTORE=${DOCKER_RESTORE:-false}
CONTAINER_NAME=${CONTAINER_NAME:-sensing_app_db}

run_pg_restore() {
  local section_arg="$1" # can be empty or --section=pre-data|data|post-data
  if [[ "$DOCKER_RESTORE" == "true" ]]; then
    echo "[restore] running pg_restore (container) section=$section_arg"
    cat "$DUMP_FILE" | docker exec -i -e PGPASSWORD="$POSTGRES_PASSWORD" "$CONTAINER_NAME" \
      pg_restore -U postgres $section_arg --no-owner --no-privileges --dbname "$TARGET_DB" -v
  else
    echo "[restore] running pg_restore (local) section=$section_arg"
    pg_restore \
      --host "$POSTGRES_HOST" \
      --port "$POSTGRES_PORT" \
      --username "$POSTGRES_USER" \
      $section_arg \
      --dbname "$TARGET_DB" \
      --verbose \
      --clean \
      --if-exists \
      --no-owner \
      --no-privileges \
      "$DUMP_FILE"
  fi
}

echo "[restore] restoring pre-data (schema + extensions)"
run_pg_restore "--section=pre-data"

echo "[restore] ensuring extensions and hypertables"
psql --host "$POSTGRES_HOST" --port "$POSTGRES_PORT" --username "$POSTGRES_USER" --dbname "$TARGET_DB" -v ON_ERROR_STOP=1 <<SQL || true
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS timescaledb;
-- Create hypertables if they don't already exist (safe to call repeatedly)
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM _timescaledb_catalog.hypertable WHERE hypertable_name = 'vitals') THEN
    PERFORM create_hypertable('vitals', 'time');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM _timescaledb_catalog.hypertable WHERE hypertable_name = 'gps') THEN
    PERFORM create_hypertable('gps', 'time');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM _timescaledb_catalog.hypertable WHERE hypertable_name = 'motion_events') THEN
    PERFORM create_hypertable('motion_events', 'time');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM _timescaledb_catalog.hypertable WHERE hypertable_name = 'audio_events') THEN
    PERFORM create_hypertable('audio_events', 'time');
  END IF;
  IF NOT EXISTS (SELECT 1 FROM _timescaledb_catalog.hypertable WHERE hypertable_name = 'events') THEN
    PERFORM create_hypertable('events', 'time');
  END IF;
END$$;
SQL

echo "[restore] restoring data (with triggers disabled)"
if [[ "$DOCKER_RESTORE" == "true" ]]; then
  # streaming into container pg_restore with --disable-triggers during data restore
  echo "[restore] (container) restoring data section"
  cat "$DUMP_FILE" | docker exec -i -e PGPASSWORD="$POSTGRES_PASSWORD" "$CONTAINER_NAME" \
    pg_restore -U postgres --section=data --no-owner --no-privileges --disable-triggers --dbname "$TARGET_DB" -v
else
  pg_restore \
    --host "$POSTGRES_HOST" \
    --port "$POSTGRES_PORT" \
    --username "$POSTGRES_USER" \
    --section=data \
    --disable-triggers \
    --dbname "$TARGET_DB" \
    --verbose \
    --no-owner \
    --no-privileges \
    "$DUMP_FILE"
fi

echo "[restore] restoring post-data (indexes, constraints)"
run_pg_restore "--section=post-data"

echo "[restore] basic verification"
if [[ "${DOCKER_RESTORE:-false}" == "true" ]]; then
  echo "[restore] running verification inside container $CONTAINER_NAME"
  docker exec -e PGPASSWORD="$POSTGRES_PASSWORD" "$CONTAINER_NAME" sh -c "psql -U postgres -d \"$TARGET_DB\" -v ON_ERROR_STOP=1 -c \"SELECT extname FROM pg_extension WHERE extname IN ('timescaledb','postgis') ORDER BY extname;\"; psql -U postgres -d \"$TARGET_DB\" -c \"SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('users','devices','sessions','metric_catalog','vitals','gps','events','motion_events','audio_events') ORDER BY table_name;\"; psql -U postgres -d \"$TARGET_DB\" -c \"SELECT count(*) AS vitals_rows FROM vitals;\"; psql -U postgres -d \"$TARGET_DB\" -c \"SELECT count(*) AS gps_rows FROM gps;\"; psql -U postgres -d \"$TARGET_DB\" -c \"SELECT count(*) AS events_rows FROM events;\""
else
  psql --host "$POSTGRES_HOST" --port "$POSTGRES_PORT" --username "$POSTGRES_USER" --dbname "$TARGET_DB" -v ON_ERROR_STOP=1 <<'SQL'
SELECT extname FROM pg_extension WHERE extname IN ('timescaledb','postgis') ORDER BY extname;
SELECT table_name FROM information_schema.tables
WHERE table_schema='public'
  AND table_name IN ('users','devices','sessions','metric_catalog','vitals','gps','events','motion_events','audio_events')
ORDER BY table_name;
SELECT count(*) AS vitals_rows FROM vitals;
SELECT count(*) AS gps_rows FROM gps;
SELECT count(*) AS events_rows FROM events;
SQL
fi

echo "[restore] completed for $TARGET_DB"
