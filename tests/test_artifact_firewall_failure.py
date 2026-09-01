from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
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


def _topology_failure_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, str, str, str]:
    """Build the raw state left by a fail-closed native preflight."""
    tmp_path.mkdir()
    raw_root, run_id, head_sha, _skipped = _failure_source(tmp_path, monkeypatch)
    diagnostic = raw_root / "capability-topology/portable-root-remainder.failure-diagnostic.json"
    diagnostic.unlink()

    def passing_exact(nodes: tuple[str, ...], report: Path) -> tuple[str, ...]:
        report.write_text(json.dumps({
            "schema_version": 1,
            "component": "root",
            "pytest_exit_status": 0,
            "custody_policy": json.loads(os.environ["TEST_GOVERNANCE_CUSTODY_POLICY"]),
            "tests": [
                {
                    "test_node_id": node,
                    "component": "root",
                    "outcome": "passed",
                    "reason": "",
                    "phase": "call",
                }
                for node in nodes
            ],
        }), encoding="utf-8")
        return nodes

    topology.execute_portable_root_remainder(
        inventory=INVENTORY, evidence_root=raw_root, run_id=run_id,
        head_sha=head_sha, exact_runner=passing_exact,
        foundation_context_path=(
            raw_root / "capability-topology/foundation-context.json"
        ),
    )

    @topology.contextmanager
    def broken_probe(code: str):
        assert code == "NATIVE-BWRAP-OS-SANDBOX"
        yield topology.NativeProbeSession(
            code, "BROKEN", "NATIVE_IDENTITY_INVALID",
            topology._native_probe_record(
                code, exit_code=topology.NATIVE_PROBE_NOT_EXECUTED,
            ),
            -1, None, None, None, None,
        )

    with pytest.raises(topology.TopologyError, match="preflight is BROKEN"):
        topology.run_lane(
            lane="native-capabilities", inventory=INVENTORY,
            evidence_root=raw_root, run_id=run_id, head_sha=head_sha,
            foundation_context_path=(
                raw_root / "capability-topology/foundation-context.json"
            ),
            native_probe_factory=broken_probe,
        )
    return raw_root, run_id, head_sha, "NATIVE-BWRAP-OS-SANDBOX"


def _source_tree_run_stub(
    *, head_sha: str, mode: str,
    calls: list[tuple[list[str], str, str]],
) -> object:
    real_run = subprocess.run
    hostile = "password: private-value /private/source/path"

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[object]:
        command_name = ""
        if command == [
            "git", "diff-index", "--cached", "--quiet", head_sha, "--",
        ]:
            command_name = "diff-index"
        elif command == ["git", "read-tree", head_sha]:
            command_name = "prepare-index"
        elif command == [
            "git", "diff", "--quiet", "--no-ext-diff", "--no-textconv", "--",
        ]:
            command_name = "diff-files"
        elif command[:2] == ["git", "rev-parse"]:
            command_name = "head"
        elif command[:2] == ["git", "ls-tree"]:
            command_name = "tree"
        if not command_name:
            return real_run(command, **kwargs)
        environment = kwargs.get("env")
        optional_locks = ""
        index_file = ""
        if isinstance(environment, dict):
            optional_locks = str(environment.get("GIT_OPTIONAL_LOCKS", ""))
            index_file = str(environment.get("GIT_INDEX_FILE", ""))
        calls.append((command, optional_locks, index_file))
        if index_file:
            alternate_parent = Path(index_file).parent
            assert alternate_parent.parent == Path("/tmp")
            assert alternate_parent.name.startswith("trading-agent-source-index.")
            alternate_info = alternate_parent.stat()
            assert alternate_info.st_uid == os.geteuid()
            assert alternate_info.st_mode & 0o777 == 0o700
        if mode == f"{command_name}-os":
            raise OSError(hostile)
        if mode == f"{command_name}-subprocess":
            raise subprocess.SubprocessError(hostile)
        if mode == "head-output" and command_name == "head":
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, hostile)
        if mode == "head-empty" and command_name == "head":
            return subprocess.CompletedProcess(command, 0, stdout="\n")
        if mode == "head-invalid" and command_name == "head":
            return subprocess.CompletedProcess(command, 0, stdout=hostile + "\n")
        if mode == f"{command_name}-command":
            if kwargs.get("check"):
                raise subprocess.CalledProcessError(
                    2, command, output=hostile, stderr=hostile,
                )
            output: object = hostile.encode() if command_name == "tree" else hostile
            return subprocess.CompletedProcess(
                command, 2, stdout=output, stderr=hostile,
            )
        if mode == f"{command_name}-drift":
            return subprocess.CompletedProcess(command, 1)
        if mode == "head-mismatch" and command_name == "head":
            return subprocess.CompletedProcess(command, 0, stdout="f" * 40 + "\n")
        stdout: object = ""
        if command_name == "head":
            stdout = head_sha + "\n"
        elif command_name == "tree":
            stdout = b"tracked-tree"
        return subprocess.CompletedProcess(command, 0, stdout=stdout)

    return run


