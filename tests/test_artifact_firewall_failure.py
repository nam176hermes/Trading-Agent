from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

import pytest

from scripts import check_artifact_firewall as firewall
from scripts import t_g03_capability_topology as topology


INVENTORY = Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv")
RAW_REASON = "fixture reason"
TREE = "a" * 64
RUN_ID = "31833372257"
RUN_ATTEMPT = "1"
ARTIFACT_DIRECTORY = (
    f"trading-agent-ci-portable-publication.{RUN_ID}.{RUN_ATTEMPT}/artifact"
)
R11_RUN_ID = "31839312983"
R11_RUN_ATTEMPT = str(os.getpid())
R11_PUBLICATION_DIRECTORY = (
    f"trading-agent-ci-portable-publication.{R11_RUN_ID}.{R11_RUN_ATTEMPT}"
)
R11_ARTIFACT_RELATIVE = Path(R11_PUBLICATION_DIRECTORY) / "artifact"


def _failure_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, str, str, str]:
    raw_root = tmp_path / "raw"
    run_id = "31827924223"
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True,
    ).stdout.strip()
    custody = tmp_path / "custody.so"
    custody.write_bytes(b"P0-12R8 retained custody fixture")
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    monkeypatch.setenv("PACKAGE6_FD_CUSTODY_EXTENSION_PATH", str(custody))
    monkeypatch.setenv(
        "PACKAGE6_FD_CUSTODY_EXTENSION_SHA256",
        hashlib.sha256(custody.read_bytes()).hexdigest(),
    )
    context_path = topology._capture_foundation_context(
        raw_root, clock=lambda: datetime(2026, 8, 14, tzinfo=timezone.utc),
    )
    inventory = topology.load_inventory(INVENTORY)
    closure = topology.load_portable_defect_closure(head_sha=head_sha)
    passed = "tests/ordinary/test_r8.py::test_passed"
    skipped = "tests/ordinary/test_r8.py::test_skipped"
    candidates = tuple(sorted(
        {row.node_id for row in inventory}
        | {row.node_id for row in closure}
        | {passed, skipped}
    ))
    topology.reserve_topology_evidence(
        raw_root, run_id=run_id, head_sha=head_sha,
        foundation_context_path=context_path,
    )
    topology.collect_portable_root_baseline(
        inventory=INVENTORY, evidence_root=raw_root, run_id=run_id,
        head_sha=head_sha, collector=lambda: candidates,
        foundation_context_path=context_path,
    )
    topology.prepare_portable_root_remainder(
        inventory=INVENTORY, evidence_root=raw_root, run_id=run_id,
        head_sha=head_sha, foundation_context_path=context_path,
    )

    def run_exact(nodes: tuple[str, ...], report: Path) -> tuple[str, ...]:
        report.write_text(json.dumps({
            "schema_version": 1,
            "component": "root",
            "pytest_exit_status": 0,
            "custody_policy": json.loads(os.environ["TEST_GOVERNANCE_CUSTODY_POLICY"]),
            "tests": [
                {
                    "test_node_id": node,
                    "component": "root",
                    "outcome": "skipped" if node == skipped else "passed",
                    "reason": RAW_REASON if node == skipped else "",
                    "phase": "call",
                }
                for node in nodes
            ],
        }), encoding="utf-8")
        return nodes

    with pytest.raises(topology.TopologyError, match="^EXACT_EXECUTION_NONPASS$"):
        topology.execute_portable_root_remainder(
            inventory=INVENTORY, evidence_root=raw_root, run_id=run_id,
            head_sha=head_sha, exact_runner=run_exact,
            foundation_context_path=context_path,
        )
    return raw_root, run_id, head_sha, skipped


def _publish_failure(
    raw_root: Path, destination: Path, monkeypatch: pytest.MonkeyPatch,
    **kwargs: object,
) -> dict[str, object]:
    monkeypatch.setattr(firewall, "_source_tree_identity", lambda _root, _head: TREE)
    return firewall.publish_root_remainder_failure(
        raw_root=raw_root,
        destination=destination,
        inventory=INVENTORY,
        foundation_context_path=raw_root / "capability-topology/foundation-context.json",
        repository_root=Path.cwd(),
        **kwargs,
    )


