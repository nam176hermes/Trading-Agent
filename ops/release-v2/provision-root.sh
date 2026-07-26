#!/usr/bin/env bash
set -euo pipefail

export PYTHONDONTWRITEBYTECODE=1
umask 077

# Independently reviewed source pin. A digest declared only by the candidate
# authority is never sufficient to select code for privileged verification.
PINNED_VERIFIER_SHA256='35472681d4edee23e91df7de65626b4a5a4fd86f58369b6a14361a6f56241acd'

fail() {
  printf '%s\n' 'release authority v2 provisioning rejected' >&2
  exit 2
}

ROOT_UID=$(stat -c '%u' -- /) || fail

safe_directory_chain() {
  local current=$1 require_private_leaf=${2:-true} uid mode first=true
  [[ -d $current && ! -L $current && $(realpath -e -- "$current") == "$current" ]] || return 1
  while :; do
    uid=$(stat -c '%u' -- "$current") || return 1
    mode=$(stat -c '%a' -- "$current") || return 1
    [[ $uid == "$ROOT_UID" || $uid == "$EUID" ]] || return 1
    if [[ $first == true && $require_private_leaf == true ]]; then
      (( (8#$mode & 07022) == 0 )) || return 1
      first=false
    elif (( (8#$mode & 0022) != 0 )); then
      (( uid == ROOT_UID && (8#$mode & 01000) != 0 && (8#$mode & 07000) == 01000 )) || return 1
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

STAGE=''
AUTHORITY=''
AUTHORITY_SHA=''
DECLARED_VERIFIER=''
DESTINATION_ROOT=''
TEST_FAKE_ROOT=false
while (($#)); do
  case "$1" in
    --stage) [[ $# -ge 2 ]] || fail; STAGE=$2; shift 2 ;;
    --authority) [[ $# -ge 2 ]] || fail; AUTHORITY=$2; shift 2 ;;
    --authority-sha256) [[ $# -ge 2 ]] || fail; AUTHORITY_SHA=$2; shift 2 ;;
    --verifier) [[ $# -ge 2 ]] || fail; DECLARED_VERIFIER=$2; shift 2 ;;
    --destination-root) [[ $# -ge 2 ]] || fail; DESTINATION_ROOT=$2; shift 2 ;;
    --test-fake-root) TEST_FAKE_ROOT=true; shift ;;
    *) fail ;;
  esac
done

# Task 5 is candidate-only. Production root and pointer activation are absent.
[[ $EUID -ne 0 ]] || fail
[[ $TEST_FAKE_ROOT == true && $DESTINATION_ROOT == /* && $DESTINATION_ROOT != / ]] || fail
[[ $STAGE == /* && $AUTHORITY == /* && $DECLARED_VERIFIER == /* ]] || fail
[[ $AUTHORITY_SHA =~ ^[0-9a-f]{64}$ ]] || fail
[[ -d $STAGE && ! -L $STAGE && -f $AUTHORITY && ! -L $AUTHORITY ]] || fail
[[ -f $DECLARED_VERIFIER && ! -L $DECLARED_VERIFIER ]] || fail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
PINNED_VERIFIER_SOURCE="$SCRIPT_DIR/verify-stage.py"
[[ -f $PINNED_VERIFIER_SOURCE && ! -L $PINNED_VERIFIER_SOURCE ]] || fail

ensure_private_directory "$DESTINATION_ROOT" || fail
TRUST_ROOT=$(mktemp -d --tmpdir release-v2-fake-provision.XXXXXXXX)
TRUSTED_AUTHORITY="$TRUST_ROOT/authority.json"
TRUSTED_VERIFIER="$TRUST_ROOT/verify-stage.py"
cleanup_trust() {
  chmod -R u+w -- "$TRUST_ROOT" 2>/dev/null || true
  rm -rf -- "$TRUST_ROOT"
}
trap cleanup_trust EXIT

install -m 0444 -- "$AUTHORITY" "$TRUSTED_AUTHORITY"
install -m 0555 -- "$PINNED_VERIFIER_SOURCE" "$TRUSTED_VERIFIER"
[[ $(sha256sum "$TRUSTED_AUTHORITY" | awk '{print $1}') == "$AUTHORITY_SHA" ]] || fail
[[ $(sha256sum "$TRUSTED_VERIFIER" | awk '{print $1}') == "$PINNED_VERIFIER_SHA256" ]] || fail
readarray -t BINDINGS < <(
  /usr/bin/python3 -I -c '
import json, pathlib, sys
d=json.loads(pathlib.Path(sys.argv[1]).read_bytes())
print(d["installation_root"])
print(d["external_verifier"]["source_path"])
print(d["external_verifier"]["sha256"])
print(d["external_verifier"]["installation_path"])
' "$TRUSTED_AUTHORITY"
) || fail
[[ ${#BINDINGS[@]} -eq 4 ]] || fail
INSTALL_ROOT=${BINDINGS[0]}
BOUND_VERIFIER=${BINDINGS[1]}
BOUND_VERIFIER_SHA=${BINDINGS[2]}
BOUND_VERIFIER_INSTALL=${BINDINGS[3]}
[[ $INSTALL_ROOT == /opt/trading-agent-v2/releases/* ]] || fail
[[ $BOUND_VERIFIER == "$DECLARED_VERIFIER" ]] || fail
[[ $BOUND_VERIFIER_SHA == "$PINNED_VERIFIER_SHA256" ]] || fail
[[ $BOUND_VERIFIER_INSTALL == /usr/libexec/trading-agent-v2/verify-stage.py ]] || fail
[[ $(sha256sum "$DECLARED_VERIFIER" | awk '{print $1}') == "$PINNED_VERIFIER_SHA256" ]] || fail

env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -I \
  "$TRUSTED_VERIFIER" "$STAGE" "$TRUSTED_AUTHORITY" \
  --expected-authority-sha256 "$AUTHORITY_SHA" \
  --verifier-copy-of "$DECLARED_VERIFIER" >/dev/null || fail
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -I \
  "$TRUSTED_VERIFIER" "$STAGE" "$TRUSTED_AUTHORITY" \
  --expected-authority-sha256 "$AUTHORITY_SHA" \
  --verifier-copy-of "$DECLARED_VERIFIER" >/dev/null || fail

RELEASE_BASE="$DESTINATION_ROOT/opt/trading-agent-v2"
AUTHORITY_BASE="$DESTINATION_ROOT/etc/trading-agent/release-authority-v2"
TARGET="${DESTINATION_ROOT%/}$INSTALL_ROOT"
AUTHORITY_TARGET="$AUTHORITY_BASE/$AUTHORITY_SHA.json"
PENDING="$RELEASE_BASE/.pending-$AUTHORITY_SHA"
AUTHORITY_PENDING="$AUTHORITY_BASE/.pending-$AUTHORITY_SHA.json"
VERIFIER_BASE="$DESTINATION_ROOT/usr/libexec/trading-agent-v2"
VERIFIER_TARGET="${DESTINATION_ROOT%/}$BOUND_VERIFIER_INSTALL"
VERIFIER_PENDING="$VERIFIER_BASE/.pending-$AUTHORITY_SHA-verify-stage.py"
PUBLISHED=false
TARGET_CREATED=false
AUTHORITY_CREATED=false
VERIFIER_CREATED=false
cleanup() {
  cleanup_trust
  if [[ $PUBLISHED == false ]]; then
    chmod -R u+w -- "$PENDING" 2>/dev/null || true
    rm -rf -- "$PENDING"
    rm -f -- "$AUTHORITY_PENDING"
    rm -f -- "$VERIFIER_PENDING"
    if [[ $AUTHORITY_CREATED == true ]]; then rm -f -- "$AUTHORITY_TARGET"; fi
    if [[ $VERIFIER_CREATED == true ]]; then rm -f -- "$VERIFIER_TARGET"; fi
    if [[ $TARGET_CREATED == true ]]; then
      chmod -R u+w -- "$TARGET" 2>/dev/null || true
      rm -rf -- "$TARGET"
    fi
  fi
}
trap cleanup EXIT

for relative in \
  opt \
  opt/trading-agent-v2 \
  opt/trading-agent-v2/releases \
  etc \
  etc/trading-agent \
  etc/trading-agent/release-authority-v2 \
  usr \
  usr/libexec \
  usr/libexec/trading-agent-v2; do
  directory="$DESTINATION_ROOT/$relative"
  if [[ -e $directory || -L $directory ]]; then
    safe_directory_chain "$directory" || fail
  else
    mkdir -m 0700 -- "$directory"
    safe_directory_chain "$directory" || fail
  fi
done
[[ ! -e $TARGET && ! -L $TARGET && ! -e $AUTHORITY_TARGET && ! -L $AUTHORITY_TARGET ]] || fail
[[ ! -e $VERIFIER_TARGET && ! -L $VERIFIER_TARGET ]] || fail
[[ ! -e $PENDING && ! -L $PENDING && ! -e $AUTHORITY_PENDING && ! -L $AUTHORITY_PENDING ]] || fail
[[ ! -e $VERIFIER_PENDING && ! -L $VERIFIER_PENDING ]] || fail
cp -a --reflink=never -- "$STAGE" "$PENDING"
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -I \
  "$TRUSTED_VERIFIER" "$PENDING" "$TRUSTED_AUTHORITY" \
  --expected-authority-sha256 "$AUTHORITY_SHA" --content-copy --test-fake-root-copy \
  --verifier-copy-of "$DECLARED_VERIFIER" >/dev/null || fail

chmod 0755 "$PENDING"
mv -T -- "$PENDING" "$TARGET"
chmod 0555 "$TARGET"
TARGET_CREATED=true
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -I \
  "$TRUSTED_VERIFIER" "$TARGET" "$TRUSTED_AUTHORITY" \
  --expected-authority-sha256 "$AUTHORITY_SHA" --content-copy --test-fake-root-copy \
  --verifier-copy-of "$DECLARED_VERIFIER" >/dev/null || fail

install -m 0555 -- "$TRUSTED_VERIFIER" "$VERIFIER_PENDING"
[[ $(sha256sum "$VERIFIER_PENDING" | awk '{print $1}') == "$PINNED_VERIFIER_SHA256" ]] || fail
mv -T -- "$VERIFIER_PENDING" "$VERIFIER_TARGET"
VERIFIER_CREATED=true
cp -- "$TRUSTED_AUTHORITY" "$AUTHORITY_PENDING"
chmod 0444 "$AUTHORITY_PENDING"
[[ $(sha256sum "$AUTHORITY_PENDING" | awk '{print $1}') == "$AUTHORITY_SHA" ]] || fail
mv -T -- "$AUTHORITY_PENDING" "$AUTHORITY_TARGET"
AUTHORITY_CREATED=true

# Publication is complete only if the installed copy still exactly matches the
# reviewed authority when checked by the digest-bound stable verifier.
env -i PATH=/usr/bin:/bin PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -I \
  "$VERIFIER_TARGET" "$TARGET" "$TRUSTED_AUTHORITY" \
  --expected-authority-sha256 "$AUTHORITY_SHA" --content-copy --test-fake-root-copy \
  --verifier-copy-of "$DECLARED_VERIFIER" >/dev/null || fail
PUBLISHED=true

cleanup_trust
trap - EXIT
printf '%s\n' 'release authority v2 candidate provisioned'