def _source_tree_repo(tmp_path: Path) -> tuple[Path, Path, str, str]:
    root = tmp_path / "source"
    root.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "core.filemode", "true"], cwd=root, check=True,
    )
    tracked = root / "tracked.txt"
    tracked.write_text("baseline\n", encoding="utf-8")
    tracked.chmod(0o644)
    subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
    subprocess.run(
        [
            "git", "-c", "user.name=P0 Test", "-c", "user.email=p0@example.invalid",
            "commit", "--quiet", "-m", "baseline",
        ],
        cwd=root, check=True,
    )
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    tree = subprocess.run(
        ["git", "ls-tree", "-rz", "--full-tree", head_sha],
        cwd=root, check=True, capture_output=True,
    ).stdout
    return root, tracked, head_sha, hashlib.sha256(tree).hexdigest()


def _git_index_state(root: Path) -> tuple[bytes, tuple[int, ...], bytes]:
    git_directory = Path(subprocess.run(
        ["git", "rev-parse", "--absolute-git-dir"], cwd=root, check=True,
        capture_output=True, text=True,
    ).stdout.strip())
    index = git_directory / "index"
    content = index.read_bytes()
    info = index.stat()
    stat_identity = (
        info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
        info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
    )
    stat_cache = subprocess.run(
        ["git", "ls-files", "--debug"], cwd=root, check=True,
        capture_output=True,
    ).stdout
    return content, stat_identity, stat_cache


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

    topology_raw, topology_run_id, topology_head_sha, code = _topology_failure_source(
        tmp_path / "topology", monkeypatch,
    )
    topology_destination = tmp_path / "runtime/state/ci-portable-topology"
    topology_manifest = firewall.publish_topology_failure(
        raw_root=topology_raw, destination=topology_destination,
        inventory=INVENTORY,
        foundation_context_path=(
            topology_raw / "capability-topology/foundation-context.json"
        ),
        repository_root=Path.cwd(),
    )

    assert topology_manifest["head_sha"] == topology_head_sha
    assert topology_manifest["run_metadata"]["run_id"] == topology_run_id
    assert set(
        path.relative_to(topology_destination).as_posix()
        for path in topology_destination.rglob("*") if path.is_file()
    ) == {"SHA256SUMS", "manifest.json", "topology-lane-failure.json"}
    topology_projection_raw = (
        topology_destination / "topology-lane-failure.json"
    ).read_bytes()
    topology_projection = json.loads(topology_projection_raw)
    assert topology_projection["diagnostic_only"] is True
    assert topology_projection["terminal_code"] == code
    assert topology_projection["terminal_outcome"] == "FAIL"
    assert topology_projection["terminal_preflight_state"] == "BROKEN"
    assert topology_projection["terminal_fact_class"] == "NATIVE_IDENTITY_INVALID"
    assert b'"outcome":"PASS"' not in topology_projection_raw


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
    if mutation == "malformed":
        topology_raw, _run_id, _head_sha, code = _topology_failure_source(
            tmp_path / "topology", monkeypatch,
        )
        marker = topology_raw / f"capability-topology/{code}.json"
        marker.write_bytes(marker.read_bytes() + b"\n")
        topology_destination = tmp_path / "topology-final"
        with pytest.raises(firewall.FirewallError):
            firewall.publish_topology_failure(
                raw_root=topology_raw, destination=topology_destination,
                inventory=INVENTORY,
                foundation_context_path=(
                    topology_raw / "capability-topology/foundation-context.json"
                ),
                repository_root=Path.cwd(),
            )
        assert not topology_destination.exists()
    if mutation == "foreign":
        topology_raw, topology_run_id, topology_head_sha, code = _topology_failure_source(
            tmp_path / "topology-prefix", monkeypatch,
        )
        marker = topology_raw / f"capability-topology/{code}.json"
        marker.unlink()
        shutil.rmtree(topology_raw / f"capability-topology/{code}.artifacts")
        context = topology.load_foundation_context(
            topology_raw / "capability-topology/foundation-context.json",
            run_id=topology_run_id, head_sha=topology_head_sha,
        )
        rows = topology.load_inventory(INVENTORY)
        _lane, expected = topology._expected_rows(
            rows, "NATIVE-USERNS-ROOT-PROVISION",
        )
        userns = topology.NativeProbeSession(
            "NATIVE-USERNS-ROOT-PROVISION", "BROKEN",
            "NATIVE_IDENTITY_INVALID",
            topology._native_probe_record(
                "NATIVE-USERNS-ROOT-PROVISION",
                exit_code=topology.NATIVE_PROBE_NOT_EXECUTED,
            ),
            -1, None, None, None, None,
        )
        topology._publish_native_failure_marker(
            receipt=topology.make_native_receipt(
                context=context, code=userns.code, expected=expected,
                collected=(), session=userns, outcome="FAIL",
                selected_test_count=0, passed=0, failed=0, unavailable=0,
            ),
            evidence_root=topology_raw,
        )
        with pytest.raises(firewall.FirewallError):
            firewall.publish_topology_failure(
                raw_root=topology_raw,
                destination=tmp_path / "topology-prefix-final",
                inventory=INVENTORY,
                foundation_context_path=(
                    topology_raw / "capability-topology/foundation-context.json"
                ),
                repository_root=Path.cwd(),
            )


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
    with pytest.raises(firewall.FailurePublicationError) as raised:
        _publish_failure(raw_root, destination, monkeypatch)
    assert raised.value.stage == "PUBLICATION"
    assert sentinel.read_text(encoding="utf-8") == "preserve"


