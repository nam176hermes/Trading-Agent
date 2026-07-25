from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest

from tests.runtime_release.test_v2 import make_release_fixture, PRIOR
from packages.runtime_release.v2 import canonical_json_bytes


ROOT = Path(__file__).resolve().parents[2]
PROVISION = ROOT / "ops/release-v2/provision-root.sh"
ROLLBACK = ROOT / "ops/release-v2/rollback.sh"


@pytest.fixture
def tmp_path() -> Path:
    path = Path(tempfile.mkdtemp(prefix="release-v2-provision-test-", dir="/tmp"))
    try:
        yield path
    finally:
        for item in sorted(path.rglob("*"), key=lambda child: len(child.parts), reverse=True):
            if not item.is_symlink():
                try:
                    item.chmod(0o755 if item.is_dir() else 0o644)
                except OSError:
                    pass
        path.chmod(0o755)
        shutil.rmtree(path, ignore_errors=True)


def _run(
    script: Path,
    *arguments: object,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [os.fspath(script), *(str(item) for item in arguments)]
    if script == PROVISION and "--verifier" in command:
        verifier = Path(command[command.index("--verifier") + 1])
        harness = verifier.parent / "provision-harness"
        harness.mkdir(mode=0o700, exist_ok=True)
        harness_verifier = harness / "verify-stage.py"
        if harness_verifier.exists():
            harness_verifier.chmod(0o644)
        shutil.copyfile(verifier, harness_verifier)
        harness_verifier.chmod(0o555)
        verifier_sha256 = hashlib.sha256(harness_verifier.read_bytes()).hexdigest()
        provision_lines = PROVISION.read_text(encoding="utf-8").splitlines()
        pin_lines = [
            index
            for index, line in enumerate(provision_lines)
            if line.startswith("PINNED_VERIFIER_SHA256=")
        ]
        assert len(pin_lines) == 1
        provision_lines[pin_lines[0]] = f"PINNED_VERIFIER_SHA256='{verifier_sha256}'"
        harness_provision = harness / "provision-root.sh"
        if harness_provision.exists():
            harness_provision.chmod(0o644)
        harness_provision.write_text("\n".join(provision_lines) + "\n", encoding="utf-8")
        harness_provision.chmod(0o755)
        command[0] = os.fspath(harness_provision)
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        env=env,
    )


def test_production_verifier_has_one_runtime_digest_and_exact_provision_pin() -> None:
    verifier = ROOT / "ops/release-v2/verify-stage.py"
    verifier_raw = verifier.read_bytes()
    verifier_sha256 = hashlib.sha256(verifier_raw).hexdigest()
    provision_source = PROVISION.read_text(encoding="utf-8")

    assert f"PINNED_VERIFIER_SHA256='{verifier_sha256}'" in provision_source
    assert "--test-fake-python-runtime" not in verifier_raw.decode("utf-8")
    assert "--test-fake-python-runtime" not in provision_source


def _seed_prior(fake_root: Path) -> Path:
    release = fake_root / "opt/trading-agent-v2/releases" / PRIOR
    release.mkdir(parents=True)
    (release / "prior-evidence").write_text("preserve\n", encoding="utf-8")
    authorities = fake_root / "etc/trading-agent/release-authority-v2"
    authorities.mkdir(parents=True)
    prior = authorities / f"{PRIOR}.json"
    prior.write_text("{}\n", encoding="utf-8")
    current_release = fake_root / "opt/trading-agent-v2/current"
    current_release.symlink_to(release)
    (authorities / "current.json").symlink_to(prior)
    return release


def _write_authority(authority: Path, document: dict[str, object]) -> str:
    unsigned = dict(document)
    unsigned.pop("binding_sha256", None)
    document["binding_sha256"] = hashlib.sha256(canonical_json_bytes(unsigned)[:-1]).hexdigest()
    authority.chmod(0o644)
    authority.write_bytes(canonical_json_bytes(document))
    authority.chmod(0o444)
    return hashlib.sha256(authority.read_bytes()).hexdigest()


