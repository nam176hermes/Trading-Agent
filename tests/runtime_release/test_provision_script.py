from __future__ import annotations

import os
from pathlib import Path
import subprocess
import hashlib
import json
import re
import shutil
import stat
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
OPS = ROOT / "ops" / "phase4b"
APP_COMMIT = "fdc085a05019d700ccbce59370941e2c97ef899a"
BACKEND_COMMIT = "41f055b48033714c660f44cc20498b7545366e75"
GENERATED_ENV_NAMES = (
    "control-api.env",
    "job-api.env",
    "job-scheduler.env",
    "job-worker.env",
)
SERVICE_ENV_NAMES = {
    "trading-control-api.service": "control-api.env",
    "trading-job-api.service": "job-api.env",
    "trading-job-scheduler.service": "job-scheduler.env",
    "trading-job-worker.service": "job-worker.env",
}
SAFE_ENVIRONMENT = {
    "TRADING_MODE": "paper",
    "LIVE_EXECUTION_ENABLED": "false",
    "LIVE_TRADING_APPROVED": "false",
}
FAKEROOT_STAGING_ID = 1000


def _text(name: str) -> str:
    return (OPS / name).read_text(encoding="utf-8")


def _private_fakeroot_test_root() -> Path:
    """Fakeroot ownership proof requires a real mode-preserving private root."""
    root = Path("/run/user") / str(os.geteuid())
    try:
        info = root.stat()
    except OSError as error:
        raise AssertionError(f"private fakeroot test root is unavailable: {error}")
    if (
        not root.is_dir()
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
    ):
        raise AssertionError("private fakeroot test root is not owner-only")
    return root


def _assert_exact_safe_environment(path: Path) -> None:
    assignments = [
        line
        for raw_line in path.read_text(encoding="utf-8").splitlines()
        if (line := raw_line.strip()) and not line.startswith("#")
    ]
    observed = [
        assignment
        for assignment in assignments
        if assignment.partition("=")[0] in SAFE_ENVIRONMENT
    ]
    expected = [f"{key}={value}" for key, value in SAFE_ENVIRONMENT.items()]
    assert sorted(observed) == sorted(expected)


def _installed_env_validation_block() -> str:
    text = _text("verify-installed.sh")
    start = text.index("for env_file in /etc/trading-agent/control-api.env")
    end = text.index("\ndone\n", start) + len("\ndone\n")
    return text[start:end]


def _write_installed_envs(root: Path) -> None:
    root.mkdir(parents=True)
    content = "\n".join(
        f"{key}={value}" for key, value in SAFE_ENVIRONMENT.items()
    ) + "\n"
    for name in GENERATED_ENV_NAMES:
        target = root / name
        target.write_text(content, encoding="utf-8")
        target.chmod(0o600)


