#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
WORK=$(mktemp -d)
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
  printf 'dashboard security integration: %s\n' "$*" >&2
  exit 1
}

expect_status() {
  local actual=$1
  local expected=$2
  local label=$3
  [[ "$actual" == "$expected" ]] || fail "$label returned HTTP $actual (expected $expected)"
}

grep -q 'node tests/run-test-inventory.mjs' "$ROOT/package.json" \
  || fail 'npm test does not run the canonical dashboard test inventory'
node "$ROOT/tests/run-test-inventory.mjs" --list-json \
  | grep -q 'tests/dashboard-security.integration.sh' \
  || fail 'canonical dashboard test inventory omits this integration smoke'

PORT=$(node -e '
  const net = require("node:net");
  const server = net.createServer();
  server.listen(0, "127.0.0.1", () => {
    process.stdout.write(String(server.address().port));
    server.close();
  });
')
BASE_URL="http://127.0.0.1:${PORT}"
APP="$ROOT"
HOME_FIXTURE="$WORK/home"
RESEARCH_FIXTURE="$WORK/crypto-research"
COOKIE_JAR="$WORK/cookies.txt"
ADMIN_COOKIE_JAR="$WORK/admin-cookies.txt"
SERVER_LOG="$WORK/server.log"

mkdir -p "$HOME_FIXTURE" "$RESEARCH_FIXTURE"
printf 'paper\n' > "$RESEARCH_FIXTURE/.mode"

if ! (
  cd "$APP"
  HOME="$HOME_FIXTURE" \
  TRADING_DATA_ROOT="$RESEARCH_FIXTURE" \
  TRADING_MASTER_KEY='fixture-master-key' \
  NEXT_TELEMETRY_DISABLED=1 \
    ./node_modules/.bin/next build >"$WORK/build.log" 2>&1
); then
  sed -n '1,240p' "$WORK/build.log" >&2
  fail 'isolated dashboard build failed'
fi

(
  cd "$APP"
  exec env \
    HOME="$HOME_FIXTURE" \
    TRADING_DATA_ROOT="$RESEARCH_FIXTURE" \
    TRADING_KILL_SWITCH_PATH="$WORK/kill-switch" \
    TRADING_MASTER_KEY='fixture-master-key' \
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
  "$BASE_URL/api/trading/mode")
expect_status "$unauthenticated_status" 401 'unauthenticated trading GET'
grep -q '"code":"UNAUTHORIZED"' "$WORK/unauthenticated.json" \
  || fail 'unauthenticated trading GET did not return the structured authorization error'

login_status=$(curl -sS -o "$WORK/login.json" -D "$WORK/login.headers" -c "$COOKIE_JAR" \
  -w '%{http_code}' -X POST "$BASE_URL/api/auth/session" \
  -H 'content-type: application/json' \
  -H 'cf-connecting-ip: 198.51.100.10' \
  -H 'x-trusted-proxy-secret: fixture-trusted-proxy-secret' \
  --data '{"password":"fixture-reader-password"}')
expect_status "$login_status" 200 'reader login'
if ! tr -d '\r' < "$WORK/login.headers" \
  | grep -Eqi '^set-cookie: trading_session=.*; HttpOnly; SameSite=Strict(; Secure)?$'; then
  sed -n '1,80p' "$WORK/login.headers" >&2
  fail 'reader login cookie is missing HttpOnly or SameSite=Strict'
fi
grep -q '"authenticated":true' "$WORK/login.json" \
  || fail 'reader login did not report authentication success'

SESSION_TOKEN=$(awk '$0 !~ /^#/ && $6 == "trading_session" { print $7 } /^#HttpOnly_/ && $6 == "trading_session" { print $7 }' "$COOKIE_JAR" | tail -n 1)
[[ -n "$SESSION_TOKEN" ]] || fail 'curl cookie jar did not capture the session token'
COOKIE_HEADER="trading_session=$SESSION_TOKEN"

reader_get_status=$(curl -sS -o "$WORK/reader-get.json" -w '%{http_code}' \
  "$BASE_URL/api/trading/mode" \
  -H "cookie: $COOKIE_HEADER")
expect_status "$reader_get_status" 503 'reader trading GET without Control API'
grep -q '"code":"CONTROL_API_UNAVAILABLE"' "$WORK/reader-get.json" \
  || fail 'reader trading GET did not fail closed without the canonical read service'

reader_mutation_status=$(curl -sS -o "$WORK/reader-mutation.json" -w '%{http_code}' \
  -X POST "$BASE_URL/api/trading/mode" \
  -H "cookie: $COOKIE_HEADER" \
  -H "origin: $BASE_URL" \
  -H 'content-type: application/json' \
  --data '{"mode":"paper"}')
expect_status "$reader_mutation_status" 403 'reader trading mutation'
grep -q '"code":"FORBIDDEN"' "$WORK/reader-mutation.json" \
  || fail 'reader mutation did not return the structured authorization error'

admin_login_status=$(curl -sS -o "$WORK/admin-login.json" -c "$ADMIN_COOKIE_JAR" \
  -w '%{http_code}' -X POST "$BASE_URL/api/auth/session" \
  -H 'content-type: application/json' \
  -H 'cf-connecting-ip: 198.51.100.30' \
  --data '{"password":"fixture-admin-password"}')
expect_status "$admin_login_status" 200 'admin login'

admin_keys_status=$(curl -sS -o "$WORK/admin-keys.json" -w '%{http_code}' \
  -b "$ADMIN_COOKIE_JAR" "$BASE_URL/api/trading/keys")
expect_status "$admin_keys_status" 503 'disabled admin key listing'
grep -q '"code":"PROCESS_EXECUTION_DISABLED"' "$WORK/admin-keys.json" \
  || fail 'admin key listing did not return the typed disabled contract'

rate_limited=false
for attempt in $(seq 1 6); do
  invalid_status=$(curl -sS -o "$WORK/invalid-login-${attempt}.json" -D "$WORK/invalid-login-${attempt}.headers" \
    -w '%{http_code}' -X POST "$BASE_URL/api/auth/session" \
    -H 'content-type: application/json' \
    -H 'cf-connecting-ip: 198.51.100.20' \
    --data '{"password":"invalid-fixture-password"}')
  if [[ "$invalid_status" == '429' ]]; then
    tr -d '\r' < "$WORK/invalid-login-${attempt}.headers" \
      | grep -Eqi '^retry-after: [1-9][0-9]*$' \
      || fail 'rate-limited login did not include Retry-After'
    rate_limited=true
    break
  fi
  expect_status "$invalid_status" 401 "invalid login attempt $attempt"
done
[[ "$rate_limited" == true ]] || fail 'invalid logins did not reach HTTP 429'

[[ $(<"$RESEARCH_FIXTURE/.mode") == 'paper' ]] \
  || fail 'security smoke changed the fixture trading mode'
[[ ! -e "$WORK/kill-switch" ]] \
  || fail 'security smoke activated the fixture kill switch'

printf 'dashboard security integration: PASS\n'