def _seed_bound_prior(
    fake_root: Path,
    authority: Path,
    document: dict[str, object],
    *,
    prior_schema_version: int = 2,
) -> tuple[Path, str]:
    prior_commit = "0" * 40
    installation_root = f"/opt/trading-agent-v2/releases/{prior_commit}"
    release = fake_root / installation_root.lstrip("/")
    release.mkdir(parents=True)
    (release / "prior-evidence").write_text("preserve\n", encoding="utf-8")
    prior_document = {
        "authority_kind": "STATIC_RELEASE",
        "installation_root": installation_root,
        "schema_version": prior_schema_version,
        "source": {"commit": prior_commit},
    }
    prior_raw = canonical_json_bytes(prior_document)
    prior_digest = hashlib.sha256(prior_raw).hexdigest()
    authority_base = fake_root / "etc/trading-agent/release-authority-v2"
    authority_base.mkdir(parents=True)
    prior_authority = authority_base / f"{prior_digest}.json"
    prior_authority.write_bytes(prior_raw)
    prior_authority.chmod(0o444)
    (fake_root / "opt/trading-agent-v2/current").symlink_to(release)
    (authority_base / "current.json").symlink_to(prior_authority)
    document["prior_release_sha256"] = prior_digest
    return release, _write_authority(authority, document)


def _minimal_build_inputs(tmp_path: Path) -> tuple[Path, str, Path, Path]:
    repo = tmp_path / "repo"
    required = {
        "packages/runtime_release/v2.py": (
            "import os, pathlib\n"
            "pathlib.Path(os.environ['RELEASE_TEST_MARKER']).touch()\n"
            "raise SystemExit(2)\n"
        ),
        "ops/release-v2/verify-stage.py": "raise SystemExit(2)\n",
        "alembic/versions/0005_job_plane_role_split.py": "revision='0005_job_plane_role_split'\n",
        "alembic/versions/0006_job_transition_database_authority.py": (
            "revision='0006_job_transition_database_authority'\n"
            "down_revision='0005_job_plane_role_split'\n"
        ),
        "uv.lock": "lock\n",
        "legacy/research-backend/uv.lock": "lock\n",
        "apps/dashboard/package-lock.json": "{}\n",
    }
    for relative, content in required.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Release Test"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "release@example.invalid"], cwd=repo, check=True,
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()
    uv_cache, npm_cache = tmp_path / "uv-cache", tmp_path / "npm-cache"
    for cache in (uv_cache, npm_cache):
        cache.mkdir(mode=0o700)
        (cache / "entry").write_text("cached\n", encoding="utf-8")
        (cache / "entry").chmod(0o600)
    return repo, commit, uv_cache, npm_cache