def _run_installed_env_validation(root: Path) -> subprocess.CompletedProcess[str]:
    fixture_metadata = subprocess.run(
        ["stat", "-c", "%u:%g:%a", str(root / GENERATED_ENV_NAMES[0])],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    block = _installed_env_validation_block().replace(
        "/etc/trading-agent", '"$ENV_ROOT"'
    ).replace(
        "1000:1000:600", fixture_metadata
    )
    return subprocess.run(
        ["bash", "-c", "set -euo pipefail\nfail() { exit 2; }\n" + block],
        capture_output=True,
        text=True,
        env={"ENV_ROOT": str(root), "PATH": "/usr/bin:/bin"},
    )


def test_staging_is_frozen_offline_and_never_publishes_semantic_inputs() -> None:
    text = _text("stage-releases.sh")
    assert APP_COMMIT in text and BACKEND_COMMIT in text
    assert "UV_OFFLINE=1" in text
    assert 'readlink -f "$(command -v python3.11)"' in text
    assert "build_phase4_release.py" in text
    assert "verify-release.py" in text
    assert "generate_phase4_command_manifest.py" in text
    assert "generate_phase4_runtime_authority.py" in text
    assert " archive --format=tar" in text
    assert "BOOTSTRAP" in text
    assert '"$APP_REPOSITORY/scripts/' not in text
    assert '/usr/bin/python3 -I "$STANDALONE_VERIFIER"' in text
    assert text.index('/usr/bin/python3 -I "$STANDALONE_VERIFIER"') < text.index(
        '"$APP_RELEASE/.venv/bin/python3.11"'
    )
    assert text.rindex('"$APP_RELEASE/.venv/bin/python3.11"') < text.rindex(
        '/usr/bin/python3 -I "$STANDALONE_VERIFIER"'
    )
    assert text.rindex("-name __pycache__") > text.rindex(
        '"$APP_RELEASE/.venv/bin/python3.11"'
    )
    assert "STAGE SEALED: no staged executable may run after this point" in text
    assert "seal_version:1" in text
    assert "staging_root:$staging_root" in text
    assert "build_phase4_semantic_manifest.py" not in text
    assert "services.semantic_input_refresher" not in text
    assert "sudo" not in text


def test_root_installer_is_root_only_create_only_and_never_mutates_systemd() -> None:
    text = _text("provision-root.sh")
    assert "EUID" in text
    assert APP_COMMIT in text and BACKEND_COMMIT in text
    assert "OPT_ROOT/releases/app-" in text
    assert "OPT_ROOT/releases/backend-" in text
    assert "/etc/systemd/user" in text
    assert "/home/thenam176/.config/systemd/user" in text
    assert "phase4-runtime-authority.json" in text
    assert "research-input-manifests" in text
    assert '"$RUN_ROOT/research-home/scratchpad"' in text
    assert 'exact_children "$RUN_ROOT/research-home" scratchpad' in text
    assert "postgres-jobs.env" in text
    assert "trading_jobs" in text
    assert "openssl rand" in text
    assert "sudo" not in text
    assert "systemctl" not in text
    assert "enable" not in text
    assert "start " not in text
    assert "restart" not in text
    assert "releases/current" not in text
    assert "ln -s" not in text
    assert "rm -rf" not in text
    assert "--reflink=auto" in text
    assert text.index('install_exact_file "$APP_MANIFEST_STAGE"') < text.index(
        'install_release "$APP_STAGE"'
    )


def test_installer_rejects_links_special_files_hardlinks_and_wrong_metadata() -> None:
    text = _text("provision-root.sh")
    for required in (
        "-type l", "-type f", "-links +1", "wrong staging owner",
        "wrong staging mode", "release verification failed", "sha256sum",
        "manifest_file_sha256",
    ):
        assert required in text
    assert 'STAGING_PYTHON=' not in text
    assert '"$APP_STAGE/.venv/bin/python3.11"' not in text
    assert '/usr/bin/python3 -I "$STANDALONE_VERIFIER"' in text
    assert ".units[$unit]" in text and "runtime_authority_sha256" in text


def test_installer_protects_secrets_and_assigns_user_env_ownership() -> None:
    text = _text("provision-root.sh")
    assert "chmod 0600" in text
    assert "RUNTIME_UID=1000" in text and "RUNTIME_GID=1000" in text
    assert 'chown "$RUNTIME_UID:$RUNTIME_GID"' in text
    assert "TRADING_JOB_API_TOKEN" in text
    assert "TRADING_JOB_API_PRINCIPAL_TYPE=OPERATOR" in text
    assert "TRADING_JOB_API_PRINCIPAL_ID=dashboard-service" in text
    assert "JOBS_DATABASE_ENV_SOURCE" in text
    assert "READER_DATABASE_ENV_SOURCE" in text
    assert "canonicalize_database_env" in text
    assert 'values[TRADING_DATABASE_USER]} == "$expected_role"' in text
    assert "trading_jobs" in text and "trading_reader" in text
    assert "duplicate database environment key" not in text
    assert "local -a required" in text
    assert "local -A values" in text
    assert "set -x" not in text
    assert "cat /etc/trading-agent" not in text
    assert "echo $" not in text


def test_verify_installed_rejects_user_shadow_and_checks_global_fragment() -> None:
    text = _text("verify-installed.sh")
    assert "EUID -ne 1000" in text
    assert "APPROVED_METADATA_SHA256" in text
    assert "/usr/bin/python3 -I \"$STANDALONE_VERIFIER\"" in text
    assert "/etc/systemd/user" in text
    assert "/etc/systemd/system" in text
    assert ".config/systemd/user" in text
    assert "FragmentPath" in text
    assert "DropInPaths" in text
    assert "ExecStart" in text
    assert "normalize_execstart" in text
    assert "start_time=[n/a]" not in text
    assert "exact_children" in text
    assert "--require-semantic" in text
    assert "attest_current_semantic_inputs" in text
    for runtime_path in (
        "/home/thenam176/.local/share/trading-agent/research-input",
        "/home/thenam176/.local/share/trading-agent/job-artifacts",
        "/home/thenam176/.local/share/trading-agent/research-output",
        "/home/thenam176/.local/run/trading-agent/research-home/scratchpad",
    ):
        assert f"exact_children {runtime_path}" not in text
    assert ".phase4-v1.json.lock" in text
    assert "command_manifest_sha256" in text
    assert "runtime_authority_sha256" in text
    assert ".seal_version == 1" in text
    assert '.staging_root == $root' in text
    assert ".units[$unit]" in text
    assert "systemd-analyze" in text
    assert "127.0.0.1:8400" in text
    assert "127.0.0.1:8401" in text
    assert "is-enabled" in text
    assert APP_COMMIT in text and BACKEND_COMMIT in text
    assert "sha256sum" in text
    assert "sudo" not in text


def test_verify_installed_requires_safe_environment_for_all_four_envs() -> None:
    checks = _installed_env_validation_block()

    for name in GENERATED_ENV_NAMES:
        assert f"/etc/trading-agent/{name}" in checks
    for key, value in SAFE_ENVIRONMENT.items():
        assert (
            f"[[ $(grep -Ec '^[[:space:]]*{key}=' \"$env_file\" || true) -eq 1 ]] || fail"
            in checks
        )
        assert f"grep -qx '{key}={value}' \"$env_file\" || fail" in checks


def _assert_generated_env_pairing(unit_root: Path) -> None:
    observed: dict[str, str] = {}
    for service in unit_root.glob("*.service"):
        for raw_line in service.read_text(encoding="utf-8").splitlines():
            key, separator, value = raw_line.partition("=")
            if key.strip() != "EnvironmentFile":
                continue
            assert separator
            environment_file = value.strip()
            assert environment_file.startswith("/etc/trading-agent/")
            assert service.name not in observed
            observed[service.name] = environment_file

    expected = {
        service: f"/etc/trading-agent/{env_name}"
        for service, env_name in SERVICE_ENV_NAMES.items()
    }
    assert observed == expected
    assert set(observed.values()) == {
        f"/etc/trading-agent/{name}" for name in GENERATED_ENV_NAMES
    }


def test_generated_envs_match_every_service_environment_file() -> None:
    _assert_generated_env_pairing(ROOT / "ops/systemd")


def test_generated_env_pairing_rejects_alternate_subdirectory(
    tmp_path: Path,
) -> None:
    unit_root = tmp_path / "systemd"
    unit_root.mkdir()
    for service, env_name in SERVICE_ENV_NAMES.items():
        directory = "/etc/trading-agent"
        if service == "trading-control-api.service":
            directory += "/alternate"
        (unit_root / service).write_text(
            f"[Service]\nEnvironmentFile={directory}/{env_name}\n",
            encoding="utf-8",
        )

    with pytest.raises(AssertionError):
        _assert_generated_env_pairing(unit_root)


def test_installed_env_validation_accepts_all_four_exact_safe_envs(
    tmp_path: Path,
) -> None:
    env_root = tmp_path / "etc/trading-agent"
    _write_installed_envs(env_root)

    result = _run_installed_env_validation(env_root)

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("name", GENERATED_ENV_NAMES)
@pytest.mark.parametrize("mutation", ("missing", "unsafe", "duplicate"))
@pytest.mark.parametrize(("key", "safe_value"), SAFE_ENVIRONMENT.items())
def test_installed_env_validation_rejects_each_invalid_env(
    tmp_path: Path, name: str, mutation: str, key: str, safe_value: str,
) -> None:
    env_root = tmp_path / "etc/trading-agent"
    _write_installed_envs(env_root)
    target = env_root / name
    assignments = target.read_text(encoding="utf-8").splitlines()
    safe_assignment = f"{key}={safe_value}"
    if mutation == "missing":
        assignments.remove(safe_assignment)
    elif mutation == "unsafe":
        assignments[assignments.index(safe_assignment)] = f"{key}=invalid"
    else:
        assignments.append(safe_assignment)
    target.write_text("\n".join(assignments) + "\n", encoding="utf-8")
    target.chmod(0o600)

    result = _run_installed_env_validation(env_root)

    assert result.returncode == 2


def test_protected_semantic_roots_are_enumerated_only_by_root_installer() -> None:
    provision = _text("provision-root.sh")
    verifier = _text("verify-installed.sh")

    assert 'ensure_directory "$SHARE_ROOT/research-input" 0 0 711' in provision
    assert 'ensure_directory "$ETC_ROOT/research-input-manifests" 0 0 711' in provision
    semantic_validation = provision[
        provision.index("validate_semantic_tree() {"):
        provision.index("validate_runtime_tree \"$SHARE_ROOT/job-artifacts\"")
    ]
    assert 'find "$input_root"' in semantic_validation
    assert 'find "$authority_root"' in semantic_validation

    semantic_checks_start = verifier.index("SEMANTIC_INPUT_ROOT=")
    semantic_checks = verifier[
        semantic_checks_start : verifier.index("for env_file in", semantic_checks_start)
    ]
    assert re.search(r"\bfind\b", semantic_checks) is None
    for root in ("SEMANTIC_INPUT_ROOT", "SEMANTIC_AUTHORITY_ROOT"):
        assert f'[[ -d "${root}" && ! -L "${root}" ]] || fail' in semantic_checks
    assert 'stat -c %u:%g:%a "$SEMANTIC_INPUT_ROOT") == 0:0:711' in verifier
    assert 'stat -c %u:%g:%a "$SEMANTIC_AUTHORITY_ROOT") == 0:0:711' in verifier
    assert "SEMANTIC_AUTHORITY_ROOT=/etc/trading-agent/research-input-manifests" in verifier
    assert 'ACTIVE_SEMANTIC="$SEMANTIC_AUTHORITY_ROOT/phase4-v1.json"' in verifier
    lock = "/etc/trading-agent/research-input-manifests/.phase4-v1.json.lock"
    lock_check = verifier[
        verifier.index(f"[[ -f {lock}") : verifier.index("validate_runtime_tree()")
    ]
    assert f"-f {lock}" in lock_check
    assert f"! -L {lock}" in lock_check
    assert f"stat -c %u:%g:%a {lock}) == 0:0:600" in lock_check
    assert f"stat -c %h {lock}) == 1" in lock_check
    require_start = verifier.index("if [[ $REQUIRE_SEMANTIC == true ]]")
    require_semantic = verifier[
        require_start : verifier.index("\nfi\n", require_start) + len("\nfi\n")
    ]
    assert "[[ -f $ACTIVE_SEMANTIC && ! -L $ACTIVE_SEMANTIC ]] || fail" in require_semantic
    assert "attest_current_semantic_inputs" in require_semantic


def test_execstart_normalization_ignores_runtime_fields_but_not_command() -> None:
    text = _text("verify-installed.sh")
    start = text.index("normalize_execstart() {")
    end = text.index("# end normalize_execstart", start)
    function = text[start:end]
    path = f"/opt/trading-agent-phase4/releases/app-{APP_COMMIT}/.venv/bin/python3.11"
    argv = f"{path} -m apps.job_api.main"
    before = (
        f"{{ path={path} ; argv[]={argv} ; ignore_errors=no ; "
        "start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }"
    )
    after = (
        f"{{ path={path} ; argv[]={argv} ; ignore_errors=no ; "
        "start_time=[Sun 2026-07-12 20:00:00 UTC] ; "
        "stop_time=[Sun 2026-07-12 20:00:02 UTC] ; pid=48123 ; "
        "code=exited ; status=0/0 }"
    )

    def normalize(serialized: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", "-c", function + '\nnormalize_execstart "$1"', "bash", serialized],
            capture_output=True,
            text=True,
        )

    expected = f"{path}\n{argv}\n"
    assert normalize(before).stdout == expected
    assert normalize(after).stdout == expected
    assert normalize(after.replace("apps.job_api.main", "apps.job_api.evil")).stdout != expected
    assert normalize(after.replace(path, "/tmp/python", 1)).stdout != expected


def test_fragment_path_identity_accepts_only_the_same_existing_canonical_file(
    tmp_path: Path,
) -> None:
    text = _text("verify-installed.sh")
    marker = "same_canonical_path() {"
    end_marker = "# end same_canonical_path"
    assert marker in text
    assert end_marker in text
    start = text.index(marker)
    end = text.index(end_marker, start)
    function = text[start:end]

    actual_parent = tmp_path / "actual"
    actual_parent.mkdir()
    expected = actual_parent / "example.service"
    expected.write_text("unit\n", encoding="utf-8")
    alias_parent = tmp_path / "alias"
    alias_parent.symlink_to(actual_parent, target_is_directory=True)
    different = actual_parent / "different.service"
    different.write_text("other\n", encoding="utf-8")

    def same_path(reported: str, installed: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "bash",
                "-c",
                function + '\nsame_canonical_path "$1" "$2"',
                "bash",
                reported,
                installed,
            ],
            capture_output=True,
            text=True,
        )

    assert same_path(str(expected), str(expected)).returncode == 0
    assert same_path(str(alias_parent / expected.name), str(expected)).returncode == 0
    for rejected in (
        str(different),
        "relative.service",
        str(tmp_path / "missing.service"),
        "",
        f"{expected}\nforged.service",
    ):
        assert same_path(rejected, str(expected)).returncode != 0


