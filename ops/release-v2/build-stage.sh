#!/usr/bin/env bash
set -euo pipefail

export PYTHONDONTWRITEBYTECODE=1
umask 077

fail() {
  printf '%s\n' 'release authority v2 build rejected' >&2
  exit 2
}

safe_directory_chain() {
  local current=$1 require_private_leaf=${2:-true} uid mode first=true
  [[ -d $current && ! -L $current && $(realpath -e -- "$current") == "$current" ]] || return 1
  while :; do
    uid=$(stat -c '%u' -- "$current") || return 1
    mode=$(stat -c '%a' -- "$current") || return 1
    [[ $uid == 0 || $uid == "$EUID" ]] || return 1
    if [[ $first == true && $require_private_leaf == true ]]; then
      (( (8#$mode & 07022) == 0 )) || return 1
      first=false
    elif (( (8#$mode & 0022) != 0 )); then
      # Root-owned sticky ancestors such as /tmp are safe traversal boundaries.
      (( uid == 0 && (8#$mode & 01000) != 0 && (8#$mode & 07000) == 01000 )) || return 1
    else
      (( (8#$mode & 07000) == 0 )) || return 1
    fi
    [[ $current == / ]] && break
    current=$(dirname -- "$current")
  done
}

ensure_private_directory() {
  local target=$1 current=$1 index
  local -a missing=()
  while [[ ! -e $current && ! -L $current ]]; do
    missing+=("$current")
    [[ $current != / ]] || return 1
    current=$(dirname -- "$current")
  done
  safe_directory_chain "$current" false || return 1
  for ((index=${#missing[@]} - 1; index >= 0; index--)); do
    mkdir -m 0700 -- "${missing[index]}" || return 1
    safe_directory_chain "${missing[index]}" || return 1
  done
  safe_directory_chain "$target"
}

safe_root_executable() {
  local executable=$1 current uid gid mode links
  [[ -f $executable && ! -L $executable && $(realpath -e -- "$executable") == "$executable" ]] || return 1
  uid=$(stat -c '%u' -- "$executable") || return 1
  gid=$(stat -c '%g' -- "$executable") || return 1
  mode=$(stat -c '%a' -- "$executable") || return 1
  links=$(stat -c '%h' -- "$executable") || return 1
  [[ $uid == 0 && $gid == 0 && $links == 1 ]] || return 1
  (( (8#$mode == 0555 || 8#$mode == 0755) && (8#$mode & 07000) == 0 )) || return 1
  current=$(dirname -- "$executable")
  while :; do
    [[ -d $current && ! -L $current ]] || return 1
    uid=$(stat -c '%u' -- "$current") || return 1
    gid=$(stat -c '%g' -- "$current") || return 1
    mode=$(stat -c '%a' -- "$current") || return 1
    [[ $uid == 0 && $gid == 0 ]] || return 1
    (( (8#$mode & (0022 | 07000)) == 0 )) || return 1
    [[ $current == / ]] && break
    current=$(dirname -- "$current")
  done
}

cache_manifest() {
  env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -I - "$1" "$EUID" <<'PY'
import hashlib, json, os, pathlib, stat, sys

root = pathlib.Path(sys.argv[1])
allowed_uids = {0, int(sys.argv[2])}
if not root.is_absolute() or root.resolve(strict=True) != root:
    raise SystemExit(2)
root_info = root.lstat()
root_mode = stat.S_IMODE(root_info.st_mode)
if (
    not stat.S_ISDIR(root_info.st_mode)
    or root_info.st_uid not in allowed_uids
    or root_mode & (0o022 | 0o7000)
):
    raise SystemExit(2)
entries = []
pending = [root]
while pending:
    directory = pending.pop()
    for child in sorted(directory.iterdir(), key=lambda item: os.fsencode(item.name)):
        relative = child.relative_to(root).as_posix()
        info = child.lstat()
        mode = stat.S_IMODE(info.st_mode)
        if info.st_uid not in allowed_uids or mode & (0o022 | 0o7000):
            raise SystemExit(2)
        if stat.S_ISDIR(info.st_mode):
            entries.append([relative, "d", mode, 0, ""])
            pending.append(child)
        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            descriptor = os.open(child, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
            try:
                before = os.fstat(descriptor)
                digest = hashlib.sha256()
                while data := os.read(descriptor, 1024 * 1024):
                    digest.update(data)
                after = os.fstat(descriptor)
            finally:
                os.close(descriptor)
            identity = lambda value: (
                value.st_dev, value.st_ino, value.st_uid, value.st_gid,
                stat.S_IMODE(value.st_mode), value.st_size, value.st_mtime_ns,
            )
            if identity(before) != identity(after) or identity(before) != identity(info):
                raise SystemExit(2)
            entries.append([relative, "f", mode, info.st_size, digest.hexdigest()])
        else:
            raise SystemExit(2)
if not entries:
    raise SystemExit(2)
entries.sort(key=lambda item: os.fsencode(item[0]))
print(hashlib.sha256(json.dumps(entries, separators=(",", ":")).encode()).hexdigest())
PY
}

REPO=''
COMMIT=''
OUTPUT=''
PRIOR_SHA=''
PYTHON=/usr/bin/python3.11
NODE=/usr/bin/node
NPM=/usr/bin/npm
UV=$(command -v uv || true)
UV_CACHE=''
NPM_CACHE=''

while (($#)); do
  case "$1" in
    --repo) [[ $# -ge 2 ]] || fail; REPO=$2; shift 2 ;;
    --commit) [[ $# -ge 2 ]] || fail; COMMIT=$2; shift 2 ;;
    --output) [[ $# -ge 2 ]] || fail; OUTPUT=$2; shift 2 ;;
    --prior-release-sha256) [[ $# -ge 2 ]] || fail; PRIOR_SHA=$2; shift 2 ;;
    --python) [[ $# -ge 2 ]] || fail; PYTHON=$2; shift 2 ;;
    --node) [[ $# -ge 2 ]] || fail; NODE=$2; shift 2 ;;
    --npm) [[ $# -ge 2 ]] || fail; NPM=$2; shift 2 ;;
    --uv) [[ $# -ge 2 ]] || fail; UV=$2; shift 2 ;;
    --uv-cache) [[ $# -ge 2 ]] || fail; UV_CACHE=$2; shift 2 ;;
    --npm-cache) [[ $# -ge 2 ]] || fail; NPM_CACHE=$2; shift 2 ;;
    *) fail ;;
  esac
done

# The clean-one-object gate runs before any stage path is created.
[[ $REPO == /* && $OUTPUT == /* && $OUTPUT != / && $COMMIT =~ ^[0-9a-f]{40}$ ]] || fail
[[ $PRIOR_SHA =~ ^[0-9a-f]{64}$ ]] || fail
[[ -d $REPO/.git && ! -L $REPO && ! -e $OUTPUT && ! -L $OUTPUT ]] || fail
[[ $(git -C "$REPO" rev-parse --show-toplevel) == "$REPO" ]] || fail
[[ $(git -C "$REPO" rev-parse HEAD) == "$COMMIT" ]] || fail
[[ $(git -C "$REPO" rev-parse --verify "$COMMIT^{commit}") == "$COMMIT" ]] || fail
[[ -z $(git -C "$REPO" status --porcelain=v1 --untracked-files=normal) ]] || fail

# Offline means pre-populated, operator-selected caches. Never silently start
# with an empty cache and then depend on network fallback.
[[ $UV_CACHE == /* && $NPM_CACHE == /* ]] || fail
for cache in "$UV_CACHE" "$NPM_CACHE"; do
  safe_directory_chain "$cache" || fail
  cache_manifest "$cache" >/dev/null || fail
done

AUTHORITY="$OUTPUT.authority.json"
VERIFIER="$OUTPUT.verify-stage.py"
[[ ! -e $AUTHORITY && ! -L $AUTHORITY && ! -e $VERIFIER && ! -L $VERIFIER ]] || fail
[[ -x $PYTHON && -x $NODE && -x $NPM && -n $UV && -x $UV ]] || fail
PYTHON=$(realpath -e -- "$PYTHON") || fail
NODE=$(realpath -e -- "$NODE") || fail
NPM=$(realpath -e -- "$NPM") || fail
UV=$(realpath -e -- "$UV") || fail

for tracked in \
  packages/runtime_release/v2.py \
  ops/release-v2/verify-stage.py \
  alembic/versions/0005_job_plane_role_split.py \
  alembic/versions/0006_job_transition_database_authority.py \
  uv.lock legacy/research-backend/uv.lock apps/dashboard/package-lock.json; do
  git -C "$REPO" cat-file -e "$COMMIT:$tracked" || fail
done

OUTPUT_PARENT=$(dirname -- "$OUTPUT")
ensure_private_directory "$OUTPUT_PARENT" || fail
# Source-proof generation must never execute an operator-home interpreter.
safe_root_executable "$PYTHON" || fail
# The same absolute Node is later bound as runtime authority and must be safe
# before npm or any committed dashboard build script can execute it.
safe_root_executable "$NODE" || fail
BUILD_ROOT=$(mktemp -d --tmpdir="$OUTPUT_PARENT" .release-v2-build.XXXXXXXX)
EXPORT_ROOT="$BUILD_ROOT/export"
STAGE="$BUILD_ROOT/stage"
BUILD_HOME="$BUILD_ROOT/home"
TOOL="$BUILD_ROOT/v2.py"
SOURCE_PROOF="$BUILD_ROOT/source-proof.json"
VERIFIER_BUILD="$BUILD_ROOT/verify-stage.py"
OUTPUT_CREATED=false
SUCCESS=false

cleanup() {
  chmod -R u+w -- "$BUILD_ROOT" 2>/dev/null || true
  rm -rf -- "$BUILD_ROOT"
  if [[ $SUCCESS == false && $OUTPUT_CREATED == true ]]; then
    chmod -R u+w -- "$OUTPUT" 2>/dev/null || true
    rm -rf -- "$OUTPUT"
  fi
  if [[ $SUCCESS == false ]]; then
    rm -f -- "$AUTHORITY"
    rm -f -- "$VERIFIER"
  fi
}
trap cleanup EXIT

git -C "$REPO" show "$COMMIT:packages/runtime_release/v2.py" >"$TOOL"
"$PYTHON" -I "$TOOL" capture-source-proof \
  --repo "$REPO" --commit "$COMMIT" --output "$SOURCE_PROOF" || fail

mkdir -p -- "$EXPORT_ROOT" "$STAGE" "$BUILD_HOME/uv-cache" "$BUILD_HOME/npm-cache"
UV_CACHE_BEFORE=$(cache_manifest "$UV_CACHE") || fail
NPM_CACHE_BEFORE=$(cache_manifest "$NPM_CACHE") || fail
cp -a --reflink=never -- "$UV_CACHE/." "$BUILD_HOME/uv-cache/"
cp -a --reflink=never -- "$NPM_CACHE/." "$BUILD_HOME/npm-cache/"
[[ $UV_CACHE_BEFORE == "$(cache_manifest "$UV_CACHE")" ]] || fail
[[ $NPM_CACHE_BEFORE == "$(cache_manifest "$NPM_CACHE")" ]] || fail
[[ $UV_CACHE_BEFORE == "$(cache_manifest "$BUILD_HOME/uv-cache")" ]] || fail
[[ $NPM_CACHE_BEFORE == "$(cache_manifest "$BUILD_HOME/npm-cache")" ]] || fail
git -C "$REPO" archive --format=tar "$COMMIT" | tar -xf - -C "$EXPORT_ROOT" --no-same-owner
[[ -d $EXPORT_ROOT/legacy/research-backend && -d $EXPORT_ROOT/apps/dashboard ]] || fail
if find "$EXPORT_ROOT" \( -type l -o ! -type d ! -type f \) -print -quit | grep -q .; then
  fail
fi

mv -- "$EXPORT_ROOT/legacy/research-backend" "$STAGE/backend"
mv -- "$EXPORT_ROOT/apps/dashboard" "$STAGE/dashboard"
rmdir -- "$EXPORT_ROOT/legacy"
rmdir -- "$EXPORT_ROOT/apps/dashboard" 2>/dev/null || true
mv -- "$EXPORT_ROOT" "$STAGE/application"

APP_VENV="$STAGE/application/.venv"
BACKEND_VENV="$STAGE/backend/.venv"
"$PYTHON" -I -m venv --without-pip --copies "$APP_VENV"
"$PYTHON" -I -m venv --without-pip --copies "$BACKEND_VENV"
rm -f -- "$APP_VENV/lib64" "$BACKEND_VENV/lib64"

# A copied launcher is not a sealed runtime if its base prefix or stdlib still
# resolves to the build host. Fail closed until a reviewed relocatable runtime
# is supplied inside each candidate environment.
for runtime in "$APP_VENV/bin/python3.11" "$BACKEND_VENV/bin/python3.11"; do
  env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 "$runtime" -I - "$STAGE" <<'PY' || fail
import pathlib, sys, sysconfig
stage = pathlib.Path(sys.argv[1]).resolve(strict=True)
for value in (sys.base_prefix, sys.base_exec_prefix, sysconfig.get_path("stdlib")):
    resolved = pathlib.Path(value).resolve(strict=True)
    if resolved != stage and stage not in resolved.parents:
        raise SystemExit(2)
PY
done

SAFE_PATH="$(dirname -- "$UV"):$(dirname -- "$PYTHON"):/usr/bin:/bin"
env -i \
  HOME="$BUILD_HOME" PATH="$SAFE_PATH" UV_CACHE_DIR="$BUILD_HOME/uv-cache" \
  UV_OFFLINE=1 UV_COMPILE_BYTECODE=0 VIRTUAL_ENV="$APP_VENV" \
  "$UV" sync --project "$STAGE/application" --frozen --no-dev --no-editable \
  --active --offline --link-mode copy --no-python-downloads
env -i \
  HOME="$BUILD_HOME" PATH="$SAFE_PATH" UV_CACHE_DIR="$BUILD_HOME/uv-cache" \
  UV_OFFLINE=1 UV_COMPILE_BYTECODE=0 VIRTUAL_ENV="$BACKEND_VENV" \
  "$UV" sync --project "$STAGE/backend" --frozen --no-dev --no-editable \
  --active --offline --link-mode copy --no-python-downloads
rm -f -- "$APP_VENV/lib64" "$BACKEND_VENV/lib64"

NODE_PATH="$(dirname -- "$NODE"):/usr/bin:/bin"
env -i HOME="$BUILD_HOME" PATH="$NODE_PATH" npm_config_offline=true \
  npm_config_cache="$BUILD_HOME/npm-cache" \
  npm_config_ignore_scripts=true NEXT_TELEMETRY_DISABLED=1 \
  "$NPM" ci --offline --ignore-scripts --prefix "$STAGE/dashboard"
env -i HOME="$BUILD_HOME" PATH="$NODE_PATH" npm_config_offline=true \
  npm_config_cache="$BUILD_HOME/npm-cache" NEXT_TELEMETRY_DISABLED=1 \
  "$NPM" run build --prefix "$STAGE/dashboard"
rm -rf -- "$STAGE/dashboard/node_modules/.bin"

find "$STAGE" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$STAGE" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete
if find "$STAGE" \( -type l -o ! -type d ! -type f \) -print -quit | grep -q .; then
  fail
fi
if find "$STAGE" -type f -links +1 -print -quit | grep -q .; then
  fail
fi

"$PYTHON" -I "$TOOL" render-units --stage "$STAGE" --commit "$COMMIT" || fail
APP_ID=$(
  "$APP_VENV/bin/python3.11" -I -c 'import platform; print(f"{platform.python_implementation()} {platform.python_version()}")'
) || fail
BACKEND_ID=$(
  "$BACKEND_VENV/bin/python3.11" -I -c 'import platform; print(f"{platform.python_implementation()} {platform.python_version()}")'
) || fail
NODE_ID="Node.js $($NODE --version)" || fail

git -C "$REPO" show "$COMMIT:ops/release-v2/verify-stage.py" >"$VERIFIER_BUILD"
chmod 0555 "$VERIFIER_BUILD"

find "$STAGE" -type d -exec chmod 0555 {} +
find "$STAGE" -type f -perm /0111 -exec chmod 0555 {} +
find "$STAGE" -type f ! -perm /0111 -exec chmod 0444 {} +
chmod 0755 "$STAGE"
mv -T -- "$STAGE" "$OUTPUT"
chmod 0555 "$OUTPUT"
OUTPUT_CREATED=true
ln -- "$VERIFIER_BUILD" "$VERIFIER" || fail
rm -f -- "$VERIFIER_BUILD"

# This external composition step hashes the sealed tree but executes no staged file.
"$PYTHON" -I "$TOOL" compose \
  --stage "$OUTPUT" \
  --source-proof "$SOURCE_PROOF" \
  --application-python-identity "$APP_ID" \
  --backend-python-identity "$BACKEND_ID" \
  --node-executable "$NODE" \
  --node-identity "$NODE_ID" \
  --external-verifier "$VERIFIER" \
  --prior-release-sha256 "$PRIOR_SHA" \
  --output "$AUTHORITY" || fail
AUTHORITY_SHA=$(sha256sum "$AUTHORITY" | awk '{print $1}')

env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -I \
  "$VERIFIER" "$OUTPUT" "$AUTHORITY" --expected-authority-sha256 "$AUTHORITY_SHA" >/dev/null || fail
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -I \
  "$VERIFIER" "$OUTPUT" "$AUTHORITY" --expected-authority-sha256 "$AUTHORITY_SHA" >/dev/null || fail

SUCCESS=true
trap - EXIT
chmod -R u+w -- "$BUILD_ROOT" 2>/dev/null || true
rm -rf -- "$BUILD_ROOT"
printf '%s\n' 'release authority v2 static candidate built and verified'
