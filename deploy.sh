#!/usr/bin/env bash
#
# One-command deployment. Checks the things that commonly go wrong before
# touching anything, so a bad config fails in a second rather than halfway
# through a build.
#
#   ./deploy.sh          build and start
#   ./deploy.sh status   show service health
#   ./deploy.sh logs     follow the backend log
#   ./deploy.sh backup   dump the application metadata database
#
set -euo pipefail
cd "$(dirname "$0")"

# The server override is applied automatically when OPERATIONAL_DB_NETWORK is
# set, which is what a database running as another container on this host
# requires. Leaving it out produced a stack that built and started cleanly and
# then could not reach the database at all.
COMPOSE_FILES="-f docker-compose.prod.yml"
if [ -f .env.production ] && grep -qE '^OPERATIONAL_DB_NETWORK=.+' .env.production; then
  COMPOSE_FILES="$COMPOSE_FILES -f docker-compose.server.yml"
fi
COMPOSE="docker compose $COMPOSE_FILES --env-file .env.production"
RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'; NC=$'\033[0m'

die()  { echo "${RED}✗ $*${NC}" >&2; exit 1; }
ok()   { echo "${GREEN}✓ $*${NC}"; }
warn() { echo "${YELLOW}! $*${NC}"; }

preflight() {
  command -v docker >/dev/null || die "docker is not installed"
  docker info >/dev/null 2>&1 || die "the docker daemon is not running"
  [ -f .env.production ] || die ".env.production is missing. Copy .env.production.example and fill it in."

  # shellcheck disable=SC1091
  set -a; . ./.env.production; set +a

  for var in APP_SECRET APP_DB_PASSWORD REDIS_PASSWORD; do
    [ -n "${!var:-}" ] || die "$var is empty in .env.production. Generate one: openssl rand -base64 48"
  done
  [ "${#APP_SECRET}" -ge 32 ] || die "APP_SECRET is too short (needs 32+ characters)"

  if [ "${DATA_SOURCE_MODE:-live}" = "live" ]; then
    [ -n "${DATABASE_HOST:-}" ] || die "DATABASE_HOST is empty and DATA_SOURCE_MODE=live"
    [ -n "${DATABASE_NAME:-}" ] || die "DATABASE_NAME is empty and DATA_SOURCE_MODE=live"
    [ -n "${DATABASE_PASSWORD:-}" ] || die "DATABASE_PASSWORD is empty and DATA_SOURCE_MODE=live"
    [ "${DATABASE_ENFORCE_READ_ONLY:-true}" = "true" ] \
      || warn "DATABASE_ENFORCE_READ_ONLY is off. The startup write-probe will not run."
    [ "${DATABASE_SSL:-require}" != "disable" ] \
      || warn "DATABASE_SSL=disable. Reporting traffic will cross the network unencrypted."
  else
    warn "DATA_SOURCE_MODE=${DATA_SOURCE_MODE}. This serves demo data, not your database."
  fi

  perms=$(stat -c "%a" .env.production 2>/dev/null || echo "600")
  [ "$perms" = "600" ] || warn ".env.production is mode $perms; 600 is safer (chmod 600 .env.production)"

  if [ -n "${OPERATIONAL_DB_NETWORK:-}" ]; then
    # A database container published only on 127.0.0.1 is unreachable from
    # inside another container through the host gateway; joining its network is.
    docker network inspect "$OPERATIONAL_DB_NETWORK" >/dev/null 2>&1       || die "OPERATIONAL_DB_NETWORK=$OPERATIONAL_DB_NETWORK does not exist. List them: docker network ls"
    ok "will join existing network $OPERATIONAL_DB_NETWORK"
  fi

  case "${PUBLIC_ORIGIN:-}" in
    https://*) ;;
    "") warn "PUBLIC_ORIGIN is unset. Session cookies cannot be marked Secure." ;;
    *)  warn "PUBLIC_ORIGIN is http://. Sign-in works, but credentials cross the network in the clear." ;;
  esac

  ok "pre-flight checks passed"
}

case "${1:-up}" in
  up)
    preflight
    echo "Building images..."
    $COMPOSE build
    echo "Starting services..."
    $COMPOSE up -d
    echo "Waiting for health..."
    for _ in $(seq 1 30); do
      if $COMPOSE ps --format '{{.Service}} {{.Status}}' | grep -q "api.*healthy"; then break; fi
      sleep 3
    done
    $COMPOSE ps --format 'table {{.Service}}\t{{.Status}}'

    if $COMPOSE logs api 2>&1 | grep -q "Read-only self-test passed"; then
      ok "read-only self-test passed: the connection cannot write"
    elif $COMPOSE logs api 2>&1 | grep -qi "REFUSING TO START"; then
      die "the operational connection has WRITE access. Create a SELECT-only role (see DEPLOYMENT.md step 1)."
    fi

    echo
    ok "deployed on port ${HTTP_PORT:-80}"
    echo "  If this is a fresh install, create the first administrator:"
    echo "    $COMPOSE exec api python scripts/bootstrap_admin.py --email you@company.com --generate-password"
    ;;
  status)  $COMPOSE ps --format 'table {{.Service}}\t{{.Status}}' ;;
  logs)    $COMPOSE logs -f "${2:-api}" ;;
  down)    $COMPOSE down ;;
  backup)
    mkdir -p backups
    out="backups/bi_metadata_$(date +%F_%H%M).sql.gz"
    $COMPOSE exec -T metadata-db pg_dump -U "${APP_DB_USER:-bi_app}" "${APP_DB_NAME:-bi_metadata}" | gzip > "$out"
    ok "wrote $out"
    ;;
  *) echo "usage: $0 {up|status|logs|down|backup}"; exit 1 ;;
esac
