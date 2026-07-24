#!/usr/bin/env bash
set -euo pipefail
umask 077
export PYTHONDONTWRITEBYTECODE=1

APP_COMMIT="fdc085a05019d700ccbce59370941e2c97ef899a"
BACKEND_COMMIT="41f055b48033714c660f44cc20498b7545366e75"
RUNTIME_UID=1000
STANDALONE_VERIFIER_SHA256="8f7cf1bc3161f64e2f9814547c4ccd8a30d67a9bade1268e79767d2e965ca5d5"
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
STANDALONE_VERIFIER="$SCRIPT_DIR/verify-release.py"

fail() {
  printf '%s\n' "phase 4b release staging rejected" >&2
  exit 2
}

[[ $# -eq 3 ]] || fail
APP_REPOSITORY=$1
BACKEND_REPOSITORY=$2
STAGING_ROOT=$3
[[ $APP_REPOSITORY == /* && $BACKEND_REPOSITORY == /* && $STAGING_ROOT == /* ]] || fail
[[ ! -e $STAGING_ROOT && ! -L $STAGING_ROOT ]] || fail
[[ -d $APP_REPOSITORY/.git || -f $APP_REPOSITORY/.git ]] || fail
[[ -d $BACKEND_REPOSITORY/.git || -f $BACKEND_REPOSITORY/.git ]] || fail
[[ $(git -C "$APP_REPOSITORY" rev-parse --verify "${APP_COMMIT}^{commit}") == "$APP_COMMIT" ]] || fail
[[ $(git -C "$BACKEND_REPOSITORY" rev-parse --verify "${BACKEND_COMMIT}^{commit}") == "$BACKEND_COMMIT" ]] || fail
[[ -f $STANDALONE_VERIFIER && ! -L $STANDALONE_VERIFIER ]] || fail
[[ $(sha256sum "$STANDALONE_VERIFIER" | awk '{print $1}') == "$STANDALONE_VERIFIER_SHA256" ]] || fail

PYTHON=$(readlink -f "$(command -v python3.11)") || fail
[[ $($PYTHON -c 'import platform; print(platform.python_version_tuple()[:2] == ("3", "11"))') == True ]] || fail
command -v uv >/dev/null || fail
command -v jq >/dev/null || fail
UV_OFFLINE=1 uv cache dir >/dev/null || fail

mkdir -m 0700 "$STAGING_ROOT"
mkdir -m 0700 "$STAGING_ROOT/releases" "$STAGING_ROOT/manifests" "$STAGING_ROOT/units"
APP_RELEASE="$STAGING_ROOT/releases/app-$APP_COMMIT"
BACKEND_RELEASE="$STAGING_ROOT/releases/backend-$BACKEND_COMMIT"

# Bootstrap only the reviewed builder closure from the exact app Git object.
# No helper is executed from the mutable checkout.
BOOTSTRAP="$STAGING_ROOT/.bootstrap"
mkdir -m 0700 "$BOOTSTRAP" "$BOOTSTRAP/source"
git -C "$APP_REPOSITORY" archive --format=tar --output="$BOOTSTRAP/app-builder.tar" \
  "$APP_COMMIT" -- packages scripts/build_phase4_release.py
/usr/bin/tar --extract --file="$BOOTSTRAP/app-builder.tar" --directory="$BOOTSTRAP/source" \
  --no-same-owner --no-same-permissions
[[ -z $(find "$BOOTSTRAP/source" -type l -print -quit) ]] || fail
[[ -z $(find "$BOOTSTRAP/source" ! -type f ! -type d -print -quit) ]] || fail
[[ -z $(find "$BOOTSTRAP/source" -type f -links +1 -print -quit) ]] || fail
[[ -z $(find "$BOOTSTRAP/source" -perm /0022 -print -quit) ]] || fail
BOOTSTRAP_BUILDER="$BOOTSTRAP/source/scripts/build_phase4_release.py"
[[ -f $BOOTSTRAP_BUILDER && ! -L $BOOTSTRAP_BUILDER ]] || fail

UV_OFFLINE=1 /usr/bin/python3 -I "$BOOTSTRAP_BUILDER" \
  "$APP_REPOSITORY" "$APP_COMMIT" "$APP_RELEASE" \
  --uid "$(id -u)" --gid "$(id -g)" --python "$PYTHON" --release-kind application \
  >"$STAGING_ROOT/.app-build.json"
APP_BUILD_MANIFEST="$STAGING_ROOT/releases/app-$APP_COMMIT.manifest.json"
APP_MANIFEST="$STAGING_ROOT/manifests/app-$APP_COMMIT.manifest.json"
mv "$APP_BUILD_MANIFEST" "$APP_MANIFEST"

UV_OFFLINE=1 /usr/bin/python3 -I "$BOOTSTRAP_BUILDER" \
  "$BACKEND_REPOSITORY" "$BACKEND_COMMIT" "$BACKEND_RELEASE" \
  --uid "$(id -u)" --gid "$(id -g)" --python "$PYTHON" --release-kind backend \
  >"$STAGING_ROOT/.backend-build.json"
BACKEND_BUILD_MANIFEST="$STAGING_ROOT/releases/backend-$BACKEND_COMMIT.manifest.json"
BACKEND_MANIFEST="$STAGING_ROOT/manifests/backend-$BACKEND_COMMIT.manifest.json"
mv "$BACKEND_BUILD_MANIFEST" "$BACKEND_MANIFEST"

APP_DIGEST=$(jq -er '.digest' "$STAGING_ROOT/.app-build.json")
APP_PYTHON_IDENTITY=$(jq -er '.python_identity // empty' "$APP_MANIFEST")
APP_MANIFEST_FILE_SHA256=$(sha256sum "$APP_MANIFEST" | awk '{print $1}')
BACKEND_DIGEST=$(jq -er '.digest' "$STAGING_ROOT/.backend-build.json")
BACKEND_PYTHON_IDENTITY=$(jq -er '.python_identity // empty' "$BACKEND_MANIFEST")
BACKEND_MANIFEST_FILE_SHA256=$(sha256sum "$BACKEND_MANIFEST" | awk '{print $1}')

# Trusted stdlib attestation precedes every staged executable/helper.
/usr/bin/python3 -I "$STANDALONE_VERIFIER" \
  "$APP_RELEASE" "$APP_MANIFEST" "$APP_DIGEST" "$APP_MANIFEST_FILE_SHA256" \
  --commit "$APP_COMMIT" --python-identity "$APP_PYTHON_IDENTITY" \
  --release-type phase4-app --uid "$(id -u)" --gid "$(id -g)" --manifest-mode 0644 \
  >/dev/null || fail
/usr/bin/python3 -I "$STANDALONE_VERIFIER" \
  "$BACKEND_RELEASE" "$BACKEND_MANIFEST" "$BACKEND_DIGEST" "$BACKEND_MANIFEST_FILE_SHA256" \
  --commit "$BACKEND_COMMIT" --python-identity "$BACKEND_PYTHON_IDENTITY" \
  --release-type phase4-backend --uid "$(id -u)" --gid "$(id -g)" --manifest-mode 0644 \
  >/dev/null || fail

COMMAND_MANIFEST="$STAGING_ROOT/manifests/commands-$BACKEND_COMMIT.json"
"$APP_RELEASE/.venv/bin/python3.11" \
  "$APP_RELEASE/scripts/generate_phase4_command_manifest.py" \
  --backend-commit "$BACKEND_COMMIT" --release-source "$BACKEND_RELEASE" \
  --manifest-source "$BACKEND_MANIFEST" --manifest-sha256 "$BACKEND_DIGEST" \
  --python-identity "$BACKEND_PYTHON_IDENTITY" --output "$COMMAND_MANIFEST" \
  --staging-uid "$(id -u)" --staging-gid "$(id -g)" --apply \
  >"$STAGING_ROOT/.command-build.json"
COMMAND_DIGEST=$(jq -er '.document_sha256' "$STAGING_ROOT/.command-build.json")
[[ $(sha256sum "$COMMAND_MANIFEST" | awk '{print $1}') == "$COMMAND_DIGEST" ]] || fail

RUNTIME_AUTHORITY="$STAGING_ROOT/manifests/phase4-runtime-authority.json"
"$APP_RELEASE/.venv/bin/python3.11" \
  "$APP_RELEASE/scripts/generate_phase4_runtime_authority.py" \
  --application-commit "$APP_COMMIT" --application-release-source "$APP_RELEASE" \
  --application-manifest-source "$APP_MANIFEST" --application-manifest-sha256 "$APP_DIGEST" \
  --application-python-identity "$APP_PYTHON_IDENTITY" \
  --backend-commit "$BACKEND_COMMIT" --backend-release-source "$BACKEND_RELEASE" \
  --backend-manifest-source "$BACKEND_MANIFEST" --backend-manifest-sha256 "$BACKEND_DIGEST" \
  --backend-python-identity "$BACKEND_PYTHON_IDENTITY" \
  --command-manifest-source "$COMMAND_MANIFEST" --command-manifest-sha256 "$COMMAND_DIGEST" \
  --output "$RUNTIME_AUTHORITY" --staging-uid "$(id -u)" --staging-gid "$(id -g)" \
  --runtime-uid "$RUNTIME_UID" --apply >"$STAGING_ROOT/.authority-build.json"
AUTHORITY_DIGEST=$(jq -er '.document_sha256' "$STAGING_ROOT/.authority-build.json")
[[ $(sha256sum "$RUNTIME_AUTHORITY" | awk '{print $1}') == "$AUTHORITY_DIGEST" ]] || fail

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
UNIT_SOURCE="$SCRIPT_DIR/../systemd"
for unit in "${!UNIT_SHA256[@]}"; do
  [[ -f $UNIT_SOURCE/$unit && ! -L $UNIT_SOURCE/$unit ]] || fail
  [[ $(sha256sum "$UNIT_SOURCE/$unit" | awk '{print $1}') == "${UNIT_SHA256[$unit]}" ]] || fail
  install -m 0600 "$UNIT_SOURCE/$unit" "$STAGING_ROOT/units/$unit"
done

find "$BOOTSTRAP" -depth -delete
rm "$STAGING_ROOT/.app-build.json" "$STAGING_ROOT/.backend-build.json" \
  "$STAGING_ROOT/.command-build.json" "$STAGING_ROOT/.authority-build.json"

UNITS_JSON='{"trading-control-api.service":"22be1484597b3406988670043eb2e40d4a05bc0bcade4f5503990eb94c98179a","trading-job-api.service":"8d7b368dfeecf654699246dadacc5d0b18d2bdec7fc1a1d7734c62e6e5f144ce","trading-job-scheduler.service":"23e0e64399c7d5d4d8236ae6665e2fca55dcf4ec7c3ab156cac91086c8c7fb60","trading-job-scheduler.timer":"cc4969564b79c6a45a22b2194a4875620e094918f7020fa96eafe8714515ad89","trading-job-worker.service":"3b5514160c1ae80c4e20eb5a98672e889a4c8d3dd9505dc6728efe511be722ea","trading-safety-state-export.service":"b573d42f0d1fc2d9bb9eacdcd2fc7101ecb5ed20bc4c4df557edeb4586161d0c","trading-safety-state-export.timer":"2780fe42c6e8705d3eb1df5310ec15167129aa7b16f1e762f58e8db25794f375","trading-semantic-input-refresh.service":"4f2b5394464bb628d2eaa7e8c7c2d3206f2d1f5ed5588dc84c169f9f0c954c2e","trading-semantic-input-refresh.timer":"a1550940d63e418bc62282ab2372a638fcbcb7ca224b1cda3760bc459bc402ad"}' # gitleaks:allow
METADATA="$STAGING_ROOT/staging-metadata.json"
jq -cn \
  --arg app_commit "$APP_COMMIT" --arg app_digest "$APP_DIGEST" \
  --arg app_file_digest "$APP_MANIFEST_FILE_SHA256" --arg app_identity "$APP_PYTHON_IDENTITY" \
  --arg backend_commit "$BACKEND_COMMIT" --arg backend_digest "$BACKEND_DIGEST" \
  --arg backend_file_digest "$BACKEND_MANIFEST_FILE_SHA256" --arg backend_identity "$BACKEND_PYTHON_IDENTITY" \
  --arg command_digest "$COMMAND_DIGEST" --arg authority_digest "$AUTHORITY_DIGEST" \
  --arg verifier_digest "$STANDALONE_VERIFIER_SHA256" --argjson units "$UNITS_JSON" \
  --arg staging_root "$STAGING_ROOT" \
  --argjson staging_uid "$(id -u)" --argjson staging_gid "$(id -g)" \
  '{schema_version:1,seal_version:1,staging_root:$staging_root,
    staging_uid:$staging_uid,staging_gid:$staging_gid,
    application:{commit:$app_commit,manifest_sha256:$app_digest,manifest_file_sha256:$app_file_digest,python_identity:$app_identity},
    backend:{commit:$backend_commit,manifest_sha256:$backend_digest,manifest_file_sha256:$backend_file_digest,python_identity:$backend_identity},
    command_manifest_sha256:$command_digest,runtime_authority_sha256:$authority_digest,
    standalone_verifier_sha256:$verifier_digest,units:$units}' \
  >"$METADATA"
chmod 0600 "$METADATA"

# Final seal: all staged helpers have finished. Re-attest exact release bytes
# using only the system interpreter and reject any import cache side effect.
[[ -z $(find "$STAGING_ROOT" \( -name __pycache__ -o -name '*.pyc' \) -print -quit) ]] || fail
/usr/bin/python3 -I "$STANDALONE_VERIFIER" \
  "$APP_RELEASE" "$APP_MANIFEST" "$APP_DIGEST" "$APP_MANIFEST_FILE_SHA256" \
  --commit "$APP_COMMIT" --python-identity "$APP_PYTHON_IDENTITY" \
  --release-type phase4-app --uid "$(id -u)" --gid "$(id -g)" --manifest-mode 0644 \
  >/dev/null || fail
/usr/bin/python3 -I "$STANDALONE_VERIFIER" \
  "$BACKEND_RELEASE" "$BACKEND_MANIFEST" "$BACKEND_DIGEST" "$BACKEND_MANIFEST_FILE_SHA256" \
  --commit "$BACKEND_COMMIT" --python-identity "$BACKEND_PYTHON_IDENTITY" \
  --release-type phase4-backend --uid "$(id -u)" --gid "$(id -g)" --manifest-mode 0644 \
  >/dev/null || fail
[[ -z $(find "$STAGING_ROOT" \( -name __pycache__ -o -name '*.pyc' \) -print -quit) ]] || fail
# STAGE SEALED: no staged executable may run after this point.
METADATA_DIGEST=$(sha256sum "$METADATA" | awk '{print $1}')
printf '{"metadata_sha256":"%s","staging_root":"%s"}\n' "$METADATA_DIGEST" "$STAGING_ROOT"