@pytest.mark.parametrize(
    ("mode", "substage", "reason", "expected_commands"),
    [
        ("diff-index-drift", "DIFF_INDEX", "DRIFT", 1),
        ("diff-index-command", "DIFF_INDEX", "COMMAND_FAILURE", 1),
        ("diff-index-os", "DIFF_INDEX", "SPAWN_FAILURE", 1),
        ("diff-index-subprocess", "DIFF_INDEX", "COMMAND_FAILURE", 1),
        ("prepare-index-command", "DIFF_FILES", "COMMAND_FAILURE", 2),
        ("prepare-index-os", "DIFF_FILES", "SPAWN_FAILURE", 2),
        ("prepare-index-subprocess", "DIFF_FILES", "COMMAND_FAILURE", 2),
        ("diff-files-drift", "DIFF_FILES", "DRIFT", 3),
        ("diff-files-command", "DIFF_FILES", "COMMAND_FAILURE", 3),
        ("diff-files-os", "DIFF_FILES", "SPAWN_FAILURE", 3),
        ("diff-files-subprocess", "DIFF_FILES", "COMMAND_FAILURE", 3),
        ("head-mismatch", "HEAD_BINDING", "MISMATCH", 4),
        ("head-command", "HEAD_BINDING", "COMMAND_FAILURE", 4),
        ("head-os", "HEAD_BINDING", "SPAWN_FAILURE", 4),
        ("head-subprocess", "HEAD_BINDING", "COMMAND_FAILURE", 4),
        ("head-output", "HEAD_BINDING", "OUTPUT_FAILURE", 4),
        ("head-empty", "HEAD_BINDING", "OUTPUT_FAILURE", 4),
        ("head-invalid", "HEAD_BINDING", "OUTPUT_FAILURE", 4),
        ("tree-command", "TREE_ENUMERATION", "COMMAND_FAILURE", 5),
        ("tree-os", "TREE_ENUMERATION", "SPAWN_FAILURE", 5),
        ("tree-subprocess", "TREE_ENUMERATION", "COMMAND_FAILURE", 5),
    ],
)
def test_source_tree_identity_reports_only_the_exact_closed_command_failure(
    monkeypatch: pytest.MonkeyPatch, mode: str, substage: str, reason: str,
    expected_commands: int,
) -> None:
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], check=True, capture_output=True, text=True,
    ).stdout.strip()
    calls: list[tuple[list[str], str, str]] = []
    monkeypatch.setenv("GIT_INDEX_FILE", "/private/source/path")
    monkeypatch.setenv("GIT_OPTIONAL_LOCKS", "hostile")
    monkeypatch.setattr(
        firewall.subprocess, "run",
        _source_tree_run_stub(head_sha=head_sha, mode=mode, calls=calls),
    )

    with pytest.raises(BaseException) as raised:
        firewall._source_tree_identity(Path.cwd(), head_sha)

    error = raised.value
    assert type(error).__name__ == "SourceTreeError"
    assert isinstance(error, firewall.FirewallError)
    assert error.code == "ARTIFACT_FIREWALL_REJECTED"
    assert error.category == "LAYOUT"
    assert error.relative_path == ""
    assert error.sha256 == ""
    assert error.args == ("source-tree check failed at a closed boundary",)
    assert error.source_tree_substage == substage
    assert error.source_tree_reason == reason
    assert error.__context__ is None
    assert error.__cause__ is None
    assert "private-value" not in repr(error.__dict__)
    assert "private/source/path" not in repr(error.__dict__)
    assert "f" * 40 not in repr(error.__dict__)
    expected = [
        ["git", "diff-index", "--cached", "--quiet", head_sha, "--"],
        ["git", "read-tree", head_sha],
        [
            "git", "diff", "--quiet", "--no-ext-diff", "--no-textconv", "--",
        ],
        ["git", "rev-parse", "HEAD"],
        ["git", "ls-tree", "-rz", "--full-tree", head_sha],
    ][:expected_commands]
    assert [command for command, _locks, _index in calls] == expected
    assert calls[0][1:] == ("0", "")
    alternate_indexes = [index for _command, _locks, index in calls if index]
    if expected_commands >= 2:
        assert len(set(alternate_indexes)) == 1
        assert all(locks == "0" for _command, locks, index in calls if index)
        alternate_index = Path(alternate_indexes[0])
        assert not alternate_index.parent.exists()
    else:
        assert alternate_indexes == []
    assert os.environ["GIT_INDEX_FILE"] == "/private/source/path"
    assert os.environ["GIT_OPTIONAL_LOCKS"] == "hostile"


