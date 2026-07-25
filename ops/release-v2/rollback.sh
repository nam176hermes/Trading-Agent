#!/usr/bin/env bash
set -euo pipefail

export PYTHONDONTWRITEBYTECODE=1

fail() {
  printf '%s\n' 'release authority v2 rollback rejected' >&2
  exit 2
}

AUTHORITY=''
AUTHORITY_SHA=''
DESTINATION_ROOT=''
TEST_FAKE_ROOT=false
while (($#)); do
  case "$1" in
    --authority) [[ $# -ge 2 ]] || fail; AUTHORITY=$2; shift 2 ;;
    --authority-sha256) [[ $# -ge 2 ]] || fail; AUTHORITY_SHA=$2; shift 2 ;;
    --destination-root) [[ $# -ge 2 ]] || fail; DESTINATION_ROOT=$2; shift 2 ;;
    --test-fake-root) TEST_FAKE_ROOT=true; shift ;;
    *) fail ;;
  esac
done

# Candidate-only Task 5 never moved an active pointer. Rollback is therefore a
# read-only proof that the prior authority remains current and both trees exist.
[[ $EUID -ne 0 ]] || fail
[[ $TEST_FAKE_ROOT == true && $DESTINATION_ROOT == /* && $DESTINATION_ROOT != / ]] || fail
[[ $AUTHORITY == /* && $AUTHORITY_SHA =~ ^[0-9a-f]{64}$ ]] || fail
[[ -f $AUTHORITY && ! -L $AUTHORITY ]] || fail
[[ $(sha256sum "$AUTHORITY" | awk '{print $1}') == "$AUTHORITY_SHA" ]] || fail
[[ -d $DESTINATION_ROOT && ! -L $DESTINATION_ROOT ]] || fail
[[ $(realpath -e -- "$DESTINATION_ROOT") == "$DESTINATION_ROOT" ]] || fail

readarray -t BINDINGS < <(
  /usr/bin/python3 -I -c '
import json, pathlib, sys
d=json.loads(pathlib.Path(sys.argv[1]).read_bytes())
assert d.get("schema_version") == 3 and d.get("authority_kind") == "STATIC_RELEASE"
print(d["installation_root"])
print(d["prior_release_sha256"])
' "$AUTHORITY"
) || fail
[[ ${#BINDINGS[@]} -eq 2 ]] || fail
INSTALL_ROOT=${BINDINGS[0]}
PRIOR_SHA=${BINDINGS[1]}
[[ $INSTALL_ROOT == /opt/trading-agent-v2/releases/* && $PRIOR_SHA =~ ^[0-9a-f]{64}$ ]] || fail

CURRENT_RELEASE="$DESTINATION_ROOT/opt/trading-agent-v2/current"
CURRENT_AUTHORITY="$DESTINATION_ROOT/etc/trading-agent/release-authority-v2/current.json"
INSTALLED="${DESTINATION_ROOT%/}$INSTALL_ROOT"
AUTHORITY_BASE="$DESTINATION_ROOT/etc/trading-agent/release-authority-v2"
PUBLISHED_AUTHORITY="$AUTHORITY_BASE/$AUTHORITY_SHA.json"
PRIOR_AUTHORITY="$AUTHORITY_BASE/$PRIOR_SHA.json"
[[ -L $CURRENT_RELEASE && -L $CURRENT_AUTHORITY && -d $INSTALLED ]] || fail
[[ -f $PUBLISHED_AUTHORITY && ! -L $PUBLISHED_AUTHORITY ]] || fail
[[ $(sha256sum "$PUBLISHED_AUTHORITY" | awk '{print $1}') == "$AUTHORITY_SHA" ]] || fail
cmp -s -- "$AUTHORITY" "$PUBLISHED_AUTHORITY" || fail
[[ -f $PRIOR_AUTHORITY && ! -L $PRIOR_AUTHORITY ]] || fail
[[ $(readlink -e -- "$CURRENT_AUTHORITY") == "$PRIOR_AUTHORITY" ]] || fail
[[ $(sha256sum "$PRIOR_AUTHORITY" | awk '{print $1}') == "$PRIOR_SHA" ]] || fail

readarray -t PRIOR_BINDINGS < <(
  /usr/bin/python3 -I -c '
import json, pathlib, re, sys
d=json.loads(pathlib.Path(sys.argv[1]).read_bytes())
schema_version=d.get("schema_version")
assert schema_version in {2, 3} and d.get("authority_kind") == "STATIC_RELEASE"
source=d.get("source")
assert isinstance(source, dict)
commit=source.get("commit")
root=d.get("installation_root")
assert isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit)
assert root == f"/opt/trading-agent-v2/releases/{commit}"
print(root)
' "$PRIOR_AUTHORITY"
) || fail
[[ ${#PRIOR_BINDINGS[@]} -eq 1 ]] || fail
PRIOR_INSTALL_ROOT=${PRIOR_BINDINGS[0]}
PRIOR_INSTALLED="${DESTINATION_ROOT%/}$PRIOR_INSTALL_ROOT"
[[ -d $PRIOR_INSTALLED && ! -L $PRIOR_INSTALLED ]] || fail
[[ $(realpath -e -- "$INSTALLED") == "$INSTALLED" ]] || fail
[[ $(realpath -e -- "$PRIOR_INSTALLED") == "$PRIOR_INSTALLED" ]] || fail
[[ $(readlink -e -- "$CURRENT_RELEASE") == "$PRIOR_INSTALLED" ]] || fail

printf '%s\n' 'release authority v2 rollback verified; pointers unchanged'
