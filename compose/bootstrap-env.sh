#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# .env is the ONE source of truth. This is how it reaches a container that
# insists on a file.
#
# The compose files pass every database, object-store and dashboard password as
# a DOCKER SECRET — a file at /run/secrets/… — never as an `environment:` value,
# because an environment value is visible to `docker inspect`, to every process
# in the container, and to any crash reporter that dumps the environment. The
# discipline is the same one the connection profile enforces with
# `secret://name`.
#
# So the passwords live in .env (one place, gitignored) and this script
# projects them into compose/secrets/* (gitignored) on the way up. Nothing is
# invented here: a value missing from .env is an error, not a generated
# default, because a generated default is a password nobody knows.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="$root/.env"
secrets_dir="$root/compose/secrets"

if [ ! -f "$env_file" ]; then
  echo "no .env at $env_file" >&2
  echo "    cp .env.example .env      and fill in the values you have" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
. "$env_file"
set +a

mkdir -p "$secrets_dir"
missing=0

write_secret() {
  local name="$1" value="${2:-}"
  if [ -z "$value" ]; then
    echo "  ✗ $name — unset in .env" >&2
    missing=1
    return
  fi
  # No trailing newline. Postgres and MinIO read the file VERBATIM, and a
  # trailing newline is a different password — the failure then looks like bad
  # credentials rather than a stray byte, which is a bad afternoon.
  printf '%s' "$value" > "$secrets_dir/$name"
  chmod 600 "$secrets_dir/$name"
  echo "  ✓ compose/secrets/$name"
}

echo "projecting .env → compose/secrets/"
write_secret pg_platform_password "${CINQFLOW_PG_PLATFORM_PASSWORD:-}"
write_secret pg_data_password     "${CINQFLOW_PG_DATA_PASSWORD:-}"
write_secret keycloak_password    "${CINQFLOW_KEYCLOAK_PASSWORD:-}"
write_secret minio_password       "${CINQFLOW_MINIO_PASSWORD:-}"
write_secret grafana_password     "${CINQFLOW_GRAFANA_PASSWORD:-}"

if [ "$missing" -ne 0 ]; then
  echo "" >&2
  echo "fill the values above in .env, then run again." >&2
  exit 1
fi