def test_source_tree_identity_ignores_mtime_only_stat_cache_drift(
    tmp_path: Path,
) -> None:
    root, tracked, head_sha, expected_identity = _source_tree_repo(tmp_path)
    tracked_info = tracked.stat()
    os.utime(
        tracked,
        ns=(tracked_info.st_atime_ns, 1_893_456_000_000_000_000),
    )

    assert subprocess.run(
        ["git", "diff-index", "--quiet", "HEAD", "--"],
        cwd=root, check=False,
    ).returncode == 1
    assert subprocess.run(
        ["git", "diff-files", "--quiet", "--"], cwd=root, check=False,
    ).returncode == 1
    index_before = _git_index_state(root)

    assert firewall._source_tree_identity(root, head_sha) == expected_identity
    assert _git_index_state(root) == index_before
    assert subprocess.run(
        ["git", "diff-index", "--quiet", "HEAD", "--"],
        cwd=root, check=False,
    ).returncode == 1
    assert subprocess.run(
        ["git", "diff-files", "--quiet", "--"], cwd=root, check=False,
    ).returncode == 1
    assert _git_index_state(root) == index_before


@pytest.mark.parametrize(
    ("drift", "staged", "substage"),
    [
        ("content", True, "DIFF_INDEX"),
        ("content", False, "DIFF_FILES"),
        ("mode", True, "DIFF_INDEX"),
        ("mode", False, "DIFF_FILES"),
    ],
)
def test_source_tree_identity_rejects_real_staged_and_unstaged_drift(
    tmp_path: Path, drift: str, staged: bool, substage: str,
) -> None:
    root, tracked, head_sha, _expected_identity = _source_tree_repo(tmp_path)
    if drift == "content":
        tracked.write_text("changed\n", encoding="utf-8")
    else:
        tracked.chmod(0o755)
    if staged:
        subprocess.run(["git", "add", "tracked.txt"], cwd=root, check=True)
    index_before = _git_index_state(root)

    with pytest.raises(firewall.SourceTreeError) as raised:
        firewall._source_tree_identity(root, head_sha)

    assert raised.value.source_tree_substage == substage
    assert raised.value.source_tree_reason == "DRIFT"
    assert raised.value.__context__ is None
    assert raised.value.__cause__ is None
    assert _git_index_state(root) == index_before


