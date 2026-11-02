#!/usr/bin/env bash
# Runs Gluons locally without Docker on macOS or Linux.
#
# Starts embedded PostgreSQL, applies migrations, syncs skills, then runs the API, worker,
# labelled ERP simulator and web app. Ctrl+C stops everything this script started.
# No provider credentials or cloud resources are used.
#
#   scripts/dev.sh            Run until Ctrl+C
#   scripts/dev.sh --no-web   Skip the web app
#   scripts/dev.sh --check    Prove the API reaches /ready, then stop everything
#   scripts/dev.sh --stop     Stop what a previous run left behind
#
# Ports come from API_PORT, ERP_PORT and WEB_PORT. GLUONS_PYTHON overrides the interpreter.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="$ROOT/.gluons-data"
LOGS="$DATA/logs"
PID_FILE="$DATA/dev-pids.txt"
PG_DATA="$ROOT/.pgdata/dev"
API_PORT="${API_PORT:-8000}"
ERP_PORT="${ERP_PORT:-8100}"
WEB_PORT="${WEB_PORT:-3000}"
READY_TIMEOUT="${READY_TIMEOUT:-120}"
PYTHON="${GLUONS_PYTHON:-$ROOT/.venv/bin/python}"
NO_WEB=0
CHECK=0
STOP=0

for arg in "$@"; do
  case "$arg" in
    --no-web) NO_WEB=1 ;;
    --check) CHECK=1 ;;
    --stop) STOP=1 ;;
    *) echo "Unknown option: $arg"; exit 2 ;;
  esac
done

if [ ! -x "$PYTHON" ]; then
  echo "Python not found at $PYTHON"
  echo "Create it first:"
  echo "  python3.13 -m venv .venv"
  echo "  .venv/bin/python -m pip install -e \"apps/api[dev,dev-postgres,mcp,optimization,documents]\""
  echo "Or point GLUONS_PYTHON at an existing interpreter."
  exit 1
fi

# Always run this checkout's code, whatever an editable install points at.
export PYTHONPATH="$ROOT/apps/api/src"
export PYTHONUTF8=1

stop_database() {
  [ -f "$PG_DATA/postmaster.pid" ] || return 0
  bin="$("$PYTHON" -c 'from pixeltable_pgserver.utils import POSTGRES_VERSIONS as v, TARGET_POSTGRES_VERSION as t; print(v[t])')"
  "$bin/pg_ctl" -w -D "$PG_DATA" stop -m fast >/dev/null 2>&1
}

stop_all() {
  if [ -f "$PID_FILE" ]; then
    while read -r id; do
      [ -n "$id" ] && pkill -TERM -P "$id" 2>/dev/null
      [ -n "$id" ] && kill "$id" 2>/dev/null
    done <"$PID_FILE"
    rm -f "$PID_FILE"
  fi
  stop_database
}

port_in_use() {
  (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
}

require_port() {
  if port_in_use "$1"; then
    echo "Port $1 for $2 is already in use. Set API_PORT, ERP_PORT or WEB_PORT."
    exit 1
  fi
}

start_service() {
  name="$1"
  shift
  "$@" >"$LOGS/$name.log" 2>&1 &
  echo "$!" >>"$PID_FILE"
}

services_alive() {
  while read -r id; do
    if [ -n "$id" ] && ! kill -0 "$id" 2>/dev/null; then
      return 1
    fi
  done <"$PID_FILE"
  return 0
}

if [ "$STOP" = 1 ]; then
  stop_all
  echo "Stopped."
  exit 0
fi

require_port "$API_PORT" "the API"
require_port "$ERP_PORT" "the ERP simulator"
if [ "$NO_WEB" = 0 ]; then
  require_port "$WEB_PORT" "the web app"
  if [ ! -d "$ROOT/apps/web/node_modules" ]; then
    echo "Install web dependencies first with: npm ci --prefix apps/web. Or pass --no-web."
    exit 1
  fi
fi

mkdir -p "$LOGS"
cd "$ROOT" || exit 1
trap 'stop_all' EXIT
trap 'exit 130' INT TERM

export GLUONS_ENV=local
export GLUONS_PUBLIC_API_URL="http://localhost:$API_PORT"
export GLUONS_PUBLIC_WEB_URL="http://localhost:$WEB_PORT"
export GLUONS_ALLOWED_WEB_ORIGINS="http://localhost:$WEB_PORT,http://127.0.0.1:$WEB_PORT"
export GLUONS_SANDBOX_ERP_URL="http://127.0.0.1:$ERP_PORT"
export NEXT_PUBLIC_API_URL="http://localhost:$API_PORT"
export NEXT_TELEMETRY_DISABLED=1

echo "Starting embedded PostgreSQL and applying migrations..."
if ! lines="$("$PYTHON" -m gluons.devdb 2>"$LOGS/migrate.log")"; then
  echo "Migration failed. See $LOGS/migrate.log"
  exit 1
fi
while IFS= read -r line; do
  case "$line" in
    GLUONS_*=*) export "${line?}" ;;
  esac
done <<EOF
$lines
EOF

echo "Syncing skills..."
"$PYTHON" -m gluons.cli skills-sync || exit 1

: >"$PID_FILE"
start_service api "$PYTHON" -m gluons.cli serve --port "$API_PORT"
start_service worker "$PYTHON" -m gluons.cli worker
start_service simulator "$PYTHON" -m uvicorn sandbox_erp:app --app-dir apps/sandbox_erp --port "$ERP_PORT"
if [ "$NO_WEB" = 0 ]; then
  start_service web npm --prefix apps/web run dev -- --port "$WEB_PORT"
fi

deadline=$(($(date +%s) + READY_TIMEOUT))
until curl -fsS "http://127.0.0.1:$API_PORT/ready" >/dev/null 2>&1; do
  if ! services_alive; then
    echo "A service stopped during startup. See $LOGS"
    exit 1
  fi
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "API did not reach /ready within $READY_TIMEOUT seconds. See $LOGS/api.log"
    exit 1
  fi
  sleep 1
done

echo "API ready: $(curl -fsS "http://127.0.0.1:$API_PORT/ready")"
echo
[ "$NO_WEB" = 0 ] && echo "Gluons Web     http://localhost:$WEB_PORT"
echo "API docs       http://localhost:$API_PORT/docs"
echo "API readiness  http://localhost:$API_PORT/ready"
echo "MCP endpoint   http://localhost:$API_PORT/mcp"
echo "ERP simulator  http://127.0.0.1:$ERP_PORT (labelled simulator, not ERPNext)"
echo "Local email    .gluons-data/mail"
echo "Logs           .gluons-data/logs"
echo

if [ "$CHECK" = 1 ]; then
  echo "Check passed. Stopping everything this script started."
  exit 0
fi

echo "Press Ctrl+C to stop."
while services_alive; do
  sleep 1
done
echo "A service stopped. See $LOGS"
exit 1
