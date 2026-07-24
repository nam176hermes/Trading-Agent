#!/usr/bin/env bash
set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1

APP_COMMIT="fdc085a05019d700ccbce59370941e2c97ef899a"
BACKEND_COMMIT="41f055b48033714c660f44cc20498b7545366e75"
APP_ROOT="/opt/trading-agent-phase4/releases/app-$APP_COMMIT"
BACKEND_ROOT="/opt/trading-agent-phase4/releases/backend-$BACKEND_COMMIT"
GLOBAL_USER_UNIT_ROOT="/etc/systemd/user"
GLOBAL_SYSTEM_UNIT_ROOT="/etc/systemd/system"
USER_SHADOW_ROOT="${HOME}/.config/systemd/user"
STANDALONE_VERIFIER_SHA256="8f7cf1bc3161f64e2f9814547c4ccd8a30d67a9bade1268e79767d2e965ca5d5"
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
STANDALONE_VERIFIER="$SCRIPT_DIR/verify-release.py"

fail() {
  printf '%s\n' "phase 4b installed authority verification rejected" >&2
  exit 2
}

[[ $EUID -ne 1000 ]] && fail
REQUIRE_SEMANTIC=false
if [[ ${3:-} == --require-semantic ]]; then
  REQUIRE_SEMANTIC=true