@pytest.mark.parametrize(
    ("mode", "substage", "reason"),
    [
        ("diff-index-drift", "DIFF_INDEX", "DRIFT"),
        ("diff-files-drift", "DIFF_FILES", "DRIFT"),
        ("head-mismatch", "HEAD_BINDING", "MISMATCH"),
        ("tree-command", "TREE_ENUMERATION", "COMMAND_FAILURE"),
    ],
)
def test_failure_publisher_cli_reports_exact_closed_source_tree_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    mode: str, substage: str, reason: str,
) -> None:
    raw_root, _run_id, head_sha, _skipped = _failure_source(tmp_path, monkeypatch)
    destination = tmp_path / "publication/artifact"
    destination.parent.mkdir(mode=0o700)
    calls: list[tuple[list[str], str, str]] = []
    source_tree_identity = firewall._source_tree_identity

    def classified_source_tree(root: Path, expected_head: str) -> str:
        with monkeypatch.context() as command_patch:
            command_patch.setattr(
                firewall.subprocess, "run",
                _source_tree_run_stub(
                    head_sha=head_sha, mode=mode, calls=calls,
                ),
            )
            return source_tree_identity(root, expected_head)

    monkeypatch.setattr(firewall, "_source_tree_identity", classified_source_tree)

    status = firewall.main([
        "publish-failure",
        "--raw-root", str(raw_root),
        "--destination", str(destination),
        "--inventory", str(INVENTORY),
        "--foundation-context-path",
        str(raw_root / "capability-topology/foundation-context.json"),
        "--repository-root", str(Path.cwd()),
    ])

    captured = capsys.readouterr()
    assert status == 2
    assert captured.out == ""
    assert captured.err == (
        "artifact firewall: ARTIFACT_FIREWALL_REJECTED LAYOUT SOURCE_TREE "
        f"{substage} {reason}\n"
    )
    assert "private-value" not in captured.err
    assert "private/source/path" not in captured.err
    assert "f" * 40 not in captured.err
    assert not destination.exists()