def test_fragment_path_checks_share_canonical_identity_without_weakening_attestations() -> None:
    text = _text("verify-installed.sh")

    assert (
        'same_canonical_path "$(systemctl --user show "$unit" '
        '--property=FragmentPath --value)" "$installed" || fail'
    ) in text
    assert (
        'same_canonical_path "$(systemctl show "$unit" '
        '--property=FragmentPath --value)" "$installed" || fail'
    ) in text
    assert '[[ $(sha256sum "$installed" | awk \'{print $1}\') == "$(jq -er' in text
    assert '[[ ! -e $USER_SHADOW_ROOT/$unit && ! -L $USER_SHADOW_ROOT/$unit ]] || fail' in text
    assert '--property=DropInPaths --value' in text
    assert text.count('--property=ExecStart --value') == 2
    assert "normalize_execstart" in text and "attest_execstart" in text


def test_scripts_have_valid_shell_syntax() -> None:
    for name in ("stage-releases.sh", "provision-root.sh", "verify-installed.sh"):
        assert os.access(OPS / name, os.X_OK)
        assert "export PYTHONDONTWRITEBYTECODE=1" in _text(name)
        completed = subprocess.run(
            ["bash", "-n", str(OPS / name)], capture_output=True, text=True,
        )
        assert completed.returncode == 0, completed.stderr