def test_fake_root_provision_is_atomic_preserves_prior_and_does_not_enable_timer(
    tmp_path: Path,
) -> None:
    stage, authority, verifier, document, digest = make_release_fixture(tmp_path)
    fake_root = tmp_path / "fake-root"
    prior = _seed_prior(fake_root)

    result = _run(
        PROVISION,
        "--stage", stage,
        "--authority", authority,
        "--authority-sha256", digest,
        "--verifier", verifier,
        "--destination-root", fake_root,
        "--test-fake-root",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "release authority v2 candidate provisioned\n"
    current = fake_root / "opt/trading-agent-v2/current"
    installed = fake_root / document["installation_root"].lstrip("/")
    assert current.resolve() == prior.resolve()
    assert installed.is_dir()
    assert prior.is_dir()
    assert (prior / "prior-evidence").read_text(encoding="utf-8") == "preserve\n"
    installed_verifier = fake_root / "usr/libexec/trading-agent-v2/verify-stage.py"
    assert installed_verifier.is_file()
    assert installed_verifier.stat().st_mode & 0o777 == 0o555
    assert hashlib.sha256(installed_verifier.read_bytes()).hexdigest() == document[
        "external_verifier"
    ]["sha256"]
    assert not list((installed / "units").glob("*.timer"))
    assert not list((fake_root / "etc/systemd").rglob("*.wants"))


def test_rejected_or_partial_stage_never_repoints_current(tmp_path: Path) -> None:
    stage, authority, verifier, document, digest = make_release_fixture(tmp_path)
    fake_root = tmp_path / "fake-root"
    prior = _seed_prior(fake_root)
    stage.chmod(0o755)
    (stage / "unexpected").write_text("reject\n", encoding="utf-8")
    (stage / "unexpected").chmod(0o444)
    stage.chmod(0o555)

    result = _run(
        PROVISION,
        "--stage", stage,
        "--authority", authority,
        "--authority-sha256", digest,
        "--verifier", verifier,
        "--destination-root", fake_root,
        "--test-fake-root",
    )

    assert result.returncode == 2
    assert (fake_root / "opt/trading-agent-v2/current").resolve() == prior.resolve()
    assert not (fake_root / document["installation_root"].lstrip("/")).exists()


@pytest.mark.parametrize("prior_schema_version", [2, 3])
def test_rollback_repoints_only_to_bound_prior_and_preserves_both_releases(
    tmp_path: Path,
    prior_schema_version: int,
) -> None:
    stage, authority, verifier, document, digest = make_release_fixture(tmp_path)
    fake_root = tmp_path / "fake-root"
    prior, digest = _seed_bound_prior(
        fake_root,
        authority,
        document,
        prior_schema_version=prior_schema_version,
    )
    provisioned = _run(
        PROVISION,
        "--stage", stage,
        "--authority", authority,
        "--authority-sha256", digest,
        "--verifier", verifier,
        "--destination-root", fake_root,
        "--test-fake-root",
    )
    assert provisioned.returncode == 0, provisioned.stderr
    installed = fake_root / document["installation_root"].lstrip("/")

    rolled_back = _run(
        ROLLBACK,
        "--authority", authority,
        "--authority-sha256", digest,
        "--destination-root", fake_root,
        "--test-fake-root",
    )

    assert rolled_back.returncode == 0, rolled_back.stderr
    assert rolled_back.stdout == "release authority v2 rollback verified; pointers unchanged\n"
    assert (fake_root / "opt/trading-agent-v2/current").resolve() == prior.resolve()
    assert installed.is_dir()
    assert prior.is_dir()


@pytest.mark.parametrize("prior_schema_version", [1, 4])
def test_rollback_rejects_unknown_prior_authority_schema(
    tmp_path: Path,
    prior_schema_version: int,
) -> None:
    stage, authority, verifier, document, _ = make_release_fixture(tmp_path)
    fake_root = tmp_path / "fake-root"
    prior, digest = _seed_bound_prior(
        fake_root,
        authority,
        document,
        prior_schema_version=prior_schema_version,
    )
    provisioned = _run(
        PROVISION,
        "--stage", stage,
        "--authority", authority,
        "--authority-sha256", digest,
        "--verifier", verifier,
        "--destination-root", fake_root,
        "--test-fake-root",
    )
    assert provisioned.returncode == 0, provisioned.stderr

    rolled_back = _run(
        ROLLBACK,
        "--authority", authority,
        "--authority-sha256", digest,
        "--destination-root", fake_root,
        "--test-fake-root",
    )

    assert rolled_back.returncode == 2
    assert (fake_root / "opt/trading-agent-v2/current").resolve() == prior.resolve()


@pytest.mark.parametrize("pointer_target", ["candidate", "outside"])
def test_rollback_rejects_release_pointer_not_bound_by_prior_authority(
    tmp_path: Path,
    pointer_target: str,
) -> None:
    stage, authority, verifier, document, _ = make_release_fixture(tmp_path)
    fake_root = tmp_path / "fake-root"
    prior, digest = _seed_bound_prior(fake_root, authority, document)
    provisioned = _run(
        PROVISION,
        "--stage", stage,
        "--authority", authority,
        "--authority-sha256", digest,
        "--verifier", verifier,
        "--destination-root", fake_root,
        "--test-fake-root",
    )
    assert provisioned.returncode == 0, provisioned.stderr
    installed = fake_root / document["installation_root"].lstrip("/")
    outside = tmp_path / "outside-release"
    outside.mkdir()
    current = fake_root / "opt/trading-agent-v2/current"
    current.unlink()
    current.symlink_to(installed if pointer_target == "candidate" else outside)

    rolled_back = _run(
        ROLLBACK,
        "--authority", authority,
        "--authority-sha256", digest,
        "--destination-root", fake_root,
        "--test-fake-root",
    )

    assert rolled_back.returncode == 2
    assert prior.is_dir()
    assert installed.is_dir()


def test_candidate_phase_rejects_root_destination_and_activation_flag(tmp_path: Path) -> None:
    stage, authority, verifier, _, digest = make_release_fixture(tmp_path)
    for forbidden in (
        ["--destination-root", "/", "--test-fake-root"],
        ["--destination-root", str(tmp_path / "fake-root"), "--activate"],
    ):
        result = _run(
            PROVISION,
            "--stage", stage,
            "--authority", authority,
            "--authority-sha256", digest,
            "--verifier", verifier,
            *forbidden,
        )
        assert result.returncode == 2


def test_fake_provision_rejects_writable_destination_root(tmp_path: Path) -> None:
    stage, authority, verifier, document, digest = make_release_fixture(tmp_path)
    fake_root = tmp_path / "writable-fake-root"
    fake_root.mkdir(mode=0o777)
    fake_root.chmod(0o777)

    result = _run(
        PROVISION,
        "--stage", stage,
        "--authority", authority,
        "--authority-sha256", digest,
        "--verifier", verifier,
        "--destination-root", fake_root,
        "--test-fake-root",
    )

    assert result.returncode == 2
    assert not (fake_root / document["installation_root"].lstrip("/")).exists()


def test_fake_provision_rejects_paired_self_declared_malicious_verifier(
    tmp_path: Path,
) -> None:
    stage, authority, _, document, _ = make_release_fixture(tmp_path)
    malicious = tmp_path / "malicious-verifier.py"
    malicious.write_text("#!/usr/bin/python3\nraise SystemExit(0)\n", encoding="utf-8")
    malicious.chmod(0o555)
    document["external_verifier"]["path"] = str(malicious)
    document["external_verifier"]["sha256"] = hashlib.sha256(malicious.read_bytes()).hexdigest()
    unsigned = dict(document)
    unsigned.pop("binding_sha256")
    document["binding_sha256"] = hashlib.sha256(canonical_json_bytes(unsigned)[:-1]).hexdigest()
    authority.chmod(0o644)
    authority.write_bytes(canonical_json_bytes(document))
    authority.chmod(0o444)
    digest = hashlib.sha256(authority.read_bytes()).hexdigest()
    fake_root = tmp_path / "fake-root"
    _seed_prior(fake_root)

    result = _run(
        PROVISION,
        "--stage", stage,
        "--authority", authority,
        "--authority-sha256", digest,
        "--verifier", malicious,
        "--destination-root", fake_root,
        "--test-fake-root",
    )

    assert result.returncode == 2
    assert not (fake_root / document["installation_root"].lstrip("/")).exists()


def test_build_script_rejects_dirty_source_before_creating_output(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Release Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "release@example.invalid"], cwd=repo, check=True)
    (repo / "tracked").write_text("committed\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()
    (repo / "untracked").write_text("dirty\n", encoding="utf-8")
    output = tmp_path / "must-not-exist"

    result = _run(
        ROOT / "ops/release-v2/build-stage.sh",
        "--repo", repo,
        "--commit", commit,
        "--output", output,
        "--prior-release-sha256", hashlib.sha256(b"prior").hexdigest(),
    )

    assert result.returncode == 2
    assert not output.exists()
    assert not output.with_suffix(".authority.json").exists()


def test_build_script_rejects_empty_offline_caches_before_creating_output(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Release Test"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "release@example.invalid"], cwd=repo, check=True,
    )
    (repo / "tracked").write_text("committed\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()
    uv_cache = tmp_path / "uv-cache"
    npm_cache = tmp_path / "npm-cache"
    uv_cache.mkdir(mode=0o700)
    npm_cache.mkdir(mode=0o700)
    output = tmp_path / "must-not-exist"

    result = _run(
        ROOT / "ops/release-v2/build-stage.sh",
        "--repo", repo,
        "--commit", commit,
        "--output", output,
        "--prior-release-sha256", hashlib.sha256(b"prior").hexdigest(),
        "--uv-cache", uv_cache,
        "--npm-cache", npm_cache,
    )

    assert result.returncode == 2
    assert not output.exists()
    assert not Path(f"{output}.authority.json").exists()


def test_build_script_rejects_writable_output_parent_before_running_toolchain(
    tmp_path: Path,
) -> None:
    repo, commit, uv_cache, npm_cache = _minimal_build_inputs(tmp_path)
    marker = tmp_path / "python-ran"
    output_parent = tmp_path / "unsafe-output"
    output_parent.mkdir(mode=0o777)
    output_parent.chmod(0o777)
    output = output_parent / "candidate"

    result = _run(
        ROOT / "ops/release-v2/build-stage.sh",
        "--repo", repo,
        "--commit", commit,
        "--output", output,
        "--prior-release-sha256", hashlib.sha256(b"prior").hexdigest(),
        "--python", "/usr/bin/python3",
        "--node", "/bin/true",
        "--npm", "/bin/true",
        "--uv", "/bin/true",
        "--uv-cache", uv_cache,
        "--npm-cache", npm_cache,
        env={**os.environ, "RELEASE_TEST_MARKER": str(marker)},
    )

    assert result.returncode != 0
    assert not marker.exists()


@pytest.mark.parametrize("unsafe_entry", ["writable", "symlink", "hardlink", "fifo"])
def test_build_script_rejects_unsafe_cache_descendants_before_running_toolchain(
    tmp_path: Path,
    unsafe_entry: str,
) -> None:
    repo, commit, uv_cache, npm_cache = _minimal_build_inputs(tmp_path)
    entry = uv_cache / "entry"
    entry.unlink()
    if unsafe_entry == "writable":
        entry.write_text("mutable\n", encoding="utf-8")
        entry.chmod(0o666)
    elif unsafe_entry == "symlink":
        entry.symlink_to(tmp_path / "outside-cache")
    elif unsafe_entry == "hardlink":
        entry.write_text("linked\n", encoding="utf-8")
        entry.chmod(0o600)
        os.link(entry, uv_cache / "second-link")
    else:
        os.mkfifo(entry, mode=0o600)
    marker = tmp_path / "python-ran"

    result = _run(
        ROOT / "ops/release-v2/build-stage.sh",
        "--repo", repo,
        "--commit", commit,
        "--output", tmp_path / "output" / "candidate",
        "--prior-release-sha256", hashlib.sha256(b"prior").hexdigest(),
        "--python", "/usr/bin/python3",
        "--node", "/bin/true",
        "--npm", "/bin/true",
        "--uv", "/bin/true",
        "--uv-cache", uv_cache,
        "--npm-cache", npm_cache,
        env={**os.environ, "RELEASE_TEST_MARKER": str(marker)},
    )

    assert result.returncode != 0
    assert not marker.exists()


def test_provision_reverifies_installed_tree_after_authority_publication(tmp_path: Path) -> None:
    stage, authority, verifier, document, digest = make_release_fixture(tmp_path)
    fake_root = tmp_path / "fake-root"
    _seed_prior(fake_root)
    installed = fake_root / document["installation_root"].lstrip("/")
    wrappers = tmp_path / "wrappers"
    wrappers.mkdir()
    cp_wrapper = wrappers / "cp"
    cp_wrapper.write_text(
        "#!/bin/bash\n"
        "/bin/cp \"$@\" || exit $?\n"
        "if [[ ${1-} != -a && -d ${TAMPER_TARGET-} ]]; then\n"
        "  chmod u+w \"$TAMPER_TARGET/application/uv.lock\"\n"
        "  printf 'tampered\\n' >\"$TAMPER_TARGET/application/uv.lock\"\n"
        "  chmod 0444 \"$TAMPER_TARGET/application/uv.lock\"\n"
        "fi\n",
        encoding="utf-8",
    )
    cp_wrapper.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = f"{wrappers}:{env['PATH']}"
    env["TAMPER_TARGET"] = str(installed)

    result = _run(
        PROVISION,
        "--stage", stage,
        "--authority", authority,
        "--authority-sha256", digest,
        "--verifier", verifier,
        "--destination-root", fake_root,
        "--test-fake-root",
        env=env,
    )

    assert result.returncode == 2
