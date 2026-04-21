#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/use_db_profile.sh <local|cloud> [--no-backup]

Switches backend .env between saved DB profiles.

Profiles:
  local -> .env.localdb
  cloud -> .env.clouddb

Examples:
  scripts/use_db_profile.sh local
  scripts/use_db_profile.sh cloud
  scripts/use_db_profile.sh cloud --no-backup
EOF
}

if [[ $# -lt 1 || $# -gt 2 ]]; then
  usage
  exit 1
fi

profile_name="$1"
no_backup="false"

if [[ $# -eq 2 ]]; then
  if [[ "$2" == "--no-backup" ]]; then
    no_backup="true"
  else
    usage
    exit 1
  fi
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

case "$profile_name" in
  local)
    profile_path="$repo_root/.env.localdb"
    ;;
  cloud)
    profile_path="$repo_root/.env.clouddb"
    ;;
  *)
    usage
    exit 1
    ;;
esac

if [[ ! -f "$profile_path" ]]; then
  echo "Profile file not found: $profile_path"
  echo "Create it first, then re-run this command."
  exit 1
fi

env_path="$repo_root/.env"
if [[ -f "$env_path" && "$no_backup" != "true" ]]; then
  backup_path="$repo_root/.env.backup.$(date +%Y%m%d_%H%M%S)"
  cp "$env_path" "$backup_path"
  echo "Backed up current .env -> $(basename "$backup_path")"
fi

cp "$profile_path" "$env_path"
echo "Activated profile: $profile_name"

echo "Current DB target from .env:"
grep -E '^(POSTGRES_HOST|POSTGRES_PORT|POSTGRES_DB|POSTGRES_USER)=' "$env_path" || true