@pytest.mark.parametrize(
    ("stage", "target", "error_type"),
    [
        ("RAW_BINDING", "raw-binding", "firewall"),
        ("RAW_BINDING", "raw-binding", "os"),
        ("RAW_BINDING", "raw-binding", "value"),
        ("PROJECTION", "projection", "firewall"),
        ("PROJECTION", "projection", "os"),
        ("PROJECTION", "projection", "value"),
        ("SOURCE_TREE", "source-tree", "firewall"),
        ("SOURCE_TREE", "source-tree", "os"),
        ("SOURCE_TREE", "source-tree", "value"),
        ("SOURCE_TREE", "source-tree", "subprocess"),
        ("PUBLICATION", "publication", "firewall"),
        ("PUBLICATION", "publication", "os"),
        ("PUBLICATION", "publication", "value"),
    ],
)
def test_failure_publisher_drops_every_underlying_exception_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    stage: str, target: str, error_type: str,
) -> None:
    raw_root, _run_id, _head_sha, _skipped = _failure_source(tmp_path, monkeypatch)
    destination = tmp_path / "publication/artifact"
    destination.parent.mkdir(mode=0o700)
    hostile = "password: private-value /private/raw/reason"
    hostile_path = "private/raw/reason"
    hostile_digest = "f" * 64

    def reject(*_args: object, **_kwargs: object) -> object:
        if error_type == "firewall":
            raise firewall.FirewallError(
                hostile, code="ARTIFACT_SECRET_REJECTED", category="PASSWORD",
                relative_path=hostile_path, sha256=hostile_digest,
            )
        if error_type == "os":
            raise OSError(hostile)
        if error_type == "value":
            raise ValueError(hostile)
        raise subprocess.SubprocessError(hostile)

    if target == "raw-binding":
        monkeypatch.setattr(firewall, "_failure_payloads_from_raw", reject)
    elif target == "projection":
        monkeypatch.setattr(firewall, "_build_candidate", reject)
    elif target == "source-tree":
        monkeypatch.setattr(firewall, "_source_tree_identity", reject)
    else:
        monkeypatch.setattr(
            firewall, "_source_tree_identity", lambda _root, _head: TREE,
        )
        monkeypatch.setattr(firewall, "_publish_evidence_set", reject)

    with pytest.raises(firewall.FailurePublicationError) as raised:
        firewall.publish_root_remainder_failure(
            raw_root=raw_root,
            destination=destination,
            inventory=INVENTORY,
            foundation_context_path=(
                raw_root / "capability-topology/foundation-context.json"
            ),
            repository_root=Path.cwd(),
        )

    error = raised.value
    expected_code = (
        "ARTIFACT_SECRET_REJECTED"
        if error_type == "firewall" else "ARTIFACT_FIREWALL_REJECTED"
    )
    expected_category = "PASSWORD" if error_type == "firewall" else "LAYOUT"
    assert error.stage == stage
    assert error.code == expected_code
    assert error.category == expected_category
    assert error.relative_path == ""
    assert error.sha256 == ""
    assert error.args == ("failure publication rejected at a closed stage",)
    assert error.__context__ is None
    assert error.__cause__ is None
    assert hostile not in repr(error.__dict__)
    assert hostile_path not in repr(error.__dict__)
    assert hostile_digest not in repr(error.__dict__)
    assert not destination.exists()


