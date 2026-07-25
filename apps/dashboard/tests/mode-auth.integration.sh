#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
CANONICAL_ROOT=$(cd "$ROOT/../.." && pwd)
BACKEND_ROOT="$CANONICAL_ROOT/legacy/research-backend"
WORK=$(mktemp -d)
DATA_ROOT="$WORK/runtime"
MODE_FILE="$WORK/runtime/mode"
KILL_SWITCH_FILE="$WORK/runtime/kill-switch"
PID=""

cleanup() {
  if [[ -n "$PID" ]]; then
    kill "$PID" 2>/dev/null || true
    wait "$PID" 2>/dev/null || true
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

fail() {
  printf 'mode authorization integration: %s\n' "$*" >&2
  exit 1
}

expect_status() {
  local actual=$1
  local expected=$2
  local label=$3
  [[ "$actual" == "$expected" ]] || fail "$label returned HTTP $actual (expected $expected)"
}

PORT=$(node -e '
  const net = require("node:net");
  const server = net.createServer();
  server.listen(0, "127.0.0.1", () => {
    process.stdout.write(String(server.address().port));
    server.close();
  });
')
BASE_URL="http://localhost:${PORT}"
COOKIE_JAR="$WORK/admin-cookies.txt"
SERVER_LOG="$WORK/server.log"

mkdir -p "$WORK/app" "$WORK/home" "$DATA_ROOT"
chmod 0700 "$DATA_ROOT"
printf 'paper\n' > "$MODE_FILE"
chmod 0600 "$MODE_FILE"
cp -a "$ROOT/src" "$ROOT/public" "$ROOT/package.json" "$ROOT/package-lock.json" \
  "$ROOT/tsconfig.json" "$ROOT/next.config.ts" "$ROOT/postcss.config.mjs" "$WORK/app/"
ln -s "$ROOT/node_modules" "$WORK/app/node_modules"

if ! (
  cd "$WORK/app"
  HOME="$WORK/home" NEXT_TELEMETRY_DISABLED=1 \
    ./node_modules/.bin/next build --webpack >"$WORK/build.log" 2>&1
); then
  sed -n '1,240p' "$WORK/build.log" >&2
  fail 'isolated dashboard build failed'
fi

(
  cd "$WORK/app"
  exec env \
    HOME="$WORK/home" \
    TRADING_DATA_ROOT="$DATA_ROOT" \
    TRADING_MODE_FILE="$MODE_FILE" \
    TRADING_KILL_SWITCH_PATH="$KILL_SWITCH_FILE" \
    TRADING_DASHBOARD_PASSWORD='fixture-reader-password' \
    TRADING_DASHBOARD_OPERATOR_PASSWORD='fixture-operator-password' \
    TRADING_DASHBOARD_ADMIN_PASSWORD='fixture-admin-password' \
    TRADING_DASHBOARD_SESSION_SECRET='fixture-session-signing-secret-at-least-32-characters' \
    TRADING_DASHBOARD_TRUSTED_PROXY_SECRET='fixture-trusted-proxy-secret' \
    LIVE_EXECUTION_ENABLED=false \
    LIVE_TRADING_APPROVED=false \
    NEXT_TELEMETRY_DISABLED=1 \
    ./node_modules/.bin/next start -H 127.0.0.1 -p "$PORT"
) >"$SERVER_LOG" 2>&1 &
PID=$!

ready=false
for _ in $(seq 1 60); do
  if ! kill -0 "$PID" 2>/dev/null; then
    sed -n '1,200p' "$SERVER_LOG" >&2
    fail 'dashboard exited before becoming ready'
  fi
  if [[ $(curl -sS -o /dev/null -w '%{http_code}' "$BASE_URL/api/auth/session" 2>/dev/null || true) == '200' ]]; then
    ready=true
    break
  fi
  sleep 0.25
done
[[ "$ready" == true ]] || fail 'dashboard did not become ready'

unauthenticated_status=$(curl -sS -o "$WORK/unauthenticated.json" -w '%{http_code}' \
  -X POST "$BASE_URL/api/trading/mode" \
  -H "origin: $BASE_URL" \
  -H 'content-type: application/json' \
  --data '{"mode":"live"}')
expect_status "$unauthenticated_status" 401 'unauthenticated live-mode mutation'
grep -q '"code":"UNAUTHORIZED"' "$WORK/unauthenticated.json" \
  || fail 'unauthenticated mutation did not return structured authorization error'
grep -q '^paper$' "$MODE_FILE" || fail 'unauthenticated mutation changed paper mode'

login_status=$(curl -sS -o "$WORK/admin-login.json" -c "$COOKIE_JAR" \
  -w '%{http_code}' -X POST "$BASE_URL/api/auth/session" \
  -H 'content-type: application/json' \
  -H 'cf-connecting-ip: 198.51.100.40' \
  -H 'x-trusted-proxy-secret: fixture-trusted-proxy-secret' \
  --data '{"password":"fixture-admin-password"}')
expect_status "$login_status" 200 'admin login'
grep -q '"authenticated":true' "$WORK/admin-login.json" \
  || fail 'admin login did not report authentication success'

paper_status=$(curl -sS -o "$WORK/paper.json" -w '%{http_code}' \
  -X POST "$BASE_URL/api/trading/mode" \
  -b "$COOKIE_JAR" \
  -H "origin: $BASE_URL" \
  -H "x-forwarded-host: localhost:$PORT" \
  -H 'x-forwarded-proto: http' \
  -H 'content-type: application/json' \
  --data '{"mode":"paper"}')
if [[ "$paper_status" != '200' ]]; then
  sed -n '1,80p' "$WORK/paper.json" >&2
fi
expect_status "$paper_status" 200 'authorized paper-mode mutation'
grep -q '"effective_mode":"paper"' "$WORK/paper.json" \
  || fail 'paper-mode mutation did not report paper mode'
grep -q '^paper$' "$MODE_FILE" || fail 'paper-mode mutation did not write paper mode'

live_status=$(curl -sS -o "$WORK/live.json" -w '%{http_code}' \
  -X POST "$BASE_URL/api/trading/mode" \
  -b "$COOKIE_JAR" \
  -H "origin: $BASE_URL" \
  -H "x-forwarded-host: localhost:$PORT" \
  -H 'x-forwarded-proto: http' \
  -H 'content-type: application/json' \
  --data '{"mode":"live"}')
expect_status "$live_status" 403 'authorized live-mode mutation'
grep -q '"code":"LIVE_EXECUTION_DISABLED"' "$WORK/live.json" \
  || fail 'live-mode mutation did not fail with LIVE_EXECUTION_DISABLED'
grep -q '^paper$' "$MODE_FILE" || fail 'rejected live mutation changed paper mode'

kill_on_status=$(curl -sS -o "$WORK/kill-on.json" -w '%{http_code}' \
  -X POST "$BASE_URL/api/trading/kill-switch" \
  -b "$COOKIE_JAR" \
  -H "origin: $BASE_URL" \
  -H "x-forwarded-host: localhost:$PORT" \
  -H 'x-forwarded-proto: http' \
  -H 'content-type: application/json' \
  --data '{"action":"on","reason":"temporary integration drill"}')
expect_status "$kill_on_status" 200 'kill-switch activation'
grep -q '"state":"ACTIVE"' "$WORK/kill-on.json" \
  || fail 'kill-switch activation did not report ACTIVE'
[[ -f "$KILL_SWITCH_FILE" ]] || fail 'kill-switch activation did not create state file'
TRADING_DATA_ROOT="$DATA_ROOT" TRADING_MODE_FILE="$MODE_FILE" \
TRADING_KILL_SWITCH_PATH="$KILL_SWITCH_FILE" PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH="$BACKEND_ROOT" python3 -c \
  'from kill_switch import read_kill_switch_state; assert read_kill_switch_state().state.value == "ACTIVE"'

kill_off_status=$(curl -sS -o "$WORK/kill-off.json" -w '%{http_code}' \
  -X POST "$BASE_URL/api/trading/kill-switch" \
  -b "$COOKIE_JAR" \
  -H "origin: $BASE_URL" \
  -H "x-forwarded-host: localhost:$PORT" \
  -H 'x-forwarded-proto: http' \
  -H 'content-type: application/json' \
  --data '{"action":"off"}')
expect_status "$kill_off_status" 200 'kill-switch deactivation'
grep -q '"state":"INACTIVE"' "$WORK/kill-off.json" \
  || fail 'kill-switch deactivation did not report INACTIVE'
[[ ! -e "$KILL_SWITCH_FILE" ]] || fail 'kill-switch deactivation left state file behind'
TRADING_DATA_ROOT="$DATA_ROOT" TRADING_MODE_FILE="$MODE_FILE" \
TRADING_KILL_SWITCH_PATH="$KILL_SWITCH_FILE" PYTHONDONTWRITEBYTECODE=1 \
PYTHONPATH="$BACKEND_ROOT" python3 -c \
  'from kill_switch import read_kill_switch_state; assert read_kill_switch_state().state.value == "INACTIVE"'

[[ -s "$DATA_ROOT/memory/dashboard_mutation_audit.jsonl" ]] \
  || fail 'mutation audit log was not written'
printf 'mode authorization integration: PASS\n'