def test_root_installer_refuses_non_root_before_reading_staging(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        return
    completed = subprocess.run(
        [str(OPS / "provision-root.sh"), str(tmp_path / "missing")],
        capture_output=True, text=True,
    )
    assert completed.returncode == 2
    assert completed.stdout == ""
    assert completed.stderr.strip() == "phase 4b root provisioning requires EUID 0"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _release_manifest(release: Path, manifest: Path, release_type: str, commit: str) -> tuple[str, str]:
    entries: list[dict[str, object]] = []
    empty = hashlib.sha256(b"").hexdigest()
    for path in sorted(release.rglob("*"), key=lambda item: os.fsencode(item.relative_to(release).as_posix())):
        info = path.lstat()
        relative = path.relative_to(release).as_posix()
        is_directory = path.is_dir()
        entries.append({
            "path": relative,
            "type": "directory" if is_directory else "file",
            "mode": f"{stat.S_IMODE(info.st_mode):04o}",
            "size": 0 if is_directory else info.st_size,
            "sha256": empty if is_directory else _sha(path),
        })
    canonical_entries = json.dumps(entries, ensure_ascii=False, separators=(",", ":")).encode()
    document = {
        "manifest_version": 1, "release_type": release_type, "git_commit": commit,
        "python_identity": "CPython 3.11.15", "entries": entries,
        "aggregate_sha256": hashlib.sha256(canonical_entries).hexdigest(),
    }
    canonical = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()
    raw = canonical + b"\n"
    manifest.write_bytes(raw)
    manifest.chmod(0o644)
    return hashlib.sha256(canonical).hexdigest(), hashlib.sha256(raw).hexdigest()


def _fixture_stage(tmp_path: Path) -> tuple[Path, str]:
    stage = tmp_path / "stage"
    (stage / "releases" / f"app-{APP_COMMIT}" / ".venv/bin").mkdir(parents=True)
    (stage / "releases" / f"backend-{BACKEND_COMMIT}" / ".venv/bin").mkdir(parents=True)
    (stage / "manifests").mkdir()
    (stage / "units").mkdir()
    verifier = stage / "releases" / f"app-{APP_COMMIT}" / ".venv/bin/python3.11"
    verifier.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    verifier.chmod(0o755)
    backend_python = stage / "releases" / f"backend-{BACKEND_COMMIT}" / ".venv/bin/python3.11"
    backend_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    backend_python.chmod(0o755)
    for release in (
        stage / "releases" / f"app-{APP_COMMIT}",
        stage / "releases" / f"backend-{BACKEND_COMMIT}",
    ):
        for directory in release.rglob("*"):
            if directory.is_dir():
                directory.chmod(0o755)
    app_manifest = stage / "manifests" / f"app-{APP_COMMIT}.manifest.json"
    backend_manifest = stage / "manifests" / f"backend-{BACKEND_COMMIT}.manifest.json"
    command = stage / "manifests" / f"commands-{BACKEND_COMMIT}.json"
    authority = stage / "manifests" / "phase4-runtime-authority.json"
    app_canonical, app_raw = _release_manifest(
        stage / "releases" / f"app-{APP_COMMIT}", app_manifest, "phase4-app", APP_COMMIT,
    )
    backend_canonical, backend_raw = _release_manifest(
        stage / "releases" / f"backend-{BACKEND_COMMIT}", backend_manifest,
        "phase4-backend", BACKEND_COMMIT,
    )
    for path, payload in ((command, b'{"fixture":"command"}\n'), (authority, b'{"fixture":"authority"}\n')):
        path.write_bytes(payload)
        path.chmod(0o444)
    unit_hashes: dict[str, str] = {}
    for unit in (
        "trading-safety-state-export.service", "trading-safety-state-export.timer",
        "trading-control-api.service",
        "trading-job-api.service", "trading-job-worker.service",
        "trading-job-scheduler.service", "trading-job-scheduler.timer",
        "trading-semantic-input-refresh.service", "trading-semantic-input-refresh.timer",
    ):
        shutil.copyfile(ROOT / "ops/systemd" / unit, stage / "units" / unit)
        (stage / "units" / unit).chmod(0o600)
        unit_hashes[unit] = _sha(stage / "units" / unit)
    metadata = {
        "schema_version": 1,
        "seal_version": 1,
        "staging_root": str(stage),
        "staging_uid": 0,
        "staging_gid": 0,
        "application": {
            "commit": APP_COMMIT,
            "manifest_sha256": app_canonical,
            "manifest_file_sha256": app_raw,
            "python_identity": "CPython 3.11.15",
        },
        "backend": {
            "commit": BACKEND_COMMIT,
            "manifest_sha256": backend_canonical,
            "manifest_file_sha256": backend_raw,
            "python_identity": "CPython 3.11.15",
        },
        "command_manifest_sha256": _sha(command),
        "runtime_authority_sha256": _sha(authority),
        "standalone_verifier_sha256": _sha(OPS / "verify-release.py"),
        "units": unit_hashes,
    }
    metadata_path = stage / "staging-metadata.json"
    metadata_path.write_text(json.dumps(metadata, separators=(",", ":")) + "\n")
    metadata_path.chmod(0o600)
    stage.chmod(0o700)
    return stage, _sha(metadata_path)


def _run_root_fixture(
    stage: Path,
    digest: str,
    root: Path,
    jobs_db_env: Path,
    reader_db_env: Path,
    *,
    tamper_pending: bool = False,
    swap_staged_unit_after_validation: bool = False,
) -> subprocess.CompletedProcess[str]:
    production = (OPS / "provision-root.sh").read_text(encoding="utf-8")
    staging_identity = json.loads((stage / "staging-metadata.json").read_text(encoding="utf-8"))
    patched = production.replace("RUNTIME_UID=1000", "RUNTIME_UID=0", 1)
    patched = patched.replace("RUNTIME_GID=1000", "RUNTIME_GID=0", 1)
    patched = patched.replace(
        'JOBS_DATABASE_ENV_SOURCE="/home/thenam176/.config/trading-agent/postgres-jobs.env"',
        f'JOBS_DATABASE_ENV_SOURCE="{jobs_db_env}"', 1,
    )
    patched = patched.replace(
        'READER_DATABASE_ENV_SOURCE="/home/thenam176/.config/trading-agent/postgres-reader.env"',
        f'READER_DATABASE_ENV_SOURCE="{reader_db_env}"', 1,
    )
    patched = patched.replace('DESTINATION_ROOT=""', f'DESTINATION_ROOT="{root}"', 1)
    if tamper_pending:
        copy = 'cp --archive --reflink=auto "$source" "$PENDING"'
        patched = patched.replace(
            copy,
            copy + '\nprintf %s tampered >>"$PENDING/.venv/bin/python3.11"',
            1,
        )
    if swap_staged_unit_after_validation:
        boundary = "USER_SHADOW_ROOT=$(destination /home/thenam176/.config/systemd/user)"
        replacement = "\n".join(
            (
                "printf '%s\\n' 'attacker-controlled-unit' >\"$STAGING_ROOT/units/.swap\"",
                'chmod 0600 "$STAGING_ROOT/units/.swap"',
                'mv "$STAGING_ROOT/units/.swap" "$STAGING_ROOT/units/trading-job-api.service"',
                boundary,
            )
        )
        assert boundary in patched
        patched = patched.replace(boundary, replacement, 1)
    if staging_identity["staging_uid"] == 0 and staging_identity["staging_gid"] == 0:
        patched = patched.replace(
            '[[ $STAGING_UID -eq 1000 && $STAGING_GID -eq 1000 ]] || fail',
            '[[ $STAGING_UID -eq 0 && $STAGING_GID -eq 0 ]] || fail', 1,
        )
    else:
        # unshare maps the calling user (the real staging owner) to namespace root.
        # Retain the signed metadata identity check, but verify the namespace view as uid 0.
        patched = patched.replace(
            '[[ $(stat -c %u "$STAGING_ROOT") -eq $STAGING_UID ]]',
            '[[ $(stat -c %u "$STAGING_ROOT") -eq 0 ]]', 1,
        )
        patched = patched.replace(
            r'find "$TRUSTED_STAGING_ROOT" -mindepth 1 \( ! -uid "$STAGING_UID" -o ! -gid "$STAGING_GID" \)',
            r'find "$TRUSTED_STAGING_ROOT" -mindepth 1 \( ! -uid 0 -o ! -gid 0 \)', 1,
        )
        patched = patched.replace(
            '--uid "$STAGING_UID" --gid "$STAGING_GID" --manifest-mode 0644',
            '--uid 0 --gid 0 --manifest-mode 0644', 2,
        )
    with tempfile.TemporaryDirectory(prefix="phase4b-root-harness-", dir=stage.parent) as raw:
        harness_root = Path(raw)
        harness = harness_root / "provision-root.sh"
        harness.write_text(patched, encoding="utf-8")
        harness.chmod(0o755)
        shutil.copyfile(OPS / "verify-release.py", harness_root / "verify-release.py")
        command = f"exec {harness} {stage} {digest}"
        return subprocess.run(
            ["unshare", "--user", "--map-root-user", "bash", "-c", command],
            capture_output=True, text=True,
        )


def _run_fakeroot_fixture(
    stage: Path,
    digest: str,
    root: Path,
    jobs_db_env: Path,
    reader_db_env: Path,
) -> subprocess.CompletedProcess[str]:
    production = (OPS / "provision-root.sh").read_text(encoding="utf-8")
    patched = production.replace("if [[ $EUID -ne 0 ]]; then", "if false; then", 1)
    patched = patched.replace(
        'JOBS_DATABASE_ENV_SOURCE="/home/thenam176/.config/trading-agent/postgres-jobs.env"',
        f'JOBS_DATABASE_ENV_SOURCE="{jobs_db_env}"', 1,
    )
    patched = patched.replace(
        'READER_DATABASE_ENV_SOURCE="/home/thenam176/.config/trading-agent/postgres-reader.env"',
        f'READER_DATABASE_ENV_SOURCE="{reader_db_env}"', 1,
    )
    patched = patched.replace('DESTINATION_ROOT=""', f'DESTINATION_ROOT="{root}"', 1)
    with tempfile.TemporaryDirectory(prefix="phase4b-fakeroot-harness-", dir=stage.parent) as raw:
        harness_root = Path(raw)
        harness = harness_root / "provision-root.sh"
        harness.write_text(patched, encoding="utf-8")
        harness.chmod(0o755)
        shutil.copyfile(OPS / "verify-release.py", harness_root / "verify-release.py")
        command = (
            'chown -R 1000:1000 "$1"; chown 1000:1000 "$2" "$3"; '
            'exec "$4" "$1" "$5"'
        )
        return subprocess.run(
            [
                "fakeroot", "--", "bash", "-c", command, "phase4b-fakeroot",
                str(stage), str(jobs_db_env), str(reader_db_env), str(harness), digest,
            ],
            capture_output=True, text=True,
        )


def _write_db_env(path: Path, role: str, *extra_lines: str) -> None:
    lines = [
        "TRADING_DATABASE_HOST=127.0.0.1",
        "TRADING_DATABASE_PORT=55432",
        "TRADING_DATABASE_NAME=trading_agent",
        f"TRADING_DATABASE_USER={role}",
        "TRADING_DATABASE_PASSWORD=fixture-secret",
        *extra_lines,
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)


def test_root_snapshot_keeps_root_owned_boundary_for_user_owned_stage(
    tmp_path: Path,
) -> None:
    del tmp_path
    with tempfile.TemporaryDirectory(
        prefix="phase4b-fakeroot-",
        dir=_private_fakeroot_test_root(),
    ) as raw:
        native = Path(raw)
        stage, _ = _fixture_stage(native)
        metadata_path = stage / "staging-metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["staging_uid"] = FAKEROOT_STAGING_ID
        metadata["staging_gid"] = FAKEROOT_STAGING_ID
        metadata_path.write_text(json.dumps(metadata, separators=(",", ":")) + "\n")
        metadata_path.chmod(0o600)
        jobs_db_env, reader_db_env = _write_db_envs(native)

        result = _run_fakeroot_fixture(
            stage,
            _sha(metadata_path),
            native / "root",
            jobs_db_env,
            reader_db_env,
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout == "phase 4b root provisioning installed create-only authority\n"


def test_fakeroot_generates_all_four_envs_with_exact_safe_environment(
    tmp_path: Path,
) -> None:
    del tmp_path
    with tempfile.TemporaryDirectory(
        prefix="phase4b-safe-envs-",
        dir=_private_fakeroot_test_root(),
    ) as raw:
        native = Path(raw)
        stage, _ = _fixture_stage(native)
        metadata_path = stage / "staging-metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["staging_uid"] = FAKEROOT_STAGING_ID
        metadata["staging_gid"] = FAKEROOT_STAGING_ID
        metadata_path.write_text(json.dumps(metadata, separators=(",", ":")) + "\n")
        metadata_path.chmod(0o600)
        jobs_db_env, reader_db_env = _write_db_envs(native)
        install_root = native / "root"

        result = _run_fakeroot_fixture(
            stage,
            _sha(metadata_path),
            install_root,
            jobs_db_env,
            reader_db_env,
        )

        assert result.returncode == 0, result.stderr
        env_root = install_root / "etc/trading-agent"
        assert {path.name for path in env_root.glob("*.env")} == set(
            GENERATED_ENV_NAMES
        )
        for name in GENERATED_ENV_NAMES:
            _assert_exact_safe_environment(env_root / name)


def test_fakeroot_rejects_staging_metadata_identity_contradiction(
    tmp_path: Path,
) -> None:
    """The fixture cannot bypass the production fixed-identity boundary."""
    del tmp_path
    with tempfile.TemporaryDirectory(
        prefix="phase4b-fakeroot-mismatch-", dir=_private_fakeroot_test_root()
    ) as raw:
        native = Path(raw)
        stage, _ = _fixture_stage(native)
        metadata_path = stage / "staging-metadata.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["staging_uid"] = FAKEROOT_STAGING_ID + 1
        metadata["staging_gid"] = FAKEROOT_STAGING_ID + 1
        metadata_path.write_text(json.dumps(metadata, separators=(",", ":")) + "\n")
        metadata_path.chmod(0o600)
        jobs_db_env, reader_db_env = _write_db_envs(native)

        result = _run_fakeroot_fixture(
            stage,
            _sha(metadata_path),
            native / "root",
            jobs_db_env,
            reader_db_env,
        )

        assert result.returncode == 2
        assert result.stdout == ""
        assert result.stderr == "phase 4b root provisioning rejected\n"


def test_trusted_snapshot_does_not_depend_on_volatile_run(tmp_path: Path) -> None:
    del tmp_path
    with tempfile.TemporaryDirectory(
        prefix="phase4b-persistent-snapshot-",
    ) as raw:
        native = Path(raw)
        stage, digest = _fixture_stage(native)
        install_root = native / "root"
        install_root.mkdir(mode=0o700)
        (install_root / "run").write_text("volatile storage unavailable\n", encoding="utf-8")
        jobs_db_env, reader_db_env = _write_db_envs(native)

        result = _run_root_fixture(
            stage,
            digest,
            install_root,
            jobs_db_env,
            reader_db_env,
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout == "phase 4b root provisioning installed create-only authority\n"


def _write_db_envs(root: Path, *jobs_extra_lines: str) -> tuple[Path, Path]:
    jobs = root / "postgres-jobs.env"
    reader = root / "postgres-reader.env"
    _write_db_env(jobs, "trading_jobs", *jobs_extra_lines)
    _write_db_env(reader, "trading_reader")
    return jobs, reader


def test_dynamic_staged_install_is_idempotent_and_secret_safe(tmp_path: Path) -> None:
    del tmp_path
    with tempfile.TemporaryDirectory(prefix="phase4b-provision-") as raw:
        native = Path(raw)
        stage, digest = _fixture_stage(native)
        install_root = native / "root"
        install_root.mkdir(mode=0o700)
        jobs_db_env, reader_db_env = _write_db_envs(native)
        first = _run_root_fixture(
            stage, digest, install_root, jobs_db_env, reader_db_env,
        )
        assert first.returncode == 0, first.stderr
        assert "fixture-secret" not in first.stdout + first.stderr
        installed = install_root / "opt/trading-agent-phase4/releases" / f"app-{APP_COMMIT}"
        artifact = install_root / "home/thenam176/.local/share/trading-agent/job-artifacts/job-1.log"
        artifact.write_text("preserve append-only evidence\n", encoding="utf-8")
        artifact.chmod(0o600)
        before = [(p.relative_to(install_root), p.stat().st_mtime_ns) for p in install_root.rglob("*")]
        second = _run_root_fixture(
            stage, digest, install_root, jobs_db_env, reader_db_env,
        )
        assert second.returncode == 0, second.stderr
        after = [(p.relative_to(install_root), p.stat().st_mtime_ns) for p in install_root.rglob("*")]
        assert installed.is_dir()
        assert artifact.read_text(encoding="utf-8") == "preserve append-only evidence\n"
        assert before == after
        assert not list((install_root / "etc/systemd").rglob("*.wants/*"))


def test_dynamic_staged_install_rejects_missing_extra_tamper_mode_links_and_special_files(tmp_path: Path) -> None:
    del tmp_path
    with tempfile.TemporaryDirectory(prefix="phase4b-reject-") as raw:
        native = Path(raw)
        for mutation in (
            "missing", "extra", "metadata_extra", "tamper", "bin_true", "mode",
            "symlink", "hardlink", "special",
        ):
            case = native / mutation
            case.mkdir()
            stage, digest = _fixture_stage(case)
            if mutation == "missing":
                (stage / "manifests" / f"commands-{BACKEND_COMMIT}.json").unlink()
            elif mutation == "extra":
                (stage / "unexpected-extra").write_text("not approved\n")
            elif mutation == "metadata_extra":
                metadata_path = stage / "staging-metadata.json"
                document = json.loads(metadata_path.read_text())
                document["unexpected"] = True
                metadata_path.write_text(json.dumps(document, separators=(",", ":")) + "\n")
                digest = _sha(metadata_path)
            elif mutation == "tamper":
                target = stage / "manifests/phase4-runtime-authority.json"
                target.chmod(0o600)
                target.write_text("tampered\n")
            elif mutation == "bin_true":
                shutil.copyfile(
                    "/bin/true",
                    stage / "releases" / f"app-{APP_COMMIT}" / ".venv/bin/python3.11",
                )
            elif mutation == "mode":
                stage.chmod(0o755)
            elif mutation == "symlink":
                (stage / "unexpected-link").symlink_to("staging-metadata.json")
            elif mutation == "hardlink":
                os.link(stage / "staging-metadata.json", stage / "unexpected-hardlink")
            else:
                os.mkfifo(stage / "unexpected-fifo", 0o600)
            install_root = case / "root"
            install_root.mkdir()
            jobs_db_env, reader_db_env = _write_db_envs(case)
            result = _run_root_fixture(
                stage, digest, install_root, jobs_db_env, reader_db_env,
            )
            assert result.returncode == 2, (mutation, result.stdout, result.stderr)
            assert not (install_root / "opt/trading-agent-phase4").exists()


def test_dynamic_partial_failure_never_overwrites_and_is_retryable(tmp_path: Path) -> None:
    del tmp_path
    with tempfile.TemporaryDirectory(prefix="phase4b-partial-") as raw:
        native = Path(raw)
        stage, digest = _fixture_stage(native)
        install_root = native / "root"
        bad_unit = install_root / "etc/systemd/user/trading-job-api.service"
        bad_unit.parent.mkdir(parents=True, mode=0o755)
        bad_unit.write_text("operator-owned-different-content\n")
        bad_unit.chmod(0o644)
        jobs_db_env, reader_db_env = _write_db_envs(native)
        failed = _run_root_fixture(
            stage, digest, install_root, jobs_db_env, reader_db_env,
        )
        assert failed.returncode == 2
        assert bad_unit.read_text() == "operator-owned-different-content\n"
        release_root = install_root / "opt/trading-agent-phase4/releases"
        manifest_root = install_root / "opt/trading-agent-phase4/manifests"
        assert not any(release_root.iterdir())
        assert not any(manifest_root.iterdir())
        assert not (install_root / "etc/trading-agent/job-api.env").exists()
        assert list(bad_unit.parent.iterdir()) == [bad_unit]
        assert not list(install_root.rglob("*.installing.*"))
        bad_unit.unlink()
        retried = _run_root_fixture(
            stage, digest, install_root, jobs_db_env, reader_db_env,
        )
        assert retried.returncode == 0, retried.stderr


def test_dynamic_copy_tamper_is_rejected_before_release_publication(tmp_path: Path) -> None:
    del tmp_path
    with tempfile.TemporaryDirectory(prefix="phase4b-copy-tamper-") as raw:
        native = Path(raw)
        stage, digest = _fixture_stage(native)
        install_root = native / "root"
        install_root.mkdir()
        jobs_db_env, reader_db_env = _write_db_envs(native)

        result = _run_root_fixture(
            stage, digest, install_root, jobs_db_env, reader_db_env,
            tamper_pending=True,
        )

        assert result.returncode == 2
        releases = install_root / "opt/trading-agent-phase4/releases"
        assert not (releases / f"app-{APP_COMMIT}").exists()
        assert not list(releases.glob("*.installing.*"))


def test_dynamic_staged_unit_swap_cannot_publish_unverified_bytes(tmp_path: Path) -> None:
    del tmp_path
    with tempfile.TemporaryDirectory(prefix="phase4b-unit-race-") as raw:
        native = Path(raw)
        stage, digest = _fixture_stage(native)
        approved_unit = ROOT / "ops/systemd/trading-job-api.service"
        install_root = native / "root"
        install_root.mkdir()
        jobs_db_env, reader_db_env = _write_db_envs(native)

        result = _run_root_fixture(
            stage,
            digest,
            install_root,
            jobs_db_env,
            reader_db_env,
            swap_staged_unit_after_validation=True,
        )

        assert result.returncode == 0, result.stderr
        assert (stage / "units/trading-job-api.service").read_text() == "attacker-controlled-unit\n"
        installed = install_root / "etc/systemd/user/trading-job-api.service"
        assert installed.read_bytes() == approved_unit.read_bytes()


def test_dynamic_user_local_unit_shadow_blocks_before_install(tmp_path: Path) -> None:
    del tmp_path
    with tempfile.TemporaryDirectory(prefix="phase4b-shadow-") as raw:
        native = Path(raw)
        stage, digest = _fixture_stage(native)
        install_root = native / "root"
        shadow = install_root / "home/thenam176/.config/systemd/user/trading-job-api.service"
        shadow.parent.mkdir(parents=True)
        shadow.write_text("shadow\n")
        jobs_db_env, reader_db_env = _write_db_envs(native)
        result = _run_root_fixture(
            stage, digest, install_root, jobs_db_env, reader_db_env,
        )
        assert result.returncode == 2
        assert not (install_root / "opt/trading-agent-phase4").exists()


def test_dynamic_database_env_rejects_duplicate_user_and_password_without_leak(
    tmp_path: Path,
) -> None:
    del tmp_path
    with tempfile.TemporaryDirectory(prefix="phase4b-db-env-reject-") as raw:
        native = Path(raw)
        for name, duplicate in (
            ("user", "TRADING_DATABASE_USER=trading_owner"),
            ("password", "TRADING_DATABASE_PASSWORD=do-not-print-this"),
        ):
            case = native / name
            case.mkdir()
            stage, digest = _fixture_stage(case)
            install_root = case / "root"
            install_root.mkdir()
            jobs_db_env, reader_db_env = _write_db_envs(case, duplicate)

            result = _run_root_fixture(
                stage, digest, install_root, jobs_db_env, reader_db_env,
            )

            assert result.returncode == 2
            assert "trading_owner" not in result.stdout + result.stderr
            assert "do-not-print-this" not in result.stdout + result.stderr
            assert not (install_root / "opt/trading-agent-phase4").exists()
