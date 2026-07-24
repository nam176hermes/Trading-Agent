#!/usr/bin/env bash
set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1

APP_COMMIT="fdc085a05019d700ccbce59370941e2c97ef899a"
BACKEND_COMMIT="41f055b48033714c660f44cc20498b7545366e75"
RUNTIME_UID=1000
RUNTIME_GID=1000
JOBS_DATABASE_ENV_SOURCE="/home/thenam176/.config/trading-agent/postgres-jobs.env"
READER_DATABASE_ENV_SOURCE="/home/thenam176/.config/trading-agent/postgres-reader.env"
DESTINATION_ROOT=""
STANDALONE_VERIFIER_SHA256="8f7cf1bc3161f64e2f9814547c4ccd8a30d67a9bade1268e79767d2e965ca5d5"
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
STANDALONE_VERIFIER="$SCRIPT_DIR/verify-release.py"

fail() {
  printf '%s\n' "phase 4b root provisioning rejected" >&2
  exit 2
}

if [[ $EUID -ne 0 ]]; then
  printf '%s\n' "phase 4b root provisioning requires EUID 0" >&2
  exit 2
fi
destination() {
  printf '%s%s' "$DESTINATION_ROOT" "$1"
}

[[ $# -eq 2 ]] || fail
STAGING_ROOT=$1
APPROVED_METADATA_SHA256=$2
[[ $STAGING_ROOT == /* && $APPROVED_METADATA_SHA256 =~ ^[0-9a-f]{64}$ ]] || fail
[[ -d $STAGING_ROOT && ! -L $STAGING_ROOT ]] || fail
command -v jq >/dev/null || fail
[[ -f $STANDALONE_VERIFIER && ! -L $STANDALONE_VERIFIER ]] || fail

PENDING=""
PENDING_FILE=""
TEMP_ENV=""
TEMP_DB_DIR=""
TRUSTED_ROOT=""
TRUSTED_BASE=""
TRUSTED_BASE_CREATED=false
PROVISIONING_SUCCEEDED=false
declare -a CREATED_TARGETS=()
cleanup_pending() {
  local index target
  if [[ $PROVISIONING_SUCCEEDED == false ]]; then
    for ((index=${#CREATED_TARGETS[@]} - 1; index >= 0; index--)); do
      target=${CREATED_TARGETS[$index]}
      if [[ -d $target && ! -L $target ]]; then
        find "$target" -depth -delete
      elif [[ -e $target || -L $target ]]; then
        find "$target" -maxdepth 0 -delete
      fi
    done
  fi
  if [[ -n $PENDING && -d $PENDING && ! -L $PENDING ]]; then
    find "$PENDING" -depth -delete
  fi
  if [[ -n $PENDING_FILE && -f $PENDING_FILE && ! -L $PENDING_FILE ]]; then
    find "$PENDING_FILE" -delete
  fi
  if [[ -n $TEMP_ENV && -f $TEMP_ENV && ! -L $TEMP_ENV ]]; then
    find "$TEMP_ENV" -delete
  fi
  if [[ -n $TEMP_DB_DIR && -d $TEMP_DB_DIR && ! -L $TEMP_DB_DIR ]]; then
    find "$TEMP_DB_DIR" -depth -delete
  fi
  if [[ -n $TRUSTED_ROOT && -d $TRUSTED_ROOT && ! -L $TRUSTED_ROOT ]]; then
    find "$TRUSTED_ROOT" -depth -delete
  fi
  if [[ $TRUSTED_BASE_CREATED == true && -d $TRUSTED_BASE && ! -L $TRUSTED_BASE ]]; then
    find "$TRUSTED_BASE" -maxdepth 0 -type d -empty -delete
  fi
}
trap cleanup_pending EXIT

# Freeze every user-controlled authority into one root-owned private snapshot.
# All verification and publication below reads only from this snapshot.
TRUSTED_BASE=$(destination /var)
if [[ -e $TRUSTED_BASE || -L $TRUSTED_BASE ]]; then
  [[ -d $TRUSTED_BASE && ! -L $TRUSTED_BASE ]] || fail
  [[ $(stat -c %u:%g:%a "$TRUSTED_BASE") == 0:0:755 ]] || fail
else
  install -d -o 0 -g 0 -m 0755 "$TRUSTED_BASE"
  TRUSTED_BASE_CREATED=true
fi
TRUSTED_ROOT=$(mktemp -d "$TRUSTED_BASE/trading-agent-phase4-provision.XXXXXX")
chmod 0700 "$TRUSTED_ROOT"
mkdir -m 0700 "$TRUSTED_ROOT/staging"
cp --archive --no-dereference --reflink=auto "$STAGING_ROOT/." "$TRUSTED_ROOT/staging/"
# cp --archive propagates the user-owned source directory metadata onto this boundary.
chown 0:0 "$TRUSTED_ROOT/staging"
install -o 0 -g 0 -m 0600 "$STANDALONE_VERIFIER" "$TRUSTED_ROOT/verify-release.py"
TRUSTED_STAGING_ROOT="$TRUSTED_ROOT/staging"
STANDALONE_VERIFIER="$TRUSTED_ROOT/verify-release.py"
METADATA="$TRUSTED_STAGING_ROOT/staging-metadata.json"
[[ -f $METADATA && ! -L $METADATA ]] || fail
[[ $(sha256sum "$METADATA" | awk '{print $1}') == "$APPROVED_METADATA_SHA256" ]] || fail
[[ $(sha256sum "$STANDALONE_VERIFIER" | awk '{print $1}') == "$STANDALONE_VERIFIER_SHA256" ]] || fail
jq -e --arg root "$STAGING_ROOT" '
  (keys == ["application","backend","command_manifest_sha256","runtime_authority_sha256","schema_version","seal_version","staging_gid","staging_root","staging_uid","standalone_verifier_sha256","units"]) and
  (.seal_version == 1) and (.staging_root == $root) and
  (.application | keys == ["commit","manifest_file_sha256","manifest_sha256","python_identity"]) and
  (.backend | keys == ["commit","manifest_file_sha256","manifest_sha256","python_identity"]) and
  (.units | keys == ["trading-control-api.service","trading-job-api.service","trading-job-scheduler.service","trading-job-scheduler.timer","trading-job-worker.service","trading-safety-state-export.service","trading-safety-state-export.timer","trading-semantic-input-refresh.service","trading-semantic-input-refresh.timer"])
' "$METADATA" >/dev/null || fail

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
exact_children "$TRUSTED_STAGING_ROOT" manifests releases staging-metadata.json units
exact_children "$TRUSTED_STAGING_ROOT/releases" "app-$APP_COMMIT" "backend-$BACKEND_COMMIT"
exact_children "$TRUSTED_STAGING_ROOT/manifests" \
  "app-$APP_COMMIT.manifest.json" "backend-$BACKEND_COMMIT.manifest.json" \
  "commands-$BACKEND_COMMIT.json" phase4-runtime-authority.json
exact_children "$TRUSTED_STAGING_ROOT/units" \
  trading-control-api.service trading-job-api.service trading-job-scheduler.service trading-job-scheduler.timer \
  trading-job-worker.service trading-safety-state-export.service \
  trading-safety-state-export.timer trading-semantic-input-refresh.service \
  trading-semantic-input-refresh.timer

STAGING_UID=$(jq -er '.staging_uid' "$METADATA")
STAGING_GID=$(jq -er '.staging_gid' "$METADATA")
[[ $STAGING_UID -eq 1000 && $STAGING_GID -eq 1000 ]] || fail
[[ $(stat -c %u "$STAGING_ROOT") -eq $STAGING_UID ]] || { printf '%s\n' "wrong staging owner" >&2; exit 2; }
[[ $(stat -c %a "$STAGING_ROOT") == 700 ]] || { printf '%s\n' "wrong staging mode" >&2; exit 2; }
[[ $(stat -c %u:%g:%a "$TRUSTED_STAGING_ROOT") == 0:0:700 ]] || fail
[[ -z $(find "$TRUSTED_STAGING_ROOT" -type l -print -quit) ]] || fail
[[ -z $(find "$TRUSTED_STAGING_ROOT" ! -type f ! -type d -print -quit) ]] || fail
[[ -z $(find "$TRUSTED_STAGING_ROOT" -type f -links +1 -print -quit) ]] || fail
[[ -z $(find "$TRUSTED_STAGING_ROOT" -mindepth 1 \( ! -uid "$STAGING_UID" -o ! -gid "$STAGING_GID" \) -print -quit) ]] \
  || { printf '%s\n' "wrong staging owner" >&2; exit 2; }
[[ -z $(find "$TRUSTED_STAGING_ROOT" -perm /0022 -print -quit) ]] \
  || { printf '%s\n' "wrong staging mode" >&2; exit 2; }

[[ $(jq -er '.schema_version' "$METADATA") == 1 ]] || fail
[[ $(jq -er '.application.commit' "$METADATA") == "$APP_COMMIT" ]] || fail
[[ $(jq -er '.backend.commit' "$METADATA") == "$BACKEND_COMMIT" ]] || fail
APP_DIGEST=$(jq -er '.application.manifest_sha256' "$METADATA")
BACKEND_DIGEST=$(jq -er '.backend.manifest_sha256' "$METADATA")
APP_MANIFEST_FILE_SHA256=$(jq -er '.application.manifest_file_sha256' "$METADATA")
BACKEND_MANIFEST_FILE_SHA256=$(jq -er '.backend.manifest_file_sha256' "$METADATA")
APP_IDENTITY=$(jq -er '.application.python_identity' "$METADATA")
BACKEND_IDENTITY=$(jq -er '.backend.python_identity' "$METADATA")
COMMAND_DIGEST=$(jq -er '.command_manifest_sha256' "$METADATA")
AUTHORITY_DIGEST=$(jq -er '.runtime_authority_sha256' "$METADATA")
[[ $(jq -er '.standalone_verifier_sha256' "$METADATA") == "$STANDALONE_VERIFIER_SHA256" ]] || fail
for digest in "$APP_DIGEST" "$BACKEND_DIGEST" "$APP_MANIFEST_FILE_SHA256" \
  "$BACKEND_MANIFEST_FILE_SHA256" "$COMMAND_DIGEST" "$AUTHORITY_DIGEST"; do
  [[ $digest =~ ^[0-9a-f]{64}$ ]] || fail
done
[[ $APP_IDENTITY =~ ^CPython\ 3\.11\.[0-9]+$ && $BACKEND_IDENTITY =~ ^CPython\ 3\.11\.[0-9]+$ ]] || fail

APP_STAGE="$TRUSTED_STAGING_ROOT/releases/app-$APP_COMMIT"
BACKEND_STAGE="$TRUSTED_STAGING_ROOT/releases/backend-$BACKEND_COMMIT"
APP_MANIFEST_STAGE="$TRUSTED_STAGING_ROOT/manifests/app-$APP_COMMIT.manifest.json"
BACKEND_MANIFEST_STAGE="$TRUSTED_STAGING_ROOT/manifests/backend-$BACKEND_COMMIT.manifest.json"
COMMAND_STAGE="$TRUSTED_STAGING_ROOT/manifests/commands-$BACKEND_COMMIT.json"
AUTHORITY_STAGE="$TRUSTED_STAGING_ROOT/manifests/phase4-runtime-authority.json"
[[ $(sha256sum "$APP_MANIFEST_STAGE" | awk '{print $1}') == "$APP_MANIFEST_FILE_SHA256" ]] || fail
[[ $(sha256sum "$BACKEND_MANIFEST_STAGE" | awk '{print $1}') == "$BACKEND_MANIFEST_FILE_SHA256" ]] || fail
[[ $(sha256sum "$COMMAND_STAGE" | awk '{print $1}') == "$COMMAND_DIGEST" ]] || fail
[[ $(sha256sum "$AUTHORITY_STAGE" | awk '{print $1}') == "$AUTHORITY_DIGEST" ]] || fail
/usr/bin/python3 -I "$STANDALONE_VERIFIER" \
  "$APP_STAGE" "$APP_MANIFEST_STAGE" "$APP_DIGEST" "$APP_MANIFEST_FILE_SHA256" \
  --commit "$APP_COMMIT" --python-identity "$APP_IDENTITY" --release-type phase4-app \
  --uid "$STAGING_UID" --gid "$STAGING_GID" --manifest-mode 0644 >/dev/null \
  || { printf '%s\n' "release verification failed" >&2; exit 2; }
/usr/bin/python3 -I "$STANDALONE_VERIFIER" \
  "$BACKEND_STAGE" "$BACKEND_MANIFEST_STAGE" "$BACKEND_DIGEST" "$BACKEND_MANIFEST_FILE_SHA256" \
  --commit "$BACKEND_COMMIT" --python-identity "$BACKEND_IDENTITY" --release-type phase4-backend \
  --uid "$STAGING_UID" --gid "$STAGING_GID" --manifest-mode 0644 >/dev/null \
  || { printf '%s\n' "release verification failed" >&2; exit 2; }

declare -A UNIT_SHA256=(
  [trading-control-api.service]=22be1484597b3406988670043eb2e40d4a05bc0bcade4f5503990eb94c98179a
  [trading-job-api.service]=8d7b368dfeecf654699246dadacc5d0b18d2bdec7fc1a1d7734c62e6e5f144ce
  [trading-job-scheduler.service]=23e0e64399c7d5d4d8236ae6665e2fca55dcf4ec7c3ab156cac91086c8c7fb60
  [trading-job-scheduler.timer]=cc4969564b79c6a45a22b2194a4875620e094918f7020fa96eafe8714515ad89
  [trading-job-worker.service]=3b5514160c1ae80c4e20eb5a98672e889a4c8d3dd9505dc6728efe511be722ea
  [trading-safety-state-export.service]=b573d42f0d1fc2d9bb9eacdcd2fc7101ecb5ed20bc4c4df557edeb4586161d0c
  [trading-safety-state-export.timer]=2780fe42c6e8705d3eb1df5310ec15167129aa7b16f1e762f58e8db25794f375
  [trading-semantic-input-refresh.service]=4f2b5394464bb628d2eaa7e8c7c2d3206f2d1f5ed5588dc84c169f9f0c954c2e
  [trading-semantic-input-refresh.timer]=a1550940d63e418bc62282ab2372a638fcbcb7ca224b1cda3760bc459bc402ad
)
[[ $(find "$TRUSTED_STAGING_ROOT/units" -mindepth 1 -maxdepth 1 -type f | wc -l) -eq ${#UNIT_SHA256[@]} ]] || fail
for unit in "${!UNIT_SHA256[@]}"; do
  [[ $(jq -er --arg unit "$unit" '.units[$unit]' "$METADATA") == "${UNIT_SHA256[$unit]}" ]] || fail
  [[ $(sha256sum "$TRUSTED_STAGING_ROOT/units/$unit" | awk '{print $1}') == "${UNIT_SHA256[$unit]}" ]] || fail
done

USER_SHADOW_ROOT=$(destination /home/thenam176/.config/systemd/user)
for unit in trading-safety-state-export.service trading-safety-state-export.timer \
  trading-control-api.service trading-job-api.service trading-job-worker.service trading-job-scheduler.service \
  trading-job-scheduler.timer; do
  [[ ! -e $USER_SHADOW_ROOT/$unit && ! -L $USER_SHADOW_ROOT/$unit ]] || fail
done

ensure_directory() {
  local path=$1 owner=$2 group=$3 mode=$4
  if [[ -e $path || -L $path ]]; then
    [[ -d $path && ! -L $path ]] || fail
    [[ $(stat -c %u:%g:%a "$path") == "$owner:$group:$mode" ]] || fail
  else
    install -d -o "$owner" -g "$group" -m "$mode" "$path"
  fi
}

install_exact_file() {
  local source=$1 target=$2 owner=$3 group=$4 mode=$5
  local temporary
  if [[ -e $target || -L $target ]]; then
    [[ -f $target && ! -L $target ]] || fail
    [[ $(stat -c %u:%g:%a "$target") == "$owner:$group:$mode" ]] || fail
    cmp -s "$source" "$target" || fail
  else
    temporary=$(mktemp "${target}.installing.XXXXXX")
    PENDING_FILE=$temporary
    install -o "$owner" -g "$group" -m "$mode" "$source" "$temporary"
    [[ -f $temporary && ! -L $temporary ]] || fail
    [[ $(stat -c %u:%g:%a "$temporary") == "$owner:$group:$mode" ]] || fail
    cmp -s "$source" "$temporary" || fail
    CREATED_TARGETS+=("$target")
    mv "$temporary" "$target"
    PENDING_FILE=""
  fi
}

TEMP_DB_DIR=$(mktemp -d)
chmod 0700 "$TEMP_DB_DIR"
canonicalize_database_env() {
  local source=$1 snapshot=$2 expected_role=$3
  local line key value canonical
  local -a required=(
    TRADING_DATABASE_HOST TRADING_DATABASE_PORT TRADING_DATABASE_NAME
    TRADING_DATABASE_USER TRADING_DATABASE_PASSWORD
  )
  local -a optional=(
    TRADING_DB_POOL_MIN TRADING_DB_POOL_MAX TRADING_DB_STATEMENT_TIMEOUT_MS
  )
  local -A values=()

  cp --archive --no-dereference "$source" "$snapshot"
  [[ -f $snapshot && ! -L $snapshot ]] || fail
  [[ $(stat -c %u:%g:%a "$snapshot") == "$RUNTIME_UID:$RUNTIME_GID:600" ]] || fail
  while IFS= read -r line || [[ -n $line ]]; do
    [[ $line =~ ^([A-Z0-9_]+)=([^[:space:]]+)$ ]] || fail
    key=${BASH_REMATCH[1]}
    value=${BASH_REMATCH[2]}
    [[ ${#value} -le 512 && -z ${values[$key]+present} ]] || fail
    case "$key" in
      TRADING_DATABASE_HOST|TRADING_DATABASE_PORT|TRADING_DATABASE_NAME|TRADING_DATABASE_USER|TRADING_DATABASE_PASSWORD|TRADING_DB_POOL_MIN|TRADING_DB_POOL_MAX|TRADING_DB_STATEMENT_TIMEOUT_MS) ;;
      *) fail ;;
    esac
    values[$key]=$value
  done <"$snapshot"
  for key in "${required[@]}"; do
    [[ -n ${values[$key]+present} ]] || fail
  done
  [[ ${values[TRADING_DATABASE_HOST]} == 127.0.0.1 ]] || fail
  [[ ${values[TRADING_DATABASE_PORT]} =~ ^[0-9]{1,5}$ ]] || fail
  (( 10#${values[TRADING_DATABASE_PORT]} >= 1 && 10#${values[TRADING_DATABASE_PORT]} <= 65535 )) || fail
  [[ ${values[TRADING_DATABASE_NAME]} =~ ^[A-Za-z_][A-Za-z0-9_]{0,62}$ ]] || fail
  [[ ${values[TRADING_DATABASE_USER]} == "$expected_role" ]] || fail
  [[ -n ${values[TRADING_DATABASE_PASSWORD]} ]] || fail
  for key in "${optional[@]}"; do
    if [[ -n ${values[$key]+present} ]]; then
      [[ ${values[$key]} =~ ^[0-9]{1,10}$ ]] || fail
    fi
  done

  canonical=$(mktemp "$TEMP_DB_DIR/canonical.XXXXXX")
  chmod 0600 "$canonical"
  for key in "${required[@]}" "${optional[@]}"; do
    if [[ -n ${values[$key]+present} ]]; then
      printf '%s=%s\n' "$key" "${values[$key]}" >>"$canonical"
    fi
  done
  chown "$RUNTIME_UID:$RUNTIME_GID" "$canonical"
  mv "$canonical" "$snapshot"
}

JOBS_DATABASE_ENV_SNAPSHOT="$TEMP_DB_DIR/postgres-jobs.env"
READER_DATABASE_ENV_SNAPSHOT="$TEMP_DB_DIR/postgres-reader.env"
canonicalize_database_env "$JOBS_DATABASE_ENV_SOURCE" "$JOBS_DATABASE_ENV_SNAPSHOT" trading_jobs
canonicalize_database_env "$READER_DATABASE_ENV_SOURCE" "$READER_DATABASE_ENV_SNAPSHOT" trading_reader

install_release() {
  local source=$1 target=$2 manifest=$3 digest=$4 raw_digest=$5 identity=$6 release_type=$7 commit=$8
  if [[ ! -e $target && ! -L $target ]]; then
    PENDING="${target}.installing.$$"
    [[ ! -e $PENDING && ! -L $PENDING ]] || fail
    cp --archive --reflink=auto "$source" "$PENDING"
    chown -R 0:0 "$PENDING"
    /usr/bin/python3 -I "$STANDALONE_VERIFIER" \
      "$PENDING" "$manifest" "$digest" "$raw_digest" --commit "$commit" \
      --python-identity "$identity" --release-type "$release_type" --uid 0 --gid 0 \
      --manifest-mode 0444 >/dev/null || fail
    CREATED_TARGETS+=("$target")
    mv "$PENDING" "$target"
    PENDING=""
  fi
  [[ -d $target && ! -L $target ]] || fail
}

OPT_ROOT=$(destination /opt/trading-agent-phase4)
ETC_ROOT=$(destination /etc/trading-agent)
GLOBAL_USER_UNITS=$(destination /etc/systemd/user)
GLOBAL_SYSTEM_UNITS=$(destination /etc/systemd/system)
SHARE_ROOT=$(destination /home/thenam176/.local/share/trading-agent)
RUN_ROOT=$(destination /home/thenam176/.local/run/trading-agent)

AUTHORITY_ALREADY_INSTALLED=false
if [[ -d $OPT_ROOT/releases/app-$APP_COMMIT && ! -L $OPT_ROOT/releases/app-$APP_COMMIT \
  && -d $OPT_ROOT/releases/backend-$BACKEND_COMMIT && ! -L $OPT_ROOT/releases/backend-$BACKEND_COMMIT \
  && -f $OPT_ROOT/manifests/commands-$BACKEND_COMMIT.json \
  && -f $ETC_ROOT/phase4-runtime-authority.json ]]; then
  AUTHORITY_ALREADY_INSTALLED=true
fi

[[ ! -e $OPT_ROOT/current && ! -L $OPT_ROOT/current ]] || fail
ensure_directory "$OPT_ROOT" 0 0 755
ensure_directory "$OPT_ROOT/releases" 0 0 755
ensure_directory "$OPT_ROOT/manifests" 0 0 755
install_exact_file "$APP_MANIFEST_STAGE" "$OPT_ROOT/manifests/app-$APP_COMMIT.manifest.json" 0 0 444
install_exact_file "$BACKEND_MANIFEST_STAGE" "$OPT_ROOT/manifests/backend-$BACKEND_COMMIT.manifest.json" 0 0 444
install_release "$APP_STAGE" "$OPT_ROOT/releases/app-$APP_COMMIT" \
  "$OPT_ROOT/manifests/app-$APP_COMMIT.manifest.json" "$APP_DIGEST" \
  "$APP_MANIFEST_FILE_SHA256" "$APP_IDENTITY" phase4-app "$APP_COMMIT"
install_release "$BACKEND_STAGE" "$OPT_ROOT/releases/backend-$BACKEND_COMMIT" \
  "$OPT_ROOT/manifests/backend-$BACKEND_COMMIT.manifest.json" "$BACKEND_DIGEST" \
  "$BACKEND_MANIFEST_FILE_SHA256" "$BACKEND_IDENTITY" phase4-backend "$BACKEND_COMMIT"
/usr/bin/python3 -I "$STANDALONE_VERIFIER" \
  "$OPT_ROOT/releases/app-$APP_COMMIT" "$OPT_ROOT/manifests/app-$APP_COMMIT.manifest.json" \
  "$APP_DIGEST" "$APP_MANIFEST_FILE_SHA256" --commit "$APP_COMMIT" \
  --python-identity "$APP_IDENTITY" --release-type phase4-app --uid 0 --gid 0 \
  --manifest-mode 0444 >/dev/null || fail
/usr/bin/python3 -I "$STANDALONE_VERIFIER" \
  "$OPT_ROOT/releases/backend-$BACKEND_COMMIT" "$OPT_ROOT/manifests/backend-$BACKEND_COMMIT.manifest.json" \
  "$BACKEND_DIGEST" "$BACKEND_MANIFEST_FILE_SHA256" --commit "$BACKEND_COMMIT" \
  --python-identity "$BACKEND_IDENTITY" --release-type phase4-backend --uid 0 --gid 0 \
  --manifest-mode 0444 >/dev/null || fail
install_exact_file "$COMMAND_STAGE" "$OPT_ROOT/manifests/commands-$BACKEND_COMMIT.json" 0 0 444
[[ $(sha256sum "$OPT_ROOT/manifests/commands-$BACKEND_COMMIT.json" | awk '{print $1}') == "$COMMAND_DIGEST" ]] || fail

ensure_directory "$ETC_ROOT" 0 0 755
ensure_directory "$ETC_ROOT/research-input-manifests" 0 0 711
install_exact_file "$AUTHORITY_STAGE" "$ETC_ROOT/phase4-runtime-authority.json" 0 0 444
[[ $(sha256sum "$ETC_ROOT/phase4-runtime-authority.json" | awk '{print $1}') == "$AUTHORITY_DIGEST" ]] || fail
LOCK="$ETC_ROOT/research-input-manifests/.phase4-v1.json.lock"
if [[ ! -e $LOCK && ! -L $LOCK ]]; then
  install_exact_file /dev/null "$LOCK" 0 0 600
else
  [[ -f $LOCK && ! -L $LOCK && $(stat -c %u:%g:%a "$LOCK") == 0:0:600 ]] || fail
fi

ensure_directory "$SHARE_ROOT/research-input" 0 0 711
ensure_directory "$SHARE_ROOT/job-artifacts" "$RUNTIME_UID" "$RUNTIME_GID" 700
ensure_directory "$SHARE_ROOT/research-output" "$RUNTIME_UID" "$RUNTIME_GID" 700
ensure_directory "$SHARE_ROOT/research-output/reports" "$RUNTIME_UID" "$RUNTIME_GID" 700
ensure_directory "$SHARE_ROOT/research-output/signals" "$RUNTIME_UID" "$RUNTIME_GID" 700
ensure_directory "$RUN_ROOT" "$RUNTIME_UID" "$RUNTIME_GID" 700
ensure_directory "$RUN_ROOT/research-home" "$RUNTIME_UID" "$RUNTIME_GID" 700
ensure_directory "$RUN_ROOT/research-home/scratchpad" "$RUNTIME_UID" "$RUNTIME_GID" 700

validate_runtime_tree() {
  local root=$1
  [[ -z $(find "$root" -type l -print -quit) ]] || fail
  [[ -z $(find "$root" ! -type f ! -type d -print -quit) ]] || fail
  [[ -z $(find "$root" -type f -links +1 -print -quit) ]] || fail
  [[ -z $(find "$root" -type d \( ! -uid "$RUNTIME_UID" -o ! -gid "$RUNTIME_GID" -o ! -perm 0700 -o -perm /7077 \) -print -quit) ]] || fail
  [[ -z $(find "$root" -type f \( ! -uid "$RUNTIME_UID" -o ! -gid "$RUNTIME_GID" -o ! -perm 0600 -o -perm /7177 \) -print -quit) ]] || fail
}

validate_semantic_tree() {
  local input_root=$1 authority_root=$2 lock=$3
  [[ $(stat -c %u:%g:%a "$input_root") == 0:0:711 ]] || fail
  [[ $(stat -c %u:%g:%a "$authority_root") == 0:0:711 ]] || fail
  [[ -f $lock && ! -L $lock && $(stat -c %u:%g:%a "$lock") == 0:0:600 ]] || fail
  [[ -z $(find "$input_root" \( -type l -o ! -type f ! -type d \) -print -quit) ]] || fail
  [[ -z $(find "$input_root" -type f -links +1 -print -quit) ]] || fail
  [[ -z $(find "$input_root" -mindepth 1 -type d \( ! -uid "$RUNTIME_UID" -o ! -gid "$RUNTIME_GID" -o ! -perm 0500 -o -perm /7277 \) -print -quit) ]] || fail
  [[ -z $(find "$input_root" -type f \( ! -uid "$RUNTIME_UID" -o ! -gid "$RUNTIME_GID" -o ! -perm 0400 -o -perm /7377 \) -print -quit) ]] || fail
  [[ -z $(find "$authority_root" -mindepth 1 -type d -print -quit) ]] || fail
  [[ -z $(find "$authority_root" \( -type l -o ! -type f ! -type d \) -print -quit) ]] || fail
  [[ -z $(find "$authority_root" -type f -links +1 -print -quit) ]] || fail
  [[ -z $(find "$authority_root" -type f ! -name '.phase4-v1.json.lock' ! -name 'phase4-v1*.json' -print -quit) ]] || fail
  [[ -z $(find "$authority_root" -type f ! -name '.phase4-v1.json.lock' \( ! -uid 0 -o ! -gid 0 -o ! -perm 0444 -o -perm /7333 \) -print -quit) ]] || fail
}

if [[ $AUTHORITY_ALREADY_INSTALLED == false ]]; then
  exact_children "$SHARE_ROOT/research-input"
  exact_children "$SHARE_ROOT/job-artifacts"
  exact_children "$SHARE_ROOT/research-output" reports signals
  exact_children "$SHARE_ROOT/research-output/reports"
  exact_children "$SHARE_ROOT/research-output/signals"
  exact_children "$RUN_ROOT/research-home" scratchpad
  exact_children "$RUN_ROOT/research-home/scratchpad"
fi
validate_semantic_tree "$SHARE_ROOT/research-input" \
  "$ETC_ROOT/research-input-manifests" "$LOCK"
validate_runtime_tree "$SHARE_ROOT/job-artifacts"
validate_runtime_tree "$SHARE_ROOT/research-output"
validate_runtime_tree "$RUN_ROOT/research-home"

make_env() {
  local target=$1 kind=$2 token="" database_snapshot="$JOBS_DATABASE_ENV_SNAPSHOT"
  local temporary
  if [[ $kind == control ]]; then
    database_snapshot=$READER_DATABASE_ENV_SNAPSHOT
  fi
  temporary=$(mktemp)
  TEMP_ENV=$temporary
  chmod 0600 "$temporary"
  if [[ $kind == api ]]; then
    if [[ -f $target && ! -L $target ]]; then
      token=$(sed -n 's/^TRADING_JOB_API_TOKEN=//p' "$target")
      [[ $token =~ ^[0-9a-f]{64}$ ]] || fail
    else
      token=$(openssl rand -hex 32)
    fi
  fi
  {
    awk 'NF && $1 !~ /^#/' "$database_snapshot"
    case "$kind" in
      control)
        printf 'TRADING_DATA_ROOT=/opt/trading-agent-phase4\n'
        printf 'TRADING_STORE_BACKEND=postgres\n'
        printf 'GIT_COMMIT=%s\n' "$APP_COMMIT"
        printf 'BUILD_TIME=immutable-release\n'
        printf 'DEPLOYMENT_ID=phase4b-control-api\n'
        ;;
      api)
        printf 'TRADING_JOB_API_TOKEN=%s\n' "$token"
        printf 'TRADING_JOB_API_PRINCIPAL_TYPE=OPERATOR\n'
        printf 'TRADING_JOB_API_PRINCIPAL_ID=dashboard-service\n'
        printf 'TRADING_JOB_API_EXPECTED_REVISION=0004_durable_research_jobs\n'
        ;;
      worker)
        printf 'TRADING_WORKER_ID=phase4b-worker-1\n'
        printf 'TRADING_CODE_COMMIT=%s\n' "$APP_COMMIT"
        printf 'TRADING_WORKER_LEASE_SECONDS=30\nTRADING_WORKER_IDLE_SECONDS=1\n'
        ;;
      scheduler)
        printf 'TRADING_SCHEDULER_ID=phase4b-scheduler-1\n'
        printf 'TRADING_SCHEDULER_ACTOR_ID=phase4b-scheduler\n'
        printf 'TRADING_CODE_COMMIT=%s\n' "$APP_COMMIT"
        ;;
      *) fail ;;
    esac
    printf 'TRADING_MODE=paper\nLIVE_EXECUTION_ENABLED=false\nLIVE_TRADING_APPROVED=false\n'
  } >"$temporary"
  chmod 0600 "$temporary"
  chown "$RUNTIME_UID:$RUNTIME_GID" "$temporary"
  install_exact_file "$temporary" "$target" "$RUNTIME_UID" "$RUNTIME_GID" 600
  find "$temporary" -delete
  TEMP_ENV=""
}
make_env "$ETC_ROOT/control-api.env" control
make_env "$ETC_ROOT/job-api.env" api
make_env "$ETC_ROOT/job-worker.env" worker
make_env "$ETC_ROOT/job-scheduler.env" scheduler

ensure_directory "$GLOBAL_USER_UNITS" 0 0 755
ensure_directory "$GLOBAL_SYSTEM_UNITS" 0 0 755
for unit in trading-safety-state-export.service trading-safety-state-export.timer \
  trading-control-api.service trading-job-api.service trading-job-worker.service trading-job-scheduler.service \
  trading-job-scheduler.timer; do
  install_exact_file "$TRUSTED_STAGING_ROOT/units/$unit" "$GLOBAL_USER_UNITS/$unit" 0 0 644
  [[ $(sha256sum "$GLOBAL_USER_UNITS/$unit" | awk '{print $1}') == "${UNIT_SHA256[$unit]}" ]] || fail
done
for unit in trading-semantic-input-refresh.service trading-semantic-input-refresh.timer; do
  install_exact_file "$TRUSTED_STAGING_ROOT/units/$unit" "$GLOBAL_SYSTEM_UNITS/$unit" 0 0 644
  [[ $(sha256sum "$GLOBAL_SYSTEM_UNITS/$unit" | awk '{print $1}') == "${UNIT_SHA256[$unit]}" ]] || fail
done

PROVISIONING_SUCCEEDED=true
printf '%s\n' "phase 4b root provisioning installed create-only authority"
