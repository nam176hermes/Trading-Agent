#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
CANONICAL_ROOT=$(cd "$ROOT/../.." && pwd)
BACKEND_ROOT="$CANONICAL_ROOT/legacy/research-backend"
WORK=$(mktemp -d)
DATA_ROOT="$WORK/runtime"
MODE_FILE="$WORK/runtime/mode"
KILL_SWITCH_FILE="$WORK/runtime/kill-switch"
PORT=43111
PID=""

cleanup() {
  if [[ -n "$PID" ]]; then
    kill "$PID" 2>/dev/null || true
    wait "$PID" 2>/dev/null || true
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT

mkdir -p "$WORK/app"
cp -a "$ROOT/src" "$ROOT/public" "$ROOT/package.json" "$ROOT/package-lock.json" "$ROOT/tsconfig.json" "$ROOT/next.config.ts" "$ROOT/postcss.config.mjs" "$WORK/app/"
ln -s "$ROOT/node_modules" "$WORK/app/node_modules"
mkdir -p "$WORK/home"
mkdir -p "$DATA_ROOT"

(
  cd "$WORK/app"
  HOME="$WORK/home" NEXT_TELEMETRY_DISABLED=1 ./node_modules/.bin/next build --webpack >/dev/null
  HOME="$WORK/home" TRADING_DATA_ROOT="$DATA_ROOT" TRADING_MODE_FILE="$MODE_FILE" TRADING_KILL_SWITCH_PATH="$KILL_SWITCH_FILE" TRADING_DASHBOARD_PASSWORD='' NEXT_TELEMETRY_DISABLED=1 ./node_modules/.bin/next start -H 127.0.0.1 -p "$PORT" >/dev/null 2>&1 &
  echo $! > "$WORK/server.pid"
)
PID=$(cat "$WORK/server.pid")

for _ in $(seq 1 30); do
  if [[ $(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORT}/api/trading/mode" 2>/dev/null) != "000" ]]; then
    break
  fi
  sleep 1
done

status=$(curl -sS -o "$WORK/response.json" -w '%{http_code}' \
  -X POST "http://127.0.0.1:${PORT}/api/trading/mode" \
  -H 'content-type: application/json' \
  --data '{"mode":"live"}')

test "$status" = "503"
grep -q 'CONFIGURATION_ERROR' "$WORK/response.json"
test ! -e "$MODE_FILE"

curl -fsS "http://127.0.0.1:${PORT}/api/trading/meta" > "$WORK/meta.json"
grep -q '"service":"legacy-trading-dashboard"' "$WORK/meta.json"
grep -q '"effective_mode":"paper"' "$WORK/meta.json"

kill "$PID"
wait "$PID" 2>/dev/null || true
PID=""

(
  cd "$WORK/app"
  HOME="$WORK/home" TRADING_DATA_ROOT="$DATA_ROOT" TRADING_MODE_FILE="$MODE_FILE" TRADING_KILL_SWITCH_PATH="$KILL_SWITCH_FILE" TRADING_DASHBOARD_PASSWORD='test-secret' NEXT_TELEMETRY_DISABLED=1 ./node_modules/.bin/next start -H 127.0.0.1 -p "$PORT" >/dev/null 2>&1 &
  echo $! > "$WORK/server.pid"
)
PID=$(cat "$WORK/server.pid")
for _ in $(seq 1 30); do
  if [[ $(curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:${PORT}/api/trading/mode" 2>/dev/null) != "000" ]]; then
    break
  fi
  sleep 1
done

missing_status=$(curl -sS -o "$WORK/missing.json" -w '%{http_code}' -X POST "http://127.0.0.1:${PORT}/api/trading/mode" -H 'content-type: application/json' --data '{"mode":"paper"}')
test "$missing_status" = "401"
grep -q 'UNAUTHORIZED' "$WORK/missing.json"

wrong_status=$(curl -sS -o "$WORK/wrong.json" -w '%{http_code}' -X POST "http://127.0.0.1:${PORT}/api/trading/mode" -H 'authorization: Bearer wrong-secret' -H 'content-type: application/json' --data '{"mode":"paper"}')
test "$wrong_status" = "401"
grep -q 'UNAUTHORIZED' "$WORK/wrong.json"

authorized_status=$(curl -sS -o "$WORK/authorized.json" -w '%{http_code}' \
  -X POST "http://127.0.0.1:${PORT}/api/trading/mode" \
  -H 'authorization: Bearer test-secret' \
  -H 'content-type: application/json' \
  --data '{"mode":"paper"}')
test "$authorized_status" = "200"
grep -q '"effective_mode":"paper"' "$WORK/authorized.json"

live_status=$(curl -sS -o "$WORK/live.json" -w '%{http_code}' \
  -X POST "http://127.0.0.1:${PORT}/api/trading/mode" \
  -H 'authorization: Bearer test-secret' \
  -H 'content-type: application/json' \
  --data '{"mode":"live"}')
test "$live_status" = "403"
grep -q 'LIVE_EXECUTION_DISABLED' "$WORK/live.json"
grep -q '^paper$' "$MODE_FILE"

kill_on_status=$(curl -sS -o "$WORK/kill-on.json" -w '%{http_code}' -X POST "http://127.0.0.1:${PORT}/api/trading/kill-switch" -H 'authorization: Bearer test-secret' -H 'content-type: application/json' --data '{"action":"on","reason":"temporary integration drill"}')
test "$kill_on_status" = "200"
grep -q '"state":"ACTIVE"' "$WORK/kill-on.json"
test -f "$KILL_SWITCH_FILE"
TRADING_DATA_ROOT="$DATA_ROOT" TRADING_MODE_FILE="$MODE_FILE" TRADING_KILL_SWITCH_PATH="$KILL_SWITCH_FILE" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$BACKEND_ROOT" python3 -c 'from kill_switch import read_kill_switch_state; assert read_kill_switch_state().state.value == "ACTIVE"'

kill_off_status=$(curl -sS -o "$WORK/kill-off.json" -w '%{http_code}' -X POST "http://127.0.0.1:${PORT}/api/trading/kill-switch" -H 'authorization: Bearer test-secret' -H 'content-type: application/json' --data '{"action":"off"}')
test "$kill_off_status" = "200"
grep -q '"state":"INACTIVE"' "$WORK/kill-off.json"
test ! -e "$KILL_SWITCH_FILE"
TRADING_DATA_ROOT="$DATA_ROOT" TRADING_MODE_FILE="$MODE_FILE" TRADING_KILL_SWITCH_PATH="$KILL_SWITCH_FILE" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$BACKEND_ROOT" python3 -c 'from kill_switch import read_kill_switch_state; assert read_kill_switch_state().state.value == "INACTIVE"'

test -s "$DATA_ROOT/memory/dashboard_mutation_audit.jsonl"