elif [[ $# -ne 2 ]]; then
  fail
fi
[[ $# -eq 2 || $# -eq 3 ]] || fail
STAGING_ROOT=$1
APPROVED_METADATA_SHA256=$2
[[ $STAGING_ROOT == /* && -d $STAGING_ROOT && ! -L $STAGING_ROOT ]] || fail
[[ $APPROVED_METADATA_SHA256 =~ ^[0-9a-f]{64}$ ]] || fail
METADATA="$STAGING_ROOT/staging-metadata.json"
[[ -f $METADATA && ! -L $METADATA ]] || fail
[[ $(sha256sum "$METADATA" | awk '{print $1}') == "$APPROVED_METADATA_SHA256" ]] || fail
[[ -f $STANDALONE_VERIFIER && ! -L $STANDALONE_VERIFIER ]] || fail
[[ $(sha256sum "$STANDALONE_VERIFIER" | awk '{print $1}') == "$STANDALONE_VERIFIER_SHA256" ]] || fail
[[ $(jq -er '.standalone_verifier_sha256' "$METADATA") == "$STANDALONE_VERIFIER_SHA256" ]] || fail
jq -e --arg root "$STAGING_ROOT" '
  (keys == ["application","backend","command_manifest_sha256","runtime_authority_sha256","schema_version","seal_version","staging_gid","staging_root","staging_uid","standalone_verifier_sha256","units"]) and
  (.seal_version == 1) and (.staging_root == $root) and
  (.application | keys == ["commit","manifest_file_sha256","manifest_sha256","python_identity"]) and
  (.backend | keys == ["commit","manifest_file_sha256","manifest_sha256","python_identity"])
' "$METADATA" >/dev/null || fail

APP_DIGEST=$(jq -er '.application.manifest_sha256' "$METADATA")
APP_RAW_DIGEST=$(jq -er '.application.manifest_file_sha256' "$METADATA")
APP_IDENTITY=$(jq -er '.application.python_identity' "$METADATA")
BACKEND_DIGEST=$(jq -er '.backend.manifest_sha256' "$METADATA")
BACKEND_RAW_DIGEST=$(jq -er '.backend.manifest_file_sha256' "$METADATA")
BACKEND_IDENTITY=$(jq -er '.backend.python_identity' "$METADATA")
COMMAND_DIGEST=$(jq -er '.command_manifest_sha256' "$METADATA")
AUTHORITY_DIGEST=$(jq -er '.runtime_authority_sha256' "$METADATA")
[[ $(jq -er '.application.commit' "$METADATA") == "$APP_COMMIT" ]] || fail
[[ $(jq -er '.backend.commit' "$METADATA") == "$BACKEND_COMMIT" ]] || fail

exact_children() {
  local directory=$1
  shift
  local -a expected=("$@") observed=()
  mapfile -t observed < <(find "$directory" -mindepth 1 -maxdepth 1 -printf '%f\n' | LC_ALL=C sort)
  [[ ${#observed[@]} -eq ${#expected[@]} ]] || fail
  local index
  for index in "${!expected[@]}"; do
    [[ ${observed[$index]} == "${expected[$index]}" ]] || fail
  done
}

exact_children /opt/trading-agent-phase4 manifests releases
exact_children /opt/trading-agent-phase4/releases "app-$APP_COMMIT" "backend-$BACKEND_COMMIT"
exact_children /opt/trading-agent-phase4/manifests \
  "app-$APP_COMMIT.manifest.json" "backend-$BACKEND_COMMIT.manifest.json" \
  "commands-$BACKEND_COMMIT.json"
exact_children /etc/trading-agent \
  control-api.env job-api.env job-scheduler.env job-worker.env phase4-runtime-authority.json \
  research-input-manifests

/usr/bin/python3 -I "$STANDALONE_VERIFIER" \
  "$APP_ROOT" "/opt/trading-agent-phase4/manifests/app-$APP_COMMIT.manifest.json" \
  "$APP_DIGEST" "$APP_RAW_DIGEST" --commit "$APP_COMMIT" \
  --python-identity "$APP_IDENTITY" --release-type phase4-app --uid 0 --gid 0 \
  --manifest-mode 0444 >/dev/null || fail
/usr/bin/python3 -I "$STANDALONE_VERIFIER" \
  "$BACKEND_ROOT" "/opt/trading-agent-phase4/manifests/backend-$BACKEND_COMMIT.manifest.json" \
  "$BACKEND_DIGEST" "$BACKEND_RAW_DIGEST" --commit "$BACKEND_COMMIT" \
  --python-identity "$BACKEND_IDENTITY" --release-type phase4-backend --uid 0 --gid 0 \
  --manifest-mode 0444 >/dev/null || fail

for pair in \
  "$STAGING_ROOT/manifests/commands-$BACKEND_COMMIT.json:/opt/trading-agent-phase4/manifests/commands-$BACKEND_COMMIT.json:$COMMAND_DIGEST" \
  "$STAGING_ROOT/manifests/phase4-runtime-authority.json:/etc/trading-agent/phase4-runtime-authority.json:$AUTHORITY_DIGEST"; do
  IFS=: read -r source installed digest <<<"$pair"
  [[ -f $installed && ! -L $installed && $(stat -c %u:%g:%a "$installed") == 0:0:444 ]] || fail
  [[ $(sha256sum "$source" | awk '{print $1}') == "$digest" ]] || fail
  [[ $(sha256sum "$installed" | awk '{print $1}') == "$digest" ]] || fail
  cmp -s "$source" "$installed" || fail
done
[[ -f /etc/trading-agent/research-input-manifests/.phase4-v1.json.lock \
  && ! -L /etc/trading-agent/research-input-manifests/.phase4-v1.json.lock \
  && $(stat -c %u:%g:%a /etc/trading-agent/research-input-manifests/.phase4-v1.json.lock) == 0:0:600 \
  && $(stat -c %h /etc/trading-agent/research-input-manifests/.phase4-v1.json.lock) == 1 ]] || fail

validate_runtime_tree() {
  local root=$1
  [[ -z $(find "$root" -type l -print -quit) ]] || fail
  [[ -z $(find "$root" ! -type f ! -type d -print -quit) ]] || fail
  [[ -z $(find "$root" -type f -links +1 -print -quit) ]] || fail
  [[ -z $(find "$root" -type d \( ! -uid 1000 -o ! -gid 1000 -o ! -perm 0700 -o -perm /7077 \) -print -quit) ]] || fail
  [[ -z $(find "$root" -type f \( ! -uid 1000 -o ! -gid 1000 -o ! -perm 0600 -o -perm /7177 \) -print -quit) ]] || fail
}

SEMANTIC_INPUT_ROOT=/home/thenam176/.local/share/trading-agent/research-input
SEMANTIC_AUTHORITY_ROOT=/etc/trading-agent/research-input-manifests
[[ -d "$SEMANTIC_INPUT_ROOT" && ! -L "$SEMANTIC_INPUT_ROOT" ]] || fail
[[ -d "$SEMANTIC_AUTHORITY_ROOT" && ! -L "$SEMANTIC_AUTHORITY_ROOT" ]] || fail
[[ $(stat -c %u:%g:%a "$SEMANTIC_INPUT_ROOT") == 0:0:711 ]] || fail
[[ $(stat -c %u:%g:%a "$SEMANTIC_AUTHORITY_ROOT") == 0:0:711 ]] || fail

ACTIVE_SEMANTIC="$SEMANTIC_AUTHORITY_ROOT/phase4-v1.json"
if [[ $REQUIRE_SEMANTIC == true ]]; then
  [[ -f $ACTIVE_SEMANTIC && ! -L $ACTIVE_SEMANTIC ]] || fail
  "$APP_ROOT/.venv/bin/python3.11" -I -c \
    'from packages.runtime_release.config import load_runtime_authority; from packages.runtime_release.semantic import attest_current_semantic_inputs; authority = load_runtime_authority(); attest_current_semantic_inputs(authority.semantic, authority.backend.git_commit)' \
    >/dev/null || fail
fi
for env_file in /etc/trading-agent/control-api.env /etc/trading-agent/job-api.env \
  /etc/trading-agent/job-worker.env /etc/trading-agent/job-scheduler.env; do
  [[ -f $env_file && ! -L $env_file && $(stat -c %u:%g:%a "$env_file") == 1000:1000:600 ]] || fail
  [[ $(grep -Ec '^[[:space:]]*TRADING_MODE=' "$env_file" || true) -eq 1 ]] || fail
  grep -qx 'TRADING_MODE=paper' "$env_file" || fail
  [[ $(grep -Ec '^[[:space:]]*LIVE_EXECUTION_ENABLED=' "$env_file" || true) -eq 1 ]] || fail
  grep -qx 'LIVE_EXECUTION_ENABLED=false' "$env_file" || fail
  [[ $(grep -Ec '^[[:space:]]*LIVE_TRADING_APPROVED=' "$env_file" || true) -eq 1 ]] || fail
  grep -qx 'LIVE_TRADING_APPROVED=false' "$env_file" || fail
done
grep -qx 'TRADING_DATABASE_USER=trading_reader' /etc/trading-agent/control-api.env || fail
grep -qx 'TRADING_STORE_BACKEND=postgres' /etc/trading-agent/control-api.env || fail
[[ -z $(grep -E '^TRADING_JOB_API_TOKEN=' /etc/trading-agent/control-api.env || true) ]] || fail
[[ $(stat -c %u:%g:%a /home/thenam176/.local/share/trading-agent/research-input) == 0:0:711 ]] || fail
for path in \
  /home/thenam176/.local/share/trading-agent/job-artifacts \
  /home/thenam176/.local/share/trading-agent/research-output \
  /home/thenam176/.local/share/trading-agent/research-output/reports \
  /home/thenam176/.local/share/trading-agent/research-output/signals \
  /home/thenam176/.local/run/trading-agent \
  /home/thenam176/.local/run/trading-agent/research-home \
  /home/thenam176/.local/run/trading-agent/research-home/scratchpad; do
  [[ -d $path && ! -L $path && $(stat -c %u:%g:%a "$path") == 1000:1000:700 ]] || fail
done
validate_runtime_tree /home/thenam176/.local/share/trading-agent/job-artifacts
validate_runtime_tree /home/thenam176/.local/share/trading-agent/research-output
validate_runtime_tree /home/thenam176/.local/run/trading-agent/research-home

same_canonical_path() {
  [[ $# -eq 2 ]] || return 1
  local reported=$1 expected_path=$2 reported_real expected_real
  [[ -n $reported && $reported == /* && $reported != *$'\n'* ]] || return 1
  [[ -n $expected_path && $expected_path == /* && $expected_path != *$'\n'* ]] || return 1
  reported_real=$(readlink -e -- "$reported") || return 1
  expected_real=$(readlink -e -- "$expected_path") || return 1
  [[ -n $reported_real && -n $expected_real && $reported_real == "$expected_real" ]]
}
# end same_canonical_path

normalize_execstart() {
  [[ $# -eq 1 && $1 != *$'\n'* ]] || return 1
  local serialized=$1 remainder path argv
  [[ $serialized == \{\ path=* ]] || return 1
  remainder=${serialized#\{ path=}
  [[ $remainder == *" ; argv[]="* ]] || return 1
  path=${remainder%%" ; argv[]="*}
  remainder=${remainder#*" ; argv[]="}
  [[ $remainder == *" ; ignore_errors="* ]] || return 1
  argv=${remainder%%" ; ignore_errors="*}
  [[ -n $path && -n $argv ]] || return 1
  printf '%s\n%s\n' "$path" "$argv"
}
# end normalize_execstart

attest_execstart() {
  local serialized=$1 expected_path=$2 expected_argv=$3 signature expected
  signature=$(normalize_execstart "$serialized") || fail
  expected=$(printf '%s\n%s\n' "$expected_path" "$expected_argv")
  [[ $signature == "$expected" ]] || fail
}

declare -A MODULES=(
  [trading-safety-state-export.service]="services.safety_state_exporter.main --once"
  [trading-control-api.service]="control_api.main"
  [trading-job-api.service]="apps.job_api.main"
  [trading-job-worker.service]="services.job_worker.main"
  [trading-job-scheduler.service]="services.job_scheduler.main"
)
USER_UNITS=(
  trading-safety-state-export.service trading-safety-state-export.timer
  trading-control-api.service
  trading-job-api.service trading-job-worker.service
  trading-job-scheduler.service trading-job-scheduler.timer
)
for unit in "${USER_UNITS[@]}"; do
  installed="$GLOBAL_USER_UNIT_ROOT/$unit"
  [[ -f $installed && ! -L $installed && $(stat -c %u:%g:%a "$installed") == 0:0:644 ]] || fail
  [[ $(sha256sum "$installed" | awk '{print $1}') == "$(jq -er --arg unit "$unit" '.units[$unit]' "$METADATA")" ]] || fail
  [[ ! -e $USER_SHADOW_ROOT/$unit && ! -L $USER_SHADOW_ROOT/$unit ]] || fail
  same_canonical_path "$(systemctl --user show "$unit" --property=FragmentPath --value)" "$installed" || fail
  [[ -z $(systemctl --user show "$unit" --property=DropInPaths --value) ]] || fail
done
for unit in "${!MODULES[@]}"; do
  expected_path="$APP_ROOT/.venv/bin/python3.11"
  expected_argv="$expected_path -m ${MODULES[$unit]}"
  attest_execstart \
    "$(systemctl --user show "$unit" --property=ExecStart --value)" \
    "$expected_path" "$expected_argv"
done

SYSTEM_UNITS=(trading-semantic-input-refresh.service trading-semantic-input-refresh.timer)
for unit in "${SYSTEM_UNITS[@]}"; do
  installed="$GLOBAL_SYSTEM_UNIT_ROOT/$unit"
  [[ -f $installed && ! -L $installed && $(stat -c %u:%g:%a "$installed") == 0:0:644 ]] || fail
  [[ $(sha256sum "$installed" | awk '{print $1}') == "$(jq -er --arg unit "$unit" '.units[$unit]' "$METADATA")" ]] || fail
  same_canonical_path "$(systemctl show "$unit" --property=FragmentPath --value)" "$installed" || fail
  [[ -z $(systemctl show "$unit" --property=DropInPaths --value) ]] || fail
done
expected_path="$APP_ROOT/.venv/bin/python3.11"
expected_argv="$expected_path -m services.semantic_input_refresher.main --apply"
attest_execstart \
  "$(systemctl show trading-semantic-input-refresh.service --property=ExecStart --value)" \
  "$expected_path" "$expected_argv"

for timer in trading-job-scheduler.timer trading-safety-state-export.timer; do
  [[ $(systemctl --user is-enabled "$timer" 2>/dev/null || true) == disabled ]] || fail
done
[[ $(systemctl is-enabled trading-semantic-input-refresh.timer 2>/dev/null || true) == disabled ]] || fail

systemd-analyze --user verify "${USER_UNITS[@]/#/$GLOBAL_USER_UNIT_ROOT/}" >/dev/null || fail
systemd-analyze verify "${SYSTEM_UNITS[@]/#/$GLOBAL_SYSTEM_UNIT_ROOT/}" >/dev/null || fail

while read -r listener; do
  [[ $listener == 127.0.0.1:8400 ]] || fail
done < <(ss -ltnH 'sport = :8400' | awk '{print $4}')
while read -r listener; do
  [[ $listener == 127.0.0.1:8401 ]] || fail
done < <(ss -ltnH 'sport = :8401' | awk '{print $4}')

printf '%s\n' "phase 4b installed authority verification passed"
