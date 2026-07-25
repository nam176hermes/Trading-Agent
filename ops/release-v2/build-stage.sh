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

safe_input_file() {
  local input=$1 uid mode links
  [[ -f $input && ! -L $input && $(realpath -e -- "$input") == "$input" ]] || return 1
  uid=$(stat -c '%u' -- "$input") || return 1
  mode=$(stat -c '%a' -- "$input") || return 1
  links=$(stat -c '%h' -- "$input") || return 1
  [[ $uid == 0 || $uid == "$EUID" ]] || return 1
  [[ $links == 1 ]] || return 1
  (( (8#$mode & (0022 | 07000)) == 0 )) || return 1
  safe_directory_chain "$(dirname -- "$input")" false
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
PYTHON=/usr/bin/python3
PYTHON_RUNTIME_ARCHIVE=''
UV=$(command -v uv || true)
WHEELHOUSE=''
PINNED_UV_SHA256='cd952ca51e2c730e848a45c4e0dfb58926d79d90550b6a5feb5543b43d3248b4'
PINNED_UV_IDENTITY='uv 0.11.7 (x86_64-unknown-linux-gnu)'

while (($#)); do
  case "$1" in
    --repo) [[ $# -ge 2 ]] || fail; REPO=$2; shift 2 ;;
    --commit) [[ $# -ge 2 ]] || fail; COMMIT=$2; shift 2 ;;
    --output) [[ $# -ge 2 ]] || fail; OUTPUT=$2; shift 2 ;;
    --prior-release-sha256) [[ $# -ge 2 ]] || fail; PRIOR_SHA=$2; shift 2 ;;
    --python) [[ $# -ge 2 ]] || fail; PYTHON=$2; shift 2 ;;
    --python-runtime-archive) [[ $# -ge 2 ]] || fail; PYTHON_RUNTIME_ARCHIVE=$2; shift 2 ;;
    --uv) [[ $# -ge 2 ]] || fail; UV=$2; shift 2 ;;
    --wheelhouse) [[ $# -ge 2 ]] || fail; WHEELHOUSE=$2; shift 2 ;;
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

# Offline inputs are operator-selected, sealed, and lock-bound. Never accept a
# broad package-manager cache or permit network fallback.
[[ $WHEELHOUSE == /* && $PYTHON_RUNTIME_ARCHIVE == /* ]] || fail
safe_directory_chain "$WHEELHOUSE" || fail
cache_manifest "$WHEELHOUSE" >/dev/null || fail
safe_input_file "$PYTHON_RUNTIME_ARCHIVE" || fail
PYTHON_RUNTIME_ARCHIVE=$(realpath -e -- "$PYTHON_RUNTIME_ARCHIVE") || fail

AUTHORITY="$OUTPUT.authority.json"
VERIFIER="$OUTPUT.verify-stage.py"
[[ ! -e $AUTHORITY && ! -L $AUTHORITY && ! -e $VERIFIER && ! -L $VERIFIER ]] || fail
[[ -x $PYTHON && -n $UV && -x $UV ]] || fail
safe_input_file "$UV" || fail
PYTHON=$(realpath -e -- "$PYTHON") || fail
UV=$(realpath -e -- "$UV") || fail
[[ $(sha256sum "$UV" | cut -d' ' -f1) == "$PINNED_UV_SHA256" ]] || fail

for tracked in \
  packages/runtime_release/v2.py \
  packages/runtime_release/offline_wheelhouse.py \
  ops/release-v2/verify-stage.py \
  packages/runtime_release/paper_application/dependency-manifest.json \
  packages/runtime_release/paper_application/uv.lock \
  legacy/research-backend/job_attribution.py \
  packages/runtime_release/paper_application/pyproject.toml \
  packages/runtime_release/paper_application/command_registry.py \
  packages/runtime_release/paper_application/runtime_release_init.py \
  packages/runtime_release/paper_backend/paper_main.py \
  packages/runtime_release/paper_backend/paper_runtime_manifest.json \
  packages/runtime_release/paper_backend/research_semantics.py; do
  git -C "$REPO" cat-file -e "$COMMIT:$tracked" || fail
done

OUTPUT_PARENT=$(dirname -- "$OUTPUT")
ensure_private_directory "$OUTPUT_PARENT" || fail
# Source-proof generation must never execute an operator-home interpreter.
safe_root_executable "$PYTHON" || fail
BUILD_ROOT=$(mktemp -d --tmpdir="$OUTPUT_PARENT" .release-v2-build.XXXXXXXX)
EXPORT_ROOT="$BUILD_ROOT/export"
STAGE="$BUILD_ROOT/stage"
BUILD_HOME="$BUILD_ROOT/home"
TOOL="$BUILD_ROOT/v2.py"
SOURCE_PROOF="$BUILD_ROOT/source-proof.json"
VERIFIER_BUILD="$BUILD_ROOT/verify-stage.py"
REQUIREMENTS="$BUILD_ROOT/application-requirements.txt"
BUILD_UV="$BUILD_ROOT/uv"
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
"$PYTHON" -I "$TOOL" project-pinned-uv \
  --source "$UV" --destination "$BUILD_UV" || fail
[[ $("$BUILD_UV" --version) == "$PINNED_UV_IDENTITY" ]] || fail
"$PYTHON" -I "$TOOL" capture-source-proof \
  --repo "$REPO" --commit "$COMMIT" --output "$SOURCE_PROOF" || fail

mkdir -p -- "$EXPORT_ROOT" "$STAGE" "$BUILD_HOME"
WHEELHOUSE_BEFORE=$(cache_manifest "$WHEELHOUSE") || fail
git -C "$REPO" archive --format=tar "$COMMIT" | tar -xf - -C "$EXPORT_ROOT" --no-same-owner
[[ -d $EXPORT_ROOT/legacy/research-backend \
  && -d $EXPORT_ROOT/packages/runtime_release/paper_application \
  && -d $EXPORT_ROOT/packages/runtime_release/paper_backend ]] || fail
if find "$EXPORT_ROOT" \( -type l -o ! -type d ! -type f \) -print -quit | grep -q .; then
  fail
fi
DEPENDENCY_MANIFEST="$EXPORT_ROOT/packages/runtime_release/paper_application/dependency-manifest.json"
chmod 0444 "$DEPENDENCY_MANIFEST"

"$PYTHON" -I "$TOOL" project-paper-application \
  --source "$EXPORT_ROOT" --destination "$STAGE/application" || fail
"$PYTHON" -I "$TOOL" project-paper-backend \
  --source "$EXPORT_ROOT" --destination "$STAGE/backend" || fail

env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
  "$PYTHON" -I - \
  "$EXPORT_ROOT/packages/runtime_release/offline_wheelhouse.py" \
  "$WHEELHOUSE" "$STAGE/application/uv.lock" <<'PY' || fail
import importlib.util
import pathlib
import sys

module_path = pathlib.Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("release_offline_wheelhouse", module_path)
if spec is None or spec.loader is None:
    raise SystemExit(2)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
digest = module.verify_offline_wheelhouse(sys.argv[2], sys.argv[3])
print(digest)
PY
[[ $WHEELHOUSE_BEFORE == "$(cache_manifest "$WHEELHOUSE")" ]] || fail

APP_VENV="$STAGE/application/.venv"
BACKEND_VENV="$STAGE/backend/.venv"
"$PYTHON" -I "$TOOL" project-python-runtime-archive \
  --archive "$PYTHON_RUNTIME_ARCHIVE" --destination "$APP_VENV" || fail
"$PYTHON" -I "$TOOL" project-python-runtime-archive \
  --archive "$PYTHON_RUNTIME_ARCHIVE" --destination "$BACKEND_VENV" || fail
"$PYTHON" -I "$TOOL" verify-python-runtime --runtime "$APP_VENV" || fail
"$PYTHON" -I "$TOOL" verify-python-runtime --runtime "$BACKEND_VENV" || fail

SAFE_PATH="$(dirname -- "$BUILD_UV"):$(dirname -- "$PYTHON"):/usr/bin:/bin"
env -i \
  HOME="$BUILD_HOME" PATH="$SAFE_PATH" \
  UV_OFFLINE=1 UV_COMPILE_BYTECODE=0 \
  "$BUILD_UV" export --project "$STAGE/application" --frozen --no-dev \
  --no-emit-project --no-annotate --no-header --offline --no-cache \
  --no-python-downloads --output-file "$REQUIREMENTS"
env -i \
  HOME="$BUILD_HOME" PATH="$SAFE_PATH" UV_COMPILE_BYTECODE=0 \
  "$BUILD_UV" pip sync "$REQUIREMENTS" \
  --python "$APP_VENV/bin/python3.11" --require-hashes --strict \
  --only-binary=:all: --no-index --find-links "$WHEELHOUSE" \
  --no-cache --link-mode copy --no-python-downloads
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 \
  "$PYTHON" -I - \
  "$EXPORT_ROOT/packages/runtime_release/offline_wheelhouse.py" \
  "$WHEELHOUSE" "$STAGE/application/uv.lock" \
  "$APP_VENV/lib/python3.11/site-packages" "$DEPENDENCY_MANIFEST" <<'PY' >/dev/null || fail
import importlib.util
import pathlib
import sys

module_path = pathlib.Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("release_offline_wheelhouse", module_path)
if spec is None or spec.loader is None:
    raise SystemExit(2)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
module.canonicalize_installed_site_packages(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
PY
find "$APP_VENV/bin" -mindepth 1 -maxdepth 1 ! -name python3.11 -delete
"$PYTHON" -I "$TOOL" verify-python-runtime \
  --runtime "$APP_VENV" --allow-site-packages || fail
"$PYTHON" -I "$TOOL" verify-python-runtime --runtime "$BACKEND_VENV" || fail

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
  "$APP_VENV/bin/python3.11" -I -B -c 'import platform; print(f"{platform.python_implementation()} {platform.python_version()}")'
) || fail
BACKEND_ID=$(
  "$BACKEND_VENV/bin/python3.11" -I -B -c 'import platform; print(f"{platform.python_implementation()} {platform.python_version()}")'
) || fail

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
  --external-verifier "$VERIFIER" \
  --application-dependency-manifest "$DEPENDENCY_MANIFEST" \
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