def _run_ci_portable_failure_probe(
    tmp_path: Path, runner_temp: Path, *, parent_attack: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    binary = tmp_path / "bin"
    binary.mkdir()
    invocation_log = tmp_path / "publish.log"
    inner_log = tmp_path / "inner.log"
    fake_make = tmp_path / "fake-make"
    fake_make.write_text(
        "#!/bin/sh\n"
        ": > \"$INNER_LOG\"\n"
        "mkdir -p \"$TEST_EVIDENCE_DIR/capability-topology\"\n"
        "touch \"$TEST_EVIDENCE_DIR/capability-topology/portable-root-remainder.failure-diagnostic.json\"\n"
        "exit 37\n",
        encoding="utf-8",
    )
    fake_make.chmod(0o700)
    fake_uv = binary / "uv"
    fake_uv.write_text(
        "#!/bin/sh\n"
        "case \" $* \" in\n"
        "  *\" python -c \"*)\n"
        "    for root do :; done\n"
        "    mkdir -p \"$root/capability-topology\"\n"
        "    context=\"$root/capability-topology/foundation-context.json\"\n"
        "    : > \"$context\"\n"
        "    printf '%s\\n' \"$context\"\n"
        "    exit 0;;\n"
        "esac\n"
        "printf '%s\\n' \"$*\" >> \"$INVOCATION_LOG\"\n"
        "exit 91\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o700)
    publication_parent = runner_temp / R11_PUBLICATION_DIRECTORY
    if parent_attack == "unsafe-create-mode":
        fake_mkdir = binary / "mkdir"
        fake_mkdir.write_text(
            "#!/bin/sh\n"
            "for value do last=$value; done\n"
            "if test \"$last\" = \"$PUBLICATION_PARENT\"; then\n"
            "  /usr/bin/mkdir -m 0755 -- \"$last\"\n"
            "  exit $?\n"
            "fi\n"
            "exec /usr/bin/mkdir \"$@\"\n",
            encoding="utf-8",
        )
        fake_mkdir.chmod(0o700)
    elif parent_attack == "foreign-owner":
        fake_stat = binary / "stat"
        fake_stat.write_text(
            "#!/bin/sh\n"
            "for value do last=$value; done\n"
            "if test \"$last\" = \"$PUBLICATION_PARENT\"; then\n"
            f"  printf '%s\\n' '{os.geteuid() + 1}:700'\n"
            "  exit 0\n"
            "fi\n"
            "exec /usr/bin/stat \"$@\"\n",
            encoding="utf-8",
        )
        fake_stat.chmod(0o700)
    result = subprocess.run(
        ["make", "--no-print-directory", "ci-portable", f"MAKE={fake_make}"],
        cwd=Path.cwd(), capture_output=True, text=True, check=False,
        env={
            **os.environ,
            "PATH": f"{binary}:{os.environ['PATH']}",
            "RUNNER_TEMP": str(runner_temp),
            "GITHUB_RUN_ID": R11_RUN_ID,
            "GITHUB_RUN_ATTEMPT": R11_RUN_ATTEMPT,
            "INVOCATION_LOG": str(invocation_log),
            "INNER_LOG": str(inner_log),
            "PUBLICATION_PARENT": str(publication_parent),
        },
    )
    return result, invocation_log, inner_log


def test_ci_portable_uses_a_private_publication_parent_under_root_owned_runner_temp(
    tmp_path: Path,
) -> None:
    runner_temp = Path("/tmp")
    info = runner_temp.stat()
    assert info.st_uid == 0
    assert info.st_mode & 0o1000
    publication_parent = runner_temp / R11_PUBLICATION_DIRECTORY
    assert not publication_parent.exists() and not publication_parent.is_symlink()
    try:
        result, invocation_log, inner_log = _run_ci_portable_failure_probe(
            tmp_path, runner_temp,
        )

        assert result.returncode != 0
        assert "Error 37" in result.stderr
        assert inner_log.is_file()
        assert publication_parent.is_dir() and not publication_parent.is_symlink()
        parent_info = publication_parent.stat()
        assert parent_info.st_uid == os.geteuid()
        assert parent_info.st_mode & 0o777 == 0o700
        expected = runner_temp / R11_ARTIFACT_RELATIVE
        assert f"--destination {expected}" in invocation_log.read_text(encoding="utf-8")
        assert not expected.exists() and not expected.is_symlink()
    finally:
        if publication_parent.is_dir() and not publication_parent.is_symlink():
            publication_parent.rmdir()


@pytest.mark.parametrize("kind", ["directory", "symlink", "file", "unsafe-mode"])
def test_ci_portable_rejects_preoccupied_publication_parent_before_inner_make(
    tmp_path: Path, kind: str,
) -> None:
    publication_parent = tmp_path / R11_PUBLICATION_DIRECTORY
    if kind == "directory":
        publication_parent.mkdir(mode=0o700)
    elif kind == "symlink":
        target = tmp_path / "foreign"
        target.mkdir()
        publication_parent.symlink_to(target, target_is_directory=True)
    elif kind == "file":
        publication_parent.write_bytes(b"foreign")
    else:
        publication_parent.mkdir(mode=0o755)

    result, _invocation_log, inner_log = _run_ci_portable_failure_probe(
        tmp_path / "probe", tmp_path,
    )

    assert result.returncode != 0
    assert not inner_log.exists()


@pytest.mark.parametrize("attack", ["unsafe-create-mode", "foreign-owner"])
def test_ci_portable_rejects_noncanonical_new_publication_parent(
    tmp_path: Path, attack: str,
) -> None:
    publication_parent = tmp_path / R11_PUBLICATION_DIRECTORY
    result, _invocation_log, inner_log = _run_ci_portable_failure_probe(
        tmp_path / "probe", tmp_path, parent_attack=attack,
    )

    assert result.returncode != 0
    assert not inner_log.exists()
    assert publication_parent.exists()


def test_failure_publisher_emits_only_a_sealed_diagnostic_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_root, run_id, head_sha, skipped = _failure_source(tmp_path, monkeypatch)
    destination = tmp_path / "runtime/state/ci-portable"
    manifest = _publish_failure(raw_root, destination, monkeypatch)

    assert manifest["schema_version"] == "portable-ci-failure-evidence-manifest/v1"
    assert manifest["head_sha"] == head_sha
    assert manifest["run_metadata"]["run_id"] == run_id
    assert set(path.relative_to(destination).as_posix() for path in destination.rglob("*") if path.is_file()) == {
        "SHA256SUMS", "manifest.json", "root-remainder-failure.json",
    }
    projection_raw = (destination / "root-remainder-failure.json").read_bytes()
    projection = json.loads(projection_raw)
    assert projection["schema_version"] == "portable-ci-root-remainder-failure/v1"
    assert projection["diagnostic_only"] is True
    assert projection["failure_class"] == "EXACT_EXECUTION_NONPASS"
    assert projection["remainder_node_count"] == 2
    assert projection["passed_count"] == 1
    assert projection["skipped_count"] == 1
    assert [item["test_node_id"] for item in projection["skipped_observations"]] == [skipped]
    assert RAW_REASON.encode() not in projection_raw
    assert b'"status":"PASS"' not in projection_raw
    assert b'"outcome":"PASS"' not in projection_raw
    assert not (destination / "capability-topology/aggregate.json").exists()
    assert not (destination / "test-governance").exists()
    assert (destination.stat().st_mode & 0o777) == 0o500
    assert all((path.stat().st_mode & 0o777) == 0o400 for path in destination.iterdir())


@pytest.mark.parametrize("mutation", ["malformed", "stale", "foreign"])
def test_failure_publisher_rejects_malformed_stale_and_foreign_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    raw_root, _run_id, _head_sha, _skipped = _failure_source(tmp_path, monkeypatch)
    diagnostic = raw_root / "capability-topology/portable-root-remainder.failure-diagnostic.json"
    if mutation == "malformed":
        diagnostic.write_bytes(diagnostic.read_bytes() + b"\n")
    else:
        document = json.loads(diagnostic.read_bytes())
        if mutation == "stale":
            document["foundation_run_id"] = "31827924224"
        else:
            document["observations"][-1]["test_node_id"] = (
                "tests/ordinary/test_r8.py::test_foreign"
            )
        document["diagnostic_sha256"] = topology._sha256({
            key: value for key, value in document.items()
            if key != "diagnostic_sha256"
        })
        diagnostic.write_bytes(topology.canonical_json_bytes(document))
    destination = tmp_path / "final"
    with pytest.raises(firewall.FirewallError):
        _publish_failure(raw_root, destination, monkeypatch)
    assert not destination.exists()


@pytest.mark.parametrize("authority", ["context", "reservation", "baseline", "remainder"])
def test_failure_publisher_revalidates_every_partial_foundation_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, authority: str,
) -> None:
    raw_root, _run_id, _head_sha, _skipped = _failure_source(tmp_path, monkeypatch)
    topology_root = raw_root / "capability-topology"
    names = {
        "context": "foundation-context.json",
        "reservation": ".reservation",
        "baseline": "portable-root-baseline.json",
        "remainder": "portable-root-remainder.json",
    }
    path = topology_root / names[authority]
    document = json.loads(path.read_bytes())
    if authority == "context":
        document["foundation_run_id"] = "31827924224"
        document["foundation_context_sha256"] = topology._sha256({
            key: value for key, value in document.items()
            if key != "foundation_context_sha256"
        })
    elif authority == "reservation":
        document["foundation_run_id"] = "31827924224"
    elif authority == "baseline":
        document["collector_policy"]["native_custody_extension_sha256"] = "f" * 64
        document["baseline_sha256"] = topology._baseline_payload_sha256(document)
    else:
        document["remainder_node_ids"] = list(reversed(document["remainder_node_ids"]))
        document["remainder_sha256"] = topology._remainder_payload_sha256(document)
    path.write_bytes(topology.canonical_json_bytes(document))
    destination = tmp_path / "final"
    with pytest.raises(firewall.FirewallError):
        _publish_failure(raw_root, destination, monkeypatch)
    assert not destination.exists()


@pytest.mark.parametrize("attack", ["symlink", "hardlink", "mode", "owner"])
def test_failure_publisher_rejects_unsafe_source_leaf_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attack: str,
) -> None:
    raw_root, _run_id, _head_sha, _skipped = _failure_source(tmp_path, monkeypatch)
    diagnostic = raw_root / "capability-topology/portable-root-remainder.failure-diagnostic.json"
    if attack == "symlink":
        copy = tmp_path / "diagnostic-copy.json"
        copy.write_bytes(diagnostic.read_bytes())
        diagnostic.unlink()
        diagnostic.symlink_to(copy)
    elif attack == "hardlink":
        os.link(diagnostic, tmp_path / "diagnostic-hardlink.json")
    elif attack == "mode":
        diagnostic.chmod(0o640)
    else:
        monkeypatch.setattr(firewall.os, "geteuid", lambda: os.getuid() + 1)
    destination = tmp_path / "final"
    with pytest.raises(firewall.FirewallError):
        _publish_failure(raw_root, destination, monkeypatch)
    assert not destination.exists()