@pytest.mark.parametrize(
    ("stage", "target", "code", "category"),
    [
        ("RAW_BINDING", "raw-binding", "ARTIFACT_FIREWALL_REJECTED", "LAYOUT"),
        ("PROJECTION", "projection", "ARTIFACT_FIREWALL_REJECTED", "LAYOUT"),
        ("SOURCE_TREE", "source-tree", "ARTIFACT_FIREWALL_REJECTED", "LAYOUT"),
        ("PUBLICATION", "publication", "ARTIFACT_SECRET_REJECTED", "PASSWORD"),
    ],
)
def test_failure_publisher_cli_reports_only_the_closed_failure_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    stage: str, target: str, code: str, category: str,
) -> None:
    raw_root, _run_id, _head_sha, _skipped = _failure_source(tmp_path, monkeypatch)
    destination = tmp_path / "publication/artifact"
    destination.parent.mkdir(mode=0o700)
    hostile = "password: private-value /private/raw/reason"

    def reject(*_args: object, **_kwargs: object) -> object:
        raise firewall.FirewallError(
            hostile, code=code, category=category, relative_path="private/raw/reason",
            sha256="f" * 64,
        )

    if target == "raw-binding":
        def reject_identity() -> tuple[str, str]:
            raise topology.TopologyError(hostile)

        monkeypatch.setattr(topology, "_active_foundation_identity", reject_identity)
    elif target == "projection":
        monkeypatch.setattr(firewall, "_build_candidate", reject)
    elif target == "source-tree":
        monkeypatch.setattr(firewall, "_source_tree_identity", reject)
    else:
        monkeypatch.setattr(
            firewall, "_source_tree_identity", lambda _root, _head: TREE,
        )
        monkeypatch.setattr(firewall, "_publish_evidence_set", reject)

    status = firewall.main([
        "publish-failure",
        "--raw-root", str(raw_root),
        "--destination", str(destination),
        "--inventory", str(INVENTORY),
        "--foundation-context-path",
        str(raw_root / "capability-topology/foundation-context.json"),
        "--repository-root", str(Path.cwd()),
    ])

    captured = capsys.readouterr()
    assert status == 2
    assert captured.out == ""
    assert captured.err == (
        f"artifact firewall: {code} {category} {stage}\n"
    )
    assert hostile not in captured.err
    assert "private/raw/reason" not in captured.err
    assert "f" * 64 not in captured.err
    assert not destination.exists()


@pytest.mark.parametrize(
    ("inner_status", "create_diagnostic", "create_topology_failure", "expected_publish_count"),
    [(0, False, False, 0), (23, False, True, 1), (37, True, False, 1)],
)
def test_ci_portable_catch_is_single_shot_and_preserves_the_original_status(
    tmp_path: Path, inner_status: int, create_diagnostic: bool,
    create_topology_failure: bool,
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
        + (
            'mkdir -p "$TEST_EVIDENCE_DIR/capability-topology/NATIVE-BWRAP-OS-SANDBOX.artifacts"\n'
            'touch "$TEST_EVIDENCE_DIR/capability-topology/NATIVE-BWRAP-OS-SANDBOX.json"\n'
            if create_topology_failure else ""
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
    if create_topology_failure:
        assert published[0].startswith("run python -m scripts.check_artifact_firewall publish-topology-failure ")
    if create_diagnostic:
        assert published[0].startswith("run python -m scripts.check_artifact_firewall publish-failure ")
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
        "check-portable-defect-closure check-p0-baseline check-p0-maintainability "
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
    assert makefile.count(make_destination) == 4
    assert '$(CURDIR)/runtime/state/ci-portable' not in makefile
    assert f"path: {workflow_path}" in workflow
    assert "path: runtime/state/ci-portable/**" not in workflow
    assert "trading-agent-ci-portable-evidence" not in workflow


def test_workflow_upload_reread_is_independent_of_checkout_namespace_swap(
    tmp_path: Path,
) -> None:
    workflow = Path(".github/workflows/foundation.yml").read_text(encoding="utf-8")
    matched = re.search(
        r"^\s+path:\s+([^\n]*trading-agent-ci-portable-publication[^\n]+)$",
        workflow,
        re.MULTILINE,
    )
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