def test_failure_publisher_rejects_source_replacement_after_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_root, _run_id, _head_sha, _skipped = _failure_source(tmp_path, monkeypatch)
    diagnostic = raw_root / "capability-topology/portable-root-remainder.failure-diagnostic.json"

    def replace(_boundary: str) -> None:
        replacement = tmp_path / "replacement.json"
        replacement.write_bytes(diagnostic.read_bytes())
        replacement.chmod(0o600)
        os.replace(replacement, diagnostic)

    destination = tmp_path / "final"
    with pytest.raises(firewall.FirewallError):
        _publish_failure(
            raw_root, destination, monkeypatch, source_boundary_hook=replace,
        )
    assert not destination.exists()


def test_failure_publisher_rejects_publication_parent_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_root, _run_id, _head_sha, _skipped = _failure_source(tmp_path, monkeypatch)
    publication_parent = tmp_path / "publication-parent"
    publication_parent.mkdir(mode=0o700)
    destination = publication_parent / "artifact"
    displaced = tmp_path / "displaced-publication-parent"
    foreign = tmp_path / "foreign-publication-parent"
    foreign.mkdir(mode=0o700)

    def replace(_boundary: str) -> None:
        publication_parent.rename(displaced)
        publication_parent.symlink_to(foreign, target_is_directory=True)

    with pytest.raises(firewall.FirewallError):
        _publish_failure(
            raw_root, destination, monkeypatch, publication_boundary_hook=replace,
        )

    assert not (foreign / destination.name).exists()


@pytest.mark.parametrize(
    "relative",
    [
        "capability-topology/aggregate.json",
        "capability-topology/NATIVE-BWRAP-OS-SANDBOX.json",
        "capability-topology/policy-validation-nonacceptance.json",
        "capability-topology/unsafe-raw-reason-nonacceptance.json",
        "test-governance-topology/test-governance.json",
    ],
)
def test_failure_publisher_rejects_acceptance_or_later_stage_coexistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str,
) -> None:
    raw_root, _run_id, _head_sha, _skipped = _failure_source(tmp_path, monkeypatch)
    path = raw_root / relative
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    path.write_bytes(b"{}")
    path.chmod(0o600)
    destination = tmp_path / "final"
    with pytest.raises(firewall.FirewallError):
        _publish_failure(raw_root, destination, monkeypatch)
    assert not destination.exists()


def test_failure_publisher_never_clobbers_an_existing_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_root, _run_id, _head_sha, _skipped = _failure_source(tmp_path, monkeypatch)
    destination = tmp_path / "runtime/state/ci-portable"
    destination.mkdir(parents=True, mode=0o700)
    sentinel = destination / "existing"
    sentinel.write_text("preserve", encoding="utf-8")
    with pytest.raises(firewall.FirewallError, match="destination already exists"):
        _publish_failure(raw_root, destination, monkeypatch)
    assert sentinel.read_text(encoding="utf-8") == "preserve"


@pytest.mark.parametrize(
    ("inner_status", "create_diagnostic", "expected_publish_count"),
    [(0, False, 0), (23, False, 0), (37, True, 1)],
)
def test_ci_portable_catch_is_single_shot_and_preserves_the_original_status(
    tmp_path: Path, inner_status: int, create_diagnostic: bool,
    expected_publish_count: int,
) -> None:
    binary = tmp_path / "bin"
    binary.mkdir()
    invocation_log = tmp_path / "publish.log"
    fake_make = tmp_path / "fake-make"
    fake_make.write_text(
        "#!/bin/sh\n"
        + (
            'mkdir -p "$TEST_EVIDENCE_DIR/capability-topology"\n'
            'touch "$TEST_EVIDENCE_DIR/capability-topology/portable-root-remainder.failure-diagnostic.json"\n'
            if create_diagnostic else ""
        )
        + f"exit {inner_status}\n",
        encoding="utf-8",
    )
    fake_make.chmod(0o700)
    fake_uv = binary / "uv"
    fake_uv.write_text(
        "#!/bin/sh\n"
        "case \" $* \" in\n"
        "  *\" python -c \"*)\n"
        "    for root do :; done\n"
        "    mkdir -p \"$root/capability-topology\"\n"
        "    context=\"$root/capability-topology/foundation-context.json\"\n"
        "    : > \"$context\"\n"
        "    echo \"$context\"\n"
        "    exit 0;;\n"
        "esac\n"
        "printf '%s\\n' \"$*\" >> \"$INVOCATION_LOG\"\n"
        "exit 91\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o700)
    result = subprocess.run(
        ["make", "--no-print-directory", "ci-portable", f"MAKE={fake_make}"],
        cwd=Path.cwd(), capture_output=True, text=True, check=False,
        env={
            **os.environ,
            "PATH": f"{binary}:{os.environ['PATH']}",
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_RUN_ID": RUN_ID,
            "GITHUB_RUN_ATTEMPT": RUN_ATTEMPT,
            "PORTABLE_CI_ARTIFACT_ROOT": str(tmp_path / "injected-destination"),
            "INVOCATION_LOG": str(invocation_log),
        },
    )
    published = [] if not invocation_log.exists() else invocation_log.read_text().splitlines()
    assert len(published) == expected_publish_count
    if inner_status == 0:
        assert result.returncode == 0
    else:
        assert result.returncode != 0
        assert f"Error {inner_status}" in result.stderr
    makefile = Path("Makefile").read_text(encoding="utf-8")
    assert "original_status=$$?" in makefile
    assert 'exit "$$original_status"' in makefile
    if published:
        expected = tmp_path / ARTIFACT_DIRECTORY
        assert f"--destination {expected}" in published[0]
        assert "injected-destination" not in published[0]


@pytest.mark.parametrize("attack", ["command-line", "makeflags", "makeoverrides"])
def test_real_recursive_gnu_make_cannot_override_private_artifact_root(
    tmp_path: Path, attack: str,
) -> None:
    makefile = Path("Makefile").read_text(encoding="utf-8")
    original_private = (
        "ci-portable-private:\n"
        "\t$(MAKE) ci-common-private ci-portable-topology "
        "check-portable-defect-closure check-p0-baseline "
        "check-test-governance-topology check-p0-ci-closure "
        "artifact-firewall-check audit-delivery-contract\n"
    )
    probe_private = (
        "ci-portable-private:\n"
        "\t@printf '%s\\n' \"$(PORTABLE_CI_ARTIFACT_ROOT)\" "
        "\"$$PORTABLE_CI_ARTIFACT_ROOT\" > \"$$GNU_MAKE_ARTIFACT_LOG\"\n"
        "\t@$(MAKE) --no-print-directory portable-artifact-probe\n"
        "\n"
        "portable-artifact-probe:\n"
        "\t@printf '%s\\n' \"$(PORTABLE_CI_ARTIFACT_ROOT)\" "
        "\"$$PORTABLE_CI_ARTIFACT_ROOT\" >> \"$$GNU_MAKE_ARTIFACT_LOG\"\n"
    )
    assert makefile.count(original_private) == 1
    probe_makefile = tmp_path / "Makefile"
    probe_makefile.write_text(
        makefile.replace(original_private, probe_private), encoding="utf-8",
    )
    binary = tmp_path / "bin"
    binary.mkdir()
    fake_uv = binary / "uv"
    fake_uv.write_text(
        "#!/bin/sh\n"
        "for root do :; done\n"
        "mkdir -p \"$root/capability-topology\"\n"
        "context=\"$root/capability-topology/foundation-context.json\"\n"
        ": > \"$context\"\n"
        "printf '%s\\n' \"$context\"\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o700)
    artifact_log = tmp_path / "artifact.log"
    attacker = "/attacker/redirected"
    command = ["make", "--no-print-directory", "ci-portable"]
    environment = {
        **os.environ,
        "PATH": f"{binary}:{os.environ['PATH']}",
        "RUNNER_TEMP": str(tmp_path),
        "GITHUB_RUN_ID": RUN_ID,
        "GITHUB_RUN_ATTEMPT": RUN_ATTEMPT,
        "GNU_MAKE_ARTIFACT_LOG": str(artifact_log),
    }
    if attack == "command-line":
        command.append(f"PORTABLE_CI_ARTIFACT_ROOT={attacker}")
    elif attack == "makeflags":
        environment["MAKEFLAGS"] = f"PORTABLE_CI_ARTIFACT_ROOT={attacker}"
    else:
        environment["PORTABLE_CI_ARTIFACT_ROOT"] = attacker
        command.append(f"MAKEOVERRIDES=PORTABLE_CI_ARTIFACT_ROOT={attacker}")

    result = subprocess.run(
        command, cwd=tmp_path, env=environment,
        capture_output=True, text=True, check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    expected = str(tmp_path / ARTIFACT_DIRECTORY)
    assert artifact_log.read_text(encoding="utf-8").splitlines() == [expected] * 4


@pytest.mark.parametrize(
    ("run_id", "attempt"),
    [
        ("../foreign", RUN_ATTEMPT), (RUN_ID, "1/foreign"),
        ("run-7", RUN_ATTEMPT), ("0", RUN_ATTEMPT), (RUN_ID, "01"),
    ],
)
def test_ci_portable_rejects_malformed_artifact_identity_before_inner_make(
    tmp_path: Path, run_id: str, attempt: str,
) -> None:
    tripwire = tmp_path / "inner-make-ran"
    fake_make = tmp_path / "fake-make"
    fake_make.write_text(
        "#!/bin/sh\n: > \"$TRIPWIRE\"\nexit 0\n", encoding="utf-8",
    )
    fake_make.chmod(0o700)

    result = subprocess.run(
        ["make", "--no-print-directory", "ci-portable", f"MAKE={fake_make}"],
        cwd=Path.cwd(), capture_output=True, text=True, check=False,
        env={
            **os.environ,
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_RUN_ID": run_id,
            "GITHUB_RUN_ATTEMPT": attempt,
            "TRIPWIRE": str(tripwire),
        },
    )

    assert result.returncode != 0
    assert not tripwire.exists()


def test_ci_portable_preserves_stale_artifact_occupancy_and_stops(
    tmp_path: Path,
) -> None:
    destination = tmp_path / ARTIFACT_DIRECTORY
    destination.mkdir(parents=True)
    sentinel = destination / "foreign"
    sentinel.write_bytes(b"preserve")
    tripwire = tmp_path / "inner-make-ran"
    fake_make = tmp_path / "fake-make"
    fake_make.write_text(
        "#!/bin/sh\n: > \"$TRIPWIRE\"\nexit 0\n", encoding="utf-8",
    )
    fake_make.chmod(0o700)

    result = subprocess.run(
        ["make", "--no-print-directory", "ci-portable", f"MAKE={fake_make}"],
        cwd=Path.cwd(), capture_output=True, text=True, check=False,
        env={
            **os.environ,
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_RUN_ID": RUN_ID,
            "GITHUB_RUN_ATTEMPT": RUN_ATTEMPT,
            "TRIPWIRE": str(tripwire),
        },
    )

    assert result.returncode != 0
    assert sentinel.read_bytes() == b"preserve"
    assert not tripwire.exists()


def test_all_portable_publishers_and_workflow_share_exact_private_final_path() -> None:
    makefile = Path("Makefile").read_text(encoding="utf-8")
    workflow = Path(".github/workflows/foundation.yml").read_text(encoding="utf-8")
    make_destination = '--destination "$${PORTABLE_CI_ARTIFACT_ROOT:?}"'
    workflow_path = (
        "${{ runner.temp }}/trading-agent-ci-portable-publication."
        "${{ github.run_id }}.${{ github.run_attempt }}/artifact/**"
    )

    assert 'publication_parent="$${RUNNER_TEMP:?}/trading-agent-ci-portable-publication.' in makefile
    assert '$${GITHUB_RUN_ID:?}.$${GITHUB_RUN_ATTEMPT:?}"' in makefile
    assert 'artifact_root="$$publication_parent/artifact"' in makefile
    assert 'export PORTABLE_CI_ARTIFACT_ROOT="$$artifact_root"' in makefile
    assert makefile.count(make_destination) == 3
    assert '$(CURDIR)/runtime/state/ci-portable' not in makefile
    assert f"path: {workflow_path}" in workflow
    assert "path: runtime/state/ci-portable/**" not in workflow
    assert "trading-agent-ci-portable-evidence" not in workflow


def test_workflow_upload_reread_is_independent_of_checkout_namespace_swap(
    tmp_path: Path,
) -> None:
    workflow = Path(".github/workflows/foundation.yml").read_text(encoding="utf-8")
    matched = re.search(r"^\s+path:\s+([^\n]+)$", workflow, re.MULTILINE)
    assert matched is not None
    configured = matched.group(1).strip()
    runner_temp = tmp_path / "runner-temp"
    runner_temp.mkdir(mode=0o700)
    private_artifact = runner_temp / ARTIFACT_DIRECTORY
    private_artifact.mkdir(parents=True, mode=0o700)
    safe_payload = private_artifact / "root-remainder-failure.json"
    safe_payload.write_bytes(b"sealed-safe-bytes")
    safe_payload.chmod(0o400)
    private_artifact.chmod(0o500)
    checkout = tmp_path / "workspace/trading-agent"
    old_artifact = checkout / "runtime/state/ci-portable"
    old_artifact.mkdir(parents=True)
    (old_artifact / safe_payload.name).write_bytes(b"sealed-safe-bytes")
    displaced = tmp_path / "displaced-checkout"
    checkout.rename(displaced)
    foreign = checkout / "runtime/state/ci-portable"
    foreign.mkdir(parents=True)
    (foreign / safe_payload.name).write_bytes(b"foreign-bytes")

    expanded = configured.replace("${{ runner.temp }}", str(runner_temp))
    expanded = expanded.replace("${{ github.run_id }}", RUN_ID)
    expanded = expanded.replace("${{ github.run_attempt }}", RUN_ATTEMPT)
    artifact_path = Path(expanded.removesuffix("/**"))
    if not artifact_path.is_absolute():
        artifact_path = checkout / artifact_path

    assert (artifact_path / safe_payload.name).read_bytes() == b"sealed-safe-bytes"
