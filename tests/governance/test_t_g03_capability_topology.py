from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import pytest
from pathlib import Path
import stat
import tempfile
import subprocess
from types import SimpleNamespace

from scripts import t_g03_capability_topology as topology
from scripts import test_governance_pytest as governance_plugin


def _seal_portable_root_baseline(
    monkeypatch: pytest.MonkeyPatch, evidence: Path, raw: str, *, run_id: str, head_sha: str,
    foundation_context_path: Path | None = None,
) -> None:
    extension = Path(raw) / "custody.so"
    extension.write_bytes(b"verified custody fixture")
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    monkeypatch.setenv("PACKAGE6_FD_CUSTODY_EXTENSION_PATH", str(extension))
    monkeypatch.setenv(
        "PACKAGE6_FD_CUSTODY_EXTENSION_SHA256",
        topology.hashlib.sha256(extension.read_bytes()).hexdigest(),
    )
    inventory = Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv")
    rows = topology.load_inventory(inventory)
    closure = topology.load_portable_defect_closure(head_sha=head_sha)
    topology.collect_portable_root_baseline(
        inventory=inventory,
        evidence_root=evidence,
        run_id=run_id,
        head_sha=head_sha,
        collector=lambda: tuple(sorted(
            {row.node_id for row in rows} | {row.node_id for row in closure}
        )),
        foundation_context_path=foundation_context_path,
    )
    topology.prepare_portable_root_remainder(
        inventory=inventory, evidence_root=evidence, run_id=run_id, head_sha=head_sha,
        foundation_context_path=foundation_context_path,
    )


def _passing_exact(nodes: tuple[str, ...], report: Path) -> tuple[str, ...]:
    report.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    report.write_text(
        topology.json.dumps({
            "schema_version": 1,
            "component": "root",
            "pytest_exit_status": 0,
            "custody_policy": topology.json.loads(os.environ["TEST_GOVERNANCE_CUSTODY_POLICY"]),
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
        }),
        encoding="utf-8",
    )
    return nodes


def _available_native_probe_factory(
    root: Path,
) -> object:
    @topology.contextmanager
    def factory(code: str):
        executable = root / f"{code}.authority"
        executable.write_bytes(b"retained native authority")
        descriptor = os.open(executable, os.O_RDONLY | os.O_CLOEXEC)
        named = executable.lstat()
        session = topology.NativeProbeSession(
            code, "AVAILABLE", "NATIVE_CAPABILITY_VALIDATED",
            topology._native_probe_record(
                code, exit_code=0,
                executable_sha256=hashlib.sha256(
                    b"retained native authority"
                ).hexdigest(),
            ),
            descriptor, executable, topology._artifact_identity(named),
            topology._artifact_identity(os.fstat(descriptor)), None,
        )
        try:
            yield session
        finally:
            os.close(descriptor)

    return factory


def _patch_native_identity_postcheck(monkeypatch: pytest.MonkeyPatch) -> None:
    def postcheck(session: topology.NativeProbeSession) -> None:
        assert session.executable_path is not None
        if (
            topology._artifact_identity(session.executable_path.lstat())
            != session.named_identity
            or topology._artifact_identity(os.fstat(session.descriptor))
            != session.descriptor_identity
            or topology._digest_fd(session.descriptor)
            != session.probe["executable_sha256"]
        ):
            raise topology.TopologyError(
                "native executable identity changed during execution"
            )

    monkeypatch.setattr(topology, "_postcheck_native_probe", postcheck)


def _receipt(**overrides: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": "t-g03a-capability-receipt/v1",
        "foundation_run_id": "31641536482",
        "foundation_head_sha": "18f22198c65c7bc735aeb848d8fda55209d01e78",
        "inventory_sha256": topology.LOCKED_INVENTORY_SHA256,
        "lane": "portable-source",
        "capability_or_authority_code": "SRC-SEMANTIC-FIXTURE-IDENTITY",
        "expected_node_ids": ["tests/runtime_release/test_semantic.py::test_one"],
        "collected_node_ids": ["tests/runtime_release/test_semantic.py::test_one"],
        "completeness_sha256": "",
        "preflight_state": "AVAILABLE",
        "redacted_fact_class": "SOURCE_TEST_EXECUTED",
        "outcome": "PASS",
        "receipt_sha256": "",
    }
    document.update(overrides)
    document["completeness_sha256"] = topology.completeness_sha256(document)
    document["receipt_sha256"] = topology.payload_sha256(document)
    return document


def _native_context() -> dict[str, object]:
    return {
        "schema_version": "t-g03a-foundation-context/v1",
        "foundation_run_id": "31641536482",
        "foundation_head_sha": "18f22198c65c7bc735aeb848d8fda55209d01e78",
        "foundation_validation_date": "2026-08-13",
        "foundation_context_sha256": "4" * 64,
    }


def _native_receipt(**overrides: object) -> dict[str, object]:
    rows = topology.load_inventory(Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"))
    code = str(overrides.get("capability_or_authority_code", "NATIVE-USERNS-ROOT-PROVISION"))
    expected = list(topology._expected_rows(rows, code)[1])
    context = _native_context()
    document: dict[str, object] = {
        "schema_version": "t-g03a-native-capability-receipt/v2",
        "foundation_run_id": context["foundation_run_id"],
        "foundation_head_sha": context["foundation_head_sha"],
        "foundation_validation_date": context["foundation_validation_date"],
        "foundation_context_sha256": context["foundation_context_sha256"],
        "inventory_sha256": topology.LOCKED_INVENTORY_SHA256,
        "lane": "native-capabilities",
        "capability_or_authority_code": code,
        "expected_node_ids": expected,
        "collected_node_ids": [],
        "preflight_state": "UNAVAILABLE",
        "redacted_fact_class": "NATIVE_COMPONENT_ABSENT",
        "probe": {
            "command_id": (
                "BWRAP_USER_PID_NET_ISOLATION_V1"
                if code == "NATIVE-BWRAP-OS-SANDBOX"
                else "UNSHARE_MAP_ROOT_USER_V1"
            ),
            "exit_code": -1,
            "stdout_sha256": hashlib.sha256(b"").hexdigest(),
            "stderr_sha256": hashlib.sha256(b"").hexdigest(),
            "executable_sha256": hashlib.sha256(b"").hexdigest(),
        },
        "selected_test_count": 0,
        "passed": 0,
        "failed": 0,
        "unavailable": len(expected),
        "completeness_sha256": "",
        "outcome": "DEFERRED",
        "receipt_sha256": "",
    }
    document.update(overrides)
    completeness_payload = {
        key: value
        for key, value in document.items()
        if key not in {"completeness_sha256", "receipt_sha256"}
    }
    document["completeness_sha256"] = hashlib.sha256(
        json.dumps(
            completeness_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    receipt_payload = {key: value for key, value in document.items() if key != "receipt_sha256"}
    document["receipt_sha256"] = hashlib.sha256(
        json.dumps(
            receipt_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return document


def _write_native_artifact_fixture(
    topology_root: Path, receipt: dict[str, object],
    governance_raw: bytes | None = None,
) -> Path:
    topology_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    topology_root.chmod(0o700)
    candidate = topology._stage_native_candidate(
        topology_root, receipt, governance_raw,
    )
    code = str(receipt["capability_or_authority_code"])
    marker = topology_root / f"{code}.json"
    topology._publish_native_candidate_bundle(
        candidate, topology_root / f"{code}.artifacts",
    )
    topology._publish_native_acceptance_marker(
        marker, topology.canonical_json_bytes(receipt),
    )
    return marker


def test_native_v2_receipt_binds_context_probe_and_exact_unavailable_count() -> None:
    """Break caught: native deferral can omit its sealed context, probe, or exact count."""
    rows = topology.load_inventory(Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"))
    context = _native_context()
    receipt = _native_receipt()

    assert topology.validate_receipt(
        topology.canonical_json_bytes(receipt), rows=rows,
        foundation_run_id=str(context["foundation_run_id"]),
        foundation_head_sha=str(context["foundation_head_sha"]),
        foundation_context=context,
    ) == receipt

    for mutation in (
        {"foundation_context_sha256": "5" * 64},
        {"foundation_validation_date": "2026-08-14"},
        {"unavailable": 7},
        {"selected_test_count": False},
    ):
        forged = _native_receipt(**mutation)
        with pytest.raises(topology.TopologyError):
            topology.validate_receipt(
                topology.canonical_json_bytes(forged), rows=rows,
                foundation_run_id=str(context["foundation_run_id"]),
                foundation_head_sha=str(context["foundation_head_sha"]),
                foundation_context=context,
            )


@pytest.mark.parametrize(
    "mutation",
    [
        {"foundation_run_id": "31641536483"},
        {"foundation_head_sha": "2" * 40},
        {"inventory_sha256": "3" * 64},
        {"expected_node_ids": []},
    ],
)
def test_native_v2_receipt_rejects_every_foreign_binding(
    mutation: dict[str, object],
) -> None:
    """Break caught: a self-consistent v2 receipt can be replayed across authority bindings."""
    rows = topology.load_inventory(Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"))
    context = _native_context()
    forged = _native_receipt(**mutation)

    with pytest.raises(topology.TopologyError):
        topology.validate_receipt(
            topology.canonical_json_bytes(forged), rows=rows,
            foundation_run_id=str(context["foundation_run_id"]),
            foundation_head_sha=str(context["foundation_head_sha"]),
            foundation_context=context,
        )

    mixed = _native_receipt()
    mixed["capability_or_authority_code"] = "NATIVE-BWRAP-OS-SANDBOX"
    mixed["completeness_sha256"] = topology.native_completeness_sha256(mixed)
    mixed["receipt_sha256"] = topology.payload_sha256(mixed)
    with pytest.raises(topology.TopologyError, match="mapping"):
        topology.validate_receipt(
            topology.canonical_json_bytes(mixed), rows=rows,
            foundation_run_id=str(context["foundation_run_id"]),
            foundation_head_sha=str(context["foundation_head_sha"]),
            foundation_context=context,
        )


def test_native_v1_is_stale_while_external_v1_remains_valid() -> None:
    """Break caught: the shared v1 parser keeps accepting native evidence after migration."""
    rows = topology.load_inventory(Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"))
    run, head = "31641536482", "18f22198c65c7bc735aeb848d8fda55209d01e78"
    native_code = "NATIVE-USERNS-ROOT-PROVISION"
    _, native_nodes = topology._expected_rows(rows, native_code)
    stale = _receipt(
        foundation_run_id=run, foundation_head_sha=head, lane="native-capabilities",
        capability_or_authority_code=native_code, expected_node_ids=list(native_nodes),
        collected_node_ids=[], preflight_state="UNAVAILABLE",
        redacted_fact_class="NATIVE_COMPONENT_ABSENT", outcome="DEFERRED",
    )
    with pytest.raises(topology.TopologyError, match="native v1"):
        topology.validate_receipt(
            topology.canonical_json_bytes(stale), rows=rows,
            foundation_run_id=run, foundation_head_sha=head,
            foundation_context=_native_context(),
        )

    external_code = "EXT-PHASE3B-CORPUS"
    _, external_nodes = topology._expected_rows(rows, external_code)
    external = _receipt(
        foundation_run_id=run, foundation_head_sha=head, lane="external-authorities",
        capability_or_authority_code=external_code, expected_node_ids=list(external_nodes),
        collected_node_ids=[], preflight_state="ABSENT",
        redacted_fact_class="AUTHORITY_ROOT_ABSENT", outcome="DEFERRED",
    )
    assert topology.validate_receipt(
        topology.canonical_json_bytes(external), rows=rows,
        foundation_run_id=run, foundation_head_sha=head,
    ) == external


def test_real_native_probe_argv_uses_retained_fd_and_exact_namespace_operations() -> None:
    """Break caught: version/help or a named executable is substituted for the namespace probe."""
    observed: list[tuple[list[str], dict[str, object]]] = []

    def successful_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    for code, suffix in (
        (
            "NATIVE-BWRAP-OS-SANDBOX",
            [
                "--die-with-parent", "--unshare-user", "--unshare-pid", "--unshare-net",
                "--new-session", "--clearenv", "--ro-bind", "/usr", "/usr",
                "--symlink", "usr/lib64", "/lib64", "--proc", "/proc", "--dev", "/dev",
                "--tmpfs", "/tmp", "--", "/usr/bin/true",
            ],
        ),
        ("NATIVE-USERNS-ROOT-PROVISION", ["--user", "--map-root-user", "/usr/bin/true"]),
    ):
        with topology._retained_native_probe(code, runner=successful_runner) as probe:
            assert probe.state == "AVAILABLE"
            assert probe.probe["exit_code"] == 0
            command, kwargs = observed[-1]
            assert command[0].startswith("/proc/self/fd/")
            assert command[1:] == suffix
            assert kwargs["pass_fds"] == (probe.descriptor,)
            assert kwargs["env"] == {"LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"}


def test_native_probe_classification_is_narrow_and_never_uses_path_fallback(
    tmp_path: Path,
) -> None:
    """Break caught: misleading output, timeout, or a nonregular leaf becomes DEFERRED."""
    opened = topology._open_unshare_session(Path("/usr/bin/unshare"))
    assert opened.state == "PROBE_PENDING"
    try:
        exact_denial = next(iter(topology.NATIVE_DENIAL_STDERR[opened.code]))
        deferred = topology._execute_native_probe(
            opened,
            runner=lambda command, **_kwargs: subprocess.CompletedProcess(
                command, 1, stdout=b"", stderr=exact_denial,
            ),
        )
        assert (deferred.state, deferred.fact) == (
            "UNAVAILABLE", "RUNNER_POLICY_DISALLOWS_USERNS",
        )

        for runner in (
            lambda command, **_kwargs: subprocess.CompletedProcess(
                command, 1, stdout=b"partial", stderr=exact_denial,
            ),
            lambda command, **_kwargs: subprocess.CompletedProcess(
                command, 1, stdout=b"", stderr=exact_denial + b" misleading",
            ),
            lambda _command, **_kwargs: (_ for _ in ()).throw(
                subprocess.TimeoutExpired("unshare", 10),
            ),
        ):
            broken = topology._execute_native_probe(opened, runner=runner)
            assert (broken.state, broken.fact) == ("BROKEN", "NATIVE_PROBE_INVALID")
    finally:
        os.close(opened.descriptor)

    missing = topology._open_unshare_session(Path("/usr/bin/p0-07-missing-unshare"))
    assert (missing.state, missing.fact) == ("UNAVAILABLE", "NATIVE_COMPONENT_ABSENT")
    directory = tmp_path / "unshare"
    directory.mkdir()
    invalid = topology._open_unshare_session(directory)
    assert (invalid.state, invalid.fact) == ("BROKEN", "NATIVE_IDENTITY_INVALID")
    assert invalid.descriptor == -1


def test_native_caller_mode_accepts_deferred_portably_but_host_requires_pass(
    tmp_path: Path,
) -> None:
    """Break caught: a host qualification converts an unavailable native resource into success."""
    assert not hasattr(topology, "validate_native_receipt_set")
    rows = topology.load_inventory(Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"))
    context = _native_context()
    topology_root = tmp_path / "capability-topology"
    topology_root.mkdir(mode=0o700)
    for code in ("NATIVE-BWRAP-OS-SANDBOX", "NATIVE-USERNS-ROOT-PROVISION"):
        _write_native_artifact_fixture(
            topology_root, _native_receipt(capability_or_authority_code=code),
        )

    assert topology.validate_native_artifacts(
        topology_root, rows=rows, foundation_context=context,
        sealed_custody={}, require_pass=False,
    ) == "DEFERRED"
    with pytest.raises(topology.TopologyError, match="requires PASS"):
        topology.validate_native_artifacts(
            topology_root, rows=rows, foundation_context=context,
            sealed_custody={}, require_pass=True,
        )


def test_native_available_path_runs_exact_16_and_8_once_and_failure_publishes_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: an available native group is partial, duplicated, or fails without a FAIL receipt."""
    run_id = "31641536482"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)

    def successful_probe_runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    @topology.contextmanager
    def real_authority_success(code: str):
        with topology._retained_native_probe(code, runner=successful_probe_runner) as session:
            yield session

    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        context = topology._capture_foundation_context(
            evidence, clock=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc),
        )
        topology.reserve_topology_evidence(
            evidence, run_id=run_id, head_sha=head, foundation_context_path=context,
        )
        _seal_portable_root_baseline(
            monkeypatch, evidence, raw, run_id=run_id, head_sha=head,
            foundation_context_path=context,
        )
        selected: list[tuple[str, ...]] = []

        def exact(nodes: tuple[str, ...], report: Path) -> tuple[str, ...]:
            selected.append(nodes)
            return _passing_exact(nodes, report)

        receipts = topology.run_lane(
            lane="native-capabilities",
            inventory=Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"),
            evidence_root=evidence, run_id=run_id, head_sha=head,
            foundation_context_path=context, exact_runner=exact,
            native_probe_factory=real_authority_success,
        )
        assert sorted(len(nodes) for nodes in selected) == [8, 16]
        assert len({node for group in selected for node in group}) == 24
        assert all(topology.parse_receipt(path.read_bytes())["outcome"] == "PASS" for path in receipts)

    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        context = topology._capture_foundation_context(
            evidence, clock=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc),
        )
        topology.reserve_topology_evidence(
            evidence, run_id=run_id, head_sha=head, foundation_context_path=context,
        )
        _seal_portable_root_baseline(
            monkeypatch, evidence, raw, run_id=run_id, head_sha=head,
            foundation_context_path=context,
        )

        def broken_exact(_nodes: tuple[str, ...], _report: Path) -> tuple[str, ...]:
            raise topology.TopologyError("bounded exact execution failed")

        with pytest.raises(topology.TopologyError, match="bounded exact execution failed"):
            topology.run_lane(
                lane="native-capabilities",
                inventory=Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"),
                evidence_root=evidence, run_id=run_id, head_sha=head,
                foundation_context_path=context, exact_runner=broken_exact,
                native_probe_factory=real_authority_success,
            )
        fail_path = evidence / "capability-topology/NATIVE-BWRAP-OS-SANDBOX.json"
        assert topology.parse_receipt(fail_path.read_bytes())["outcome"] == "FAIL"
        assert not (evidence / "capability-topology/NATIVE-BWRAP-OS-SANDBOX.governance.json").exists()


def test_broken_native_probe_publishes_fail_before_lane_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: present-invalid native authority raises without durable FAIL evidence."""
    run_id = "31641536482"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)

    @topology.contextmanager
    def broken_probe(code: str):
        yield topology.NativeProbeSession(
            code, "BROKEN", "NATIVE_IDENTITY_INVALID",
            topology._native_probe_record(
                code, exit_code=topology.NATIVE_PROBE_NOT_EXECUTED,
            ),
            -1, None, None, None, None,
        )

    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        context_path = topology._capture_foundation_context(
            evidence, clock=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc),
        )
        topology.reserve_topology_evidence(
            evidence, run_id=run_id, head_sha=head,
            foundation_context_path=context_path,
        )
        with pytest.raises(topology.TopologyError, match="preflight is BROKEN"):
            topology.run_lane(
                lane="native-capabilities",
                inventory=Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"),
                evidence_root=evidence, run_id=run_id, head_sha=head,
                foundation_context_path=context_path,
                native_probe_factory=broken_probe,
            )
        fail_path = evidence / "capability-topology/NATIVE-BWRAP-OS-SANDBOX.json"
        receipt = topology.parse_receipt(fail_path.read_bytes())
        assert receipt["outcome"] == "FAIL"
        assert receipt["redacted_fact_class"] == "NATIVE_IDENTITY_INVALID"
        assert not list((evidence / "capability-topology").glob("*.governance.json"))


def test_unavailable_probe_replacement_publishes_fail_before_lane_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: a retained denial authority is replaced and leaves no FAIL receipt."""
    run_id = "31641536482"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        root = Path(raw)
        executable = root / "bwrap"
        executable.write_bytes(b"held authority")
        descriptor = os.open(executable, os.O_RDONLY | os.O_CLOEXEC)
        named = executable.lstat()
        replacement = root / "replacement"
        replacement.write_bytes(b"replacement authority")
        replacement.replace(executable)
        exact_denial = next(iter(topology.NATIVE_DENIAL_STDERR["NATIVE-BWRAP-OS-SANDBOX"]))
        session = topology.NativeProbeSession(
            "NATIVE-BWRAP-OS-SANDBOX", "UNAVAILABLE",
            "RUNNER_POLICY_DISALLOWS_USERNS",
            topology._native_probe_record(
                "NATIVE-BWRAP-OS-SANDBOX", exit_code=1, stderr=exact_denial,
                executable_sha256=hashlib.sha256(b"held authority").hexdigest(),
            ),
            descriptor, executable, topology._artifact_identity(named),
            topology._artifact_identity(os.fstat(descriptor)), None,
        )

        @topology.contextmanager
        def replaced_probe(code: str):
            assert code == session.code
            try:
                yield session
            finally:
                os.close(descriptor)

        evidence = root / "evidence"
        context_path = topology._capture_foundation_context(
            evidence, clock=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc),
        )
        topology.reserve_topology_evidence(
            evidence, run_id=run_id, head_sha=head,
            foundation_context_path=context_path,
        )
        with pytest.raises(topology.TopologyError, match="identity changed"):
            topology.run_lane(
                lane="native-capabilities",
                inventory=Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"),
                evidence_root=evidence, run_id=run_id, head_sha=head,
                foundation_context_path=context_path,
                native_probe_factory=replaced_probe,
            )
        fail_path = evidence / "capability-topology/NATIVE-BWRAP-OS-SANDBOX.json"
        receipt = topology.parse_receipt(fail_path.read_bytes())
        assert receipt["outcome"] == "FAIL"
        assert receipt["redacted_fact_class"] == "NATIVE_IDENTITY_REPLACED"
        assert not list((evidence / "capability-topology").glob("*.governance.json"))


@pytest.mark.parametrize("boundary", ["before", "during", "before-publication"])
def test_retained_native_executable_replacement_fails_at_each_boundary(
    monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    """Break caught: a named executable replacement can survive exact execution."""
    run_id = "31641536482"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        root = Path(raw)
        evidence = root / "evidence"
        context_path = topology._capture_foundation_context(
            evidence, clock=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc),
        )
        topology.reserve_topology_evidence(
            evidence, run_id=run_id, head_sha=head,
            foundation_context_path=context_path,
        )
        _seal_portable_root_baseline(
            monkeypatch, evidence, raw, run_id=run_id, head_sha=head,
            foundation_context_path=context_path,
        )
        baseline = topology.load_portable_root_baseline(
            inventory=Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"),
            evidence_root=evidence, run_id=run_id, head_sha=head,
            foundation_context_path=context_path,
        )
        executable = root / "retained-unshare"
        executable.write_bytes(b"authority one")
        descriptor = os.open(executable, os.O_RDONLY | os.O_CLOEXEC)
        named = executable.lstat()
        session = topology.NativeProbeSession(
            "NATIVE-USERNS-ROOT-PROVISION", "AVAILABLE",
            "NATIVE_CAPABILITY_VALIDATED",
            topology._native_probe_record(
                "NATIVE-USERNS-ROOT-PROVISION", exit_code=0,
                executable_sha256=hashlib.sha256(b"authority one").hexdigest(),
            ),
            descriptor, executable, topology._artifact_identity(named),
            topology._artifact_identity(os.fstat(descriptor)), None,
        )
        expected = topology._expected_rows(
            topology.load_inventory(
                Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv")
            ),
            "NATIVE-USERNS-ROOT-PROVISION",
        )[1]

        def replace() -> None:
            replacement = executable.with_name("replacement")
            replacement.write_bytes(b"authority two")
            replacement.replace(executable)

        if boundary == "before":
            replace()

        def exact(nodes: tuple[str, ...], report: Path) -> tuple[str, ...]:
            result = _passing_exact(nodes, report)
            if boundary == "during":
                replace()
            return result

        original_publish = topology._publish_no_clobber

        def publish(path: Path, content: bytes) -> None:
            if boundary == "before-publication" and path.name == "governance.json":
                replace()
            original_publish(path, content)

        monkeypatch.setattr(topology, "_publish_no_clobber", publish)
        try:
            with pytest.raises(topology.TopologyError, match="native executable identity changed"):
                topology._execute_native_pass_transaction(
                    baseline=baseline, expected=expected,
                    evidence_root=evidence,
                    context=topology.load_foundation_context(
                        context_path, run_id=run_id, head_sha=head,
                    ),
                    code="NATIVE-USERNS-ROOT-PROVISION",
                    session=session, exact_runner=exact,
                )
            marker = evidence / "capability-topology/NATIVE-USERNS-ROOT-PROVISION.json"
            assert topology.parse_receipt(marker.read_bytes())["outcome"] == "FAIL"
            assert not (evidence / "capability-topology/NATIVE-USERNS-ROOT-PROVISION.governance.json").exists()
        finally:
            os.close(descriptor)


@pytest.mark.parametrize(
    "boundary", ["candidate-postcheck", "published-bundle-postcheck", "marker-foreign"],
)
def test_native_pass_transaction_never_deletes_and_publishes_exact_fail_before_marker(
    monkeypatch: pytest.MonkeyPatch, boundary: str,
) -> None:
    """Break caught: pre-marker drift or foreign occupancy triggers destructive rollback."""
    run_id = "31641536482"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        root = Path(raw)
        evidence = root / "evidence"
        context_path = topology._capture_foundation_context(
            evidence, clock=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc),
        )
        topology.reserve_topology_evidence(
            evidence, run_id=run_id, head_sha=head,
            foundation_context_path=context_path,
        )
        _seal_portable_root_baseline(
            monkeypatch, evidence, raw, run_id=run_id, head_sha=head,
            foundation_context_path=context_path,
        )
        active: topology.NativeProbeSession | None = None

        @topology.contextmanager
        def available_probe(code: str):
            nonlocal active
            executable = root / f"{code}.authority"
            executable.write_bytes(b"retained native authority")
            descriptor = os.open(executable, os.O_RDONLY | os.O_CLOEXEC)
            named = executable.lstat()
            session = topology.NativeProbeSession(
                code, "AVAILABLE", "NATIVE_CAPABILITY_VALIDATED",
                topology._native_probe_record(
                    code, exit_code=0,
                    executable_sha256=hashlib.sha256(
                        b"retained native authority"
                    ).hexdigest(),
                ),
                descriptor, executable, topology._artifact_identity(named),
                topology._artifact_identity(os.fstat(descriptor)), None,
            )
            active = session
            try:
                yield session
            finally:
                os.close(descriptor)

        def identity_postcheck(session: topology.NativeProbeSession) -> None:
            assert session.executable_path is not None
            if (
                topology._artifact_identity(session.executable_path.lstat())
                != session.named_identity
                or topology._artifact_identity(os.fstat(session.descriptor))
                != session.descriptor_identity
            ):
                raise topology.TopologyError(
                    "native executable identity changed during execution"
                )

        monkeypatch.setattr(topology, "_postcheck_native_probe", identity_postcheck)

        def replace_active() -> None:
            assert active is not None and active.executable_path is not None
            replacement = active.executable_path.with_name(
                f"{active.executable_path.name}.replacement"
            )
            replacement.write_bytes(b"replacement native authority")
            replacement.replace(active.executable_path)

        if boundary == "candidate-postcheck":
            original_stage = topology._stage_native_candidate

            def stage_then_replace(*args: object, **kwargs: object) -> Path:
                candidate = original_stage(*args, **kwargs)
                replace_active()
                return candidate

            monkeypatch.setattr(
                topology, "_stage_native_candidate", stage_then_replace,
            )
        elif boundary == "published-bundle-postcheck":
            original_bundle = topology._publish_native_candidate_bundle

            def bundle_then_replace(candidate: Path, destination: Path) -> None:
                original_bundle(candidate, destination)
                replace_active()

            monkeypatch.setattr(
                topology, "_publish_native_candidate_bundle", bundle_then_replace,
            )
        else:
            def foreign_marker(path: Path, _content: bytes) -> None:
                path.write_bytes(b"foreign no-clobber evidence")
                path.chmod(0o600)
                raise OSError("simulated foreign marker occupancy")

            monkeypatch.setattr(
                topology, "_publish_native_acceptance_marker", foreign_marker,
            )

        with pytest.raises(topology.TopologyError):
            topology.run_lane(
                lane="native-capabilities",
                inventory=Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"),
                evidence_root=evidence, run_id=run_id, head_sha=head,
                foundation_context_path=context_path, exact_runner=_passing_exact,
                native_probe_factory=available_probe,
            )

        topology_root = evidence / "capability-topology"
        fail_path = topology_root / "NATIVE-BWRAP-OS-SANDBOX.json"
        bundle = topology_root / "NATIVE-BWRAP-OS-SANDBOX.artifacts"
        if boundary == "marker-foreign":
            assert fail_path.read_bytes() == b"foreign no-clobber evidence"
            assert bundle.is_dir()
            assert topology.parse_receipt(
                (bundle / "receipt.json").read_bytes(),
            )["outcome"] == "PASS"
            return
        receipt = topology.parse_receipt(fail_path.read_bytes())
        assert receipt["outcome"] == "FAIL"
        assert receipt["redacted_fact_class"] == "NATIVE_IDENTITY_REPLACED"
        assert receipt["collected_node_ids"] == []
        assert not (topology_root / "NATIVE-BWRAP-OS-SANDBOX.governance.json").exists()
        assert bundle.is_dir()
        if boundary == "candidate-postcheck":
            assert (bundle / "receipt.json").read_bytes() == fail_path.read_bytes()
        else:
            assert topology.parse_receipt(
                (bundle / "receipt.json").read_bytes(),
            )["outcome"] == "PASS"


def test_native_architecture_a_accepts_only_atomic_bundle_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: a flat PASS marker is accepted without a committed bundle manifest."""
    run_id = "31641536482"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    _patch_native_identity_postcheck(monkeypatch)
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        root = Path(raw)
        evidence = root / "evidence"
        context_path = topology._capture_foundation_context(
            evidence, clock=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc),
        )
        topology.reserve_topology_evidence(
            evidence, run_id=run_id, head_sha=head,
            foundation_context_path=context_path,
        )
        _seal_portable_root_baseline(
            monkeypatch, evidence, raw, run_id=run_id, head_sha=head,
            foundation_context_path=context_path,
        )

        paths = topology.run_lane(
            lane="native-capabilities",
            inventory=Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"),
            evidence_root=evidence, run_id=run_id, head_sha=head,
            foundation_context_path=context_path, exact_runner=_passing_exact,
            native_probe_factory=_available_native_probe_factory(root),
        )

        assert len(paths) == 2
        topology_root = evidence / "capability-topology"
        for marker in paths:
            code = marker.stem
            bundle = topology_root / f"{code}.artifacts"
            assert bundle.is_dir()
            assert stat.S_IMODE(bundle.stat().st_mode) == 0o700
            assert {entry.name for entry in bundle.iterdir()} == {
                "receipt.json", "governance.json", "manifest.json",
            }
            receipt_raw = (bundle / "receipt.json").read_bytes()
            assert marker.read_bytes() == receipt_raw
            assert not (topology_root / f"{code}.governance.json").exists()
            manifest_raw = (bundle / "manifest.json").read_bytes()
            manifest = json.loads(manifest_raw)
            assert set(manifest) == {
                "schema_version", "capability_or_authority_code",
                "foundation_run_id", "foundation_head_sha",
                "foundation_validation_date", "foundation_context_sha256",
                "inventory_sha256", "receipt_filename",
                "receipt_bytes_sha256", "receipt_self_sha256",
                "governance_filename", "governance_present",
                "governance_sha256", "expected_node_ids",
                "expected_node_ids_sha256", "expected_node_count",
                "selected_test_count", "probe", "outcome",
                "manifest_sha256",
            }
            assert manifest["schema_version"] == "t-g03a-native-artifact-manifest/v1"
            assert manifest["capability_or_authority_code"] == code
            assert manifest["receipt_bytes_sha256"] == hashlib.sha256(
                receipt_raw,
            ).hexdigest()
            assert manifest["governance_present"] is True
            assert manifest["governance_sha256"] == hashlib.sha256(
                (bundle / "governance.json").read_bytes(),
            ).hexdigest()


def test_native_architecture_a_resolves_successful_marker_exception_by_exact_reread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: an exception after marker creation leaves an ambiguous accepted PASS."""
    run_id = "31641536482"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    _patch_native_identity_postcheck(monkeypatch)
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        root = Path(raw)
        evidence = root / "evidence"
        context_path = topology._capture_foundation_context(
            evidence, clock=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc),
        )
        topology.reserve_topology_evidence(
            evidence, run_id=run_id, head_sha=head,
            foundation_context_path=context_path,
        )
        _seal_portable_root_baseline(
            monkeypatch, evidence, raw, run_id=run_id, head_sha=head,
            foundation_context_path=context_path,
        )
        calls = 0

        def publish_then_raise(marker: Path, raw_bytes: bytes) -> None:
            nonlocal calls
            calls += 1
            topology._publish_no_clobber(marker, raw_bytes)
            raise OSError("simulated response loss after marker write")

        monkeypatch.setattr(
            topology, "_publish_native_acceptance_marker",
            publish_then_raise, raising=False,
        )
        paths = topology.run_lane(
            lane="native-capabilities",
            inventory=Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"),
            evidence_root=evidence, run_id=run_id, head_sha=head,
            foundation_context_path=context_path, exact_runner=_passing_exact,
            native_probe_factory=_available_native_probe_factory(root),
        )

        assert calls == 2
        assert len(paths) == 2
        for marker in paths:
            assert marker.read_bytes() == (
                marker.with_suffix(".artifacts") / "receipt.json"
            ).read_bytes()


def test_native_bundle_rename_failure_and_foreign_occupancy_never_delete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    topology_root = tmp_path / "capability-topology"
    topology_root.mkdir(mode=0o700)
    receipt = _native_receipt()
    candidate = topology._stage_native_candidate(topology_root, receipt, None)
    destination = topology_root / "NATIVE-USERNS-ROOT-PROVISION.artifacts"

    monkeypatch.setattr(
        topology, "_renameat2_noreplace",
        lambda *_args: (_ for _ in ()).throw(OSError("rename response lost")),
    )
    with pytest.raises(topology.TopologyError, match="exact resolved rename"):
        topology._publish_native_candidate_bundle(candidate, destination)
    assert candidate.is_dir()
    assert not destination.exists()

    monkeypatch.undo()
    destination.mkdir(mode=0o700)
    sentinel = destination / "foreign"
    sentinel.write_bytes(b"foreign evidence")
    with pytest.raises(topology.TopologyError, match="exact resolved rename"):
        topology._publish_native_candidate_bundle(candidate, destination)
    assert candidate.is_dir()
    assert sentinel.read_bytes() == b"foreign evidence"


def test_native_bundle_rename_ambiguity_accepts_only_the_same_inode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    topology_root = tmp_path / "capability-topology"
    topology_root.mkdir(mode=0o700)
    candidate = topology._stage_native_candidate(
        topology_root, _native_receipt(), None,
    )
    destination = topology_root / "NATIVE-USERNS-ROOT-PROVISION.artifacts"
    original = topology._renameat2_noreplace

    def rename_then_raise(*args: object) -> None:
        original(*args)
        raise OSError("simulated response loss after rename")

    monkeypatch.setattr(topology, "_renameat2_noreplace", rename_then_raise)
    topology._publish_native_candidate_bundle(candidate, destination)
    assert destination.is_dir()
    assert not candidate.exists()
    assert (destination / "receipt.json").is_file()


def test_native_manifest_tamper_bundle_symlink_and_leaf_replacement_are_rejected(
    tmp_path: Path,
) -> None:
    rows = topology.load_inventory(Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"))
    context = _native_context()
    topology_root = tmp_path / "capability-topology"
    marker = _write_native_artifact_fixture(topology_root, _native_receipt())
    bundle = marker.with_suffix(".artifacts")
    manifest = bundle / "manifest.json"
    original_manifest = manifest.read_bytes()
    tampered = json.loads(original_manifest)
    tampered["expected_node_count"] += 1
    manifest.write_bytes(topology.canonical_json_bytes(tampered))
    manifest.chmod(0o600)
    with pytest.raises(topology.TopologyError, match="manifest"):
        topology.validate_native_artifact_set(
            marker, rows=rows, foundation_context=context, sealed_custody={},
        )
    manifest.write_bytes(original_manifest)
    manifest.chmod(0o600)

    with topology._retained_private_native_artifacts(marker) as artifacts:
        replacement_manifest = tmp_path / "replacement-manifest"
        replacement_manifest.write_bytes(original_manifest)
        replacement_manifest.chmod(0o600)
        replacement_manifest.replace(manifest)
        with pytest.raises(topology.TopologyError, match="identity changed"):
            topology._postcheck_private_native_artifacts(artifacts)

    replacement_bundle = topology_root / "replacement-bundle"
    replacement_bundle.mkdir(mode=0o700)
    bundle.replace(replacement_bundle)
    bundle.symlink_to(replacement_bundle, target_is_directory=True)
    with pytest.raises(topology.TopologyError, match="bundle is unsafe"):
        topology.validate_native_artifact_set(
            marker, rows=rows, foundation_context=context, sealed_custody={},
        )


def test_native_random_candidate_is_inert_and_transaction_never_unlinks_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    rows = topology.load_inventory(Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"))
    context = _native_context()
    topology_root = tmp_path / "capability-topology"
    candidate = topology._stage_native_candidate(
        topology_root, _native_receipt(), None,
    )
    assert candidate.is_dir()
    with pytest.raises(topology.TopologyError):
        topology.validate_native_artifact_set(
            topology_root / "NATIVE-USERNS-ROOT-PROVISION.json",
            rows=rows, foundation_context=context, sealed_custody={},
        )
    marker = _write_native_artifact_fixture(
        topology_root, _native_receipt(),
    )
    assert topology.validate_native_artifact_set(
        marker, rows=rows, foundation_context=context, sealed_custody={},
    )[0]["outcome"] == "DEFERRED"
    assert candidate.is_dir()


def test_native_exact_nonpass_uses_only_inert_append_only_diagnostic_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: native non-PASS enters generic staging cleanup and pathname unlink."""
    run_id = "31641536482"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    _patch_native_identity_postcheck(monkeypatch)
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        root = Path(raw)
        evidence = root / "evidence"
        context_path = topology._capture_foundation_context(
            evidence, clock=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc),
        )
        topology.reserve_topology_evidence(
            evidence, run_id=run_id, head_sha=head,
            foundation_context_path=context_path,
        )
        _seal_portable_root_baseline(
            monkeypatch, evidence, raw, run_id=run_id, head_sha=head,
            foundation_context_path=context_path,
        )

        def nonpassing_exact(nodes: tuple[str, ...], report: Path) -> tuple[str, ...]:
            topology._publish_no_clobber(report, json.dumps({
                "schema_version": 1,
                "component": "root",
                "pytest_exit_status": 1,
                "custody_policy": json.loads(
                    os.environ["TEST_GOVERNANCE_CUSTODY_POLICY"],
                ),
                "tests": [{
                    "test_node_id": node,
                    "component": "root",
                    "outcome": "failed" if index == 0 else "passed",
                    "reason": "assertion failed" if index == 0 else "",
                    "phase": "call",
                } for index, node in enumerate(nodes)],
            }, sort_keys=True).encode("utf-8"))
            return nodes

        generic_diagnostic_calls: list[Path] = []
        generic_publisher = topology._publish_failure_diagnostic

        def track_generic_diagnostic(path: Path, content: bytes) -> None:
            generic_diagnostic_calls.append(path)
            generic_publisher(path, content)

        monkeypatch.setattr(
            topology, "_publish_failure_diagnostic", track_generic_diagnostic,
        )
        native_unlink_calls: list[Path] = []
        real_unlink = Path.unlink
        native_call_active = False

        def forbid_native_unlink(path: Path, *args: object, **kwargs: object) -> None:
            if native_call_active:
                native_unlink_calls.append(path)
                raise AssertionError("native Architecture A forbids pathname unlink")
            real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", forbid_native_unlink)
        native_call_active = True
        try:
            with pytest.raises(topology.TopologyError) as caught:
                topology.run_lane(
                    lane="native-capabilities",
                    inventory=Path(
                        "tests/fixtures/t-g03a-hosted-failure-inventory.tsv",
                    ),
                    evidence_root=evidence, run_id=run_id, head_sha=head,
                    foundation_context_path=context_path,
                    exact_runner=nonpassing_exact,
                    native_probe_factory=_available_native_probe_factory(root),
                )
        finally:
            native_call_active = False

        assert str(caught.value) == "EXACT_EXECUTION_NONPASS"
        assert generic_diagnostic_calls == []
        assert native_unlink_calls == []
        topology_root = evidence / "capability-topology"
        marker = topology_root / "NATIVE-BWRAP-OS-SANDBOX.json"
        receipt = topology.parse_receipt(marker.read_bytes())
        assert receipt["outcome"] == "FAIL"
        bundle = marker.with_suffix(".artifacts")
        assert topology.parse_receipt(
            (bundle / "receipt.json").read_bytes(),
        )["outcome"] == "FAIL"
        assert not (bundle / "governance.json").exists()

        execution_roots = list(topology_root.glob(".native-execution-*"))
        assert len(execution_roots) == 1
        execution_root = execution_roots[0]
        assert stat.S_IMODE(execution_root.stat().st_mode) == 0o700
        provisional = execution_root / ".governance.json.executing"
        diagnostic = execution_root / "portable-root-remainder.failure-diagnostic.json"
        assert stat.S_IMODE(provisional.stat().st_mode) == 0o600
        assert stat.S_IMODE(diagnostic.stat().st_mode) == 0o600
        assert topology.parse_failure_diagnostic(
            topology._read_private_regular_file(
                diagnostic, label="inert native failure diagnostic",
            ),
        )["diagnostic_only"] is True
        assert not (
            topology_root / "portable-root-remainder.failure-diagnostic.json"
        ).exists()


def test_native_artifact_reader_requires_private_receipt_and_complete_pass_governance(
    tmp_path: Path,
) -> None:
    """Break caught: symlinked receipt or a forged PASS without exact governance is aggregated."""
    rows = topology.load_inventory(Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"))
    context = _native_context()
    topology_root = tmp_path / "capability-topology"
    topology_root.mkdir(mode=0o700)
    deferred = _write_native_artifact_fixture(topology_root, _native_receipt())

    receipt, executed = topology.validate_native_artifact_set(
        deferred, rows=rows, foundation_context=context, sealed_custody={},
    )
    assert receipt["outcome"] == "DEFERRED"
    assert executed == ()

    alias = topology_root / "NATIVE-BWRAP-OS-SANDBOX.json"
    alias.symlink_to(deferred)
    with pytest.raises(topology.TopologyError, match="private regular"):
        topology.validate_native_artifact_set(
            alias, rows=rows, foundation_context=context, sealed_custody={},
        )

    expected = topology._expected_rows(rows, "NATIVE-BWRAP-OS-SANDBOX")[1]
    forged_pass = _native_receipt(
        capability_or_authority_code="NATIVE-BWRAP-OS-SANDBOX",
        collected_node_ids=list(expected), preflight_state="AVAILABLE",
        redacted_fact_class="NATIVE_CAPABILITY_VALIDATED",
        probe={
            "command_id": "BWRAP_USER_PID_NET_ISOLATION_V1", "exit_code": 0,
            "stdout_sha256": hashlib.sha256(b"").hexdigest(),
            "stderr_sha256": hashlib.sha256(b"").hexdigest(),
            "executable_sha256": "9" * 64,
        },
        selected_test_count=16, passed=16, failed=0, unavailable=0, outcome="PASS",
    )
    nonzero_probe = dict(forged_pass["probe"])
    nonzero_probe["exit_code"] = 1
    forged_nonzero = _native_receipt(
        capability_or_authority_code="NATIVE-BWRAP-OS-SANDBOX",
        collected_node_ids=list(expected), preflight_state="AVAILABLE",
        redacted_fact_class="NATIVE_CAPABILITY_VALIDATED", probe=nonzero_probe,
        selected_test_count=16, passed=16, failed=0, unavailable=0, outcome="PASS",
    )
    with pytest.raises(topology.TopologyError, match="exact probe"):
        topology.validate_receipt(
            topology.canonical_json_bytes(forged_nonzero), rows=rows,
            foundation_run_id=str(context["foundation_run_id"]),
            foundation_head_sha=str(context["foundation_head_sha"]),
            foundation_context=context,
        )
    alias.unlink()
    alias = _write_native_artifact_fixture(topology_root, forged_pass)
    with pytest.raises(topology.TopologyError, match="governance"):
        topology.validate_native_artifact_set(
            alias, rows=rows, foundation_context=context, sealed_custody={},
        )


def test_native_artifact_reader_rejects_modes_governance_links_and_replacement(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Break caught: private paired artifacts can change parent or leaf during validation."""
    rows = topology.load_inventory(Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"))
    context = _native_context()
    topology_root = tmp_path / "capability-topology"
    topology_root.mkdir(mode=0o700)
    receipt_path = _write_native_artifact_fixture(topology_root, _native_receipt())
    receipt_path.chmod(0o644)
    with pytest.raises(topology.TopologyError, match="private regular"):
        topology.validate_native_artifact_set(
            receipt_path, rows=rows, foundation_context=context, sealed_custody={},
        )
    receipt_path.chmod(0o600)

    governance = receipt_path.with_suffix(".governance.json")
    target = tmp_path / "governance-target"
    target.write_bytes(b"{}")
    governance.symlink_to(target)
    with pytest.raises(topology.TopologyError, match="legacy flat"):
        topology.validate_native_artifact_set(
            receipt_path, rows=rows, foundation_context=context, sealed_custody={},
        )
    governance.unlink()

    with topology._retained_private_native_artifacts(receipt_path) as artifacts:
        replacement = topology_root.with_name("replacement-topology")
        topology_root.replace(replacement)
        topology_root.mkdir(mode=0o700)
        with pytest.raises(topology.TopologyError, match="identity changed"):
            topology._postcheck_private_native_artifacts(artifacts)
    replacement.replace(topology_root)

    with topology._retained_private_native_artifacts(receipt_path) as artifacts:
        replacement_receipt = tmp_path / "replacement-receipt"
        replacement_receipt.write_bytes(receipt_path.read_bytes())
        replacement_receipt.chmod(0o600)
        replacement_receipt.replace(receipt_path)
        with pytest.raises(topology.TopologyError, match="identity changed"):
            topology._postcheck_private_native_artifacts(artifacts)

    receipt_path.chmod(0o600)
    current_gid = os.getegid()
    monkeypatch.setattr(topology.os, "getegid", lambda: current_gid + 1)
    with pytest.raises(topology.TopologyError, match="unsafe"):
        topology.validate_native_artifact_set(
            receipt_path, rows=rows, foundation_context=context, sealed_custody={},
        )


def test_native_publication_is_no_clobber_for_receipt_and_governance(tmp_path: Path) -> None:
    """Break caught: retry overwrites accepted native receipt or governance evidence."""
    receipt = _native_receipt()
    first = topology.publish_receipt(receipt, tmp_path)
    original = first.read_bytes()
    with pytest.raises(FileExistsError):
        topology.publish_receipt(receipt, tmp_path)
    assert first.read_bytes() == original

    governance = first.with_suffix(".governance.json")
    topology._publish_no_clobber(governance, b"first")
    with pytest.raises(FileExistsError):
        topology._publish_no_clobber(governance, b"second")
    assert governance.read_bytes() == b"first"


def test_receipt_parser_accepts_only_canonical_bytes_and_a_matching_self_hash() -> None:
    """Break caught: whitespace, reordered keys, or a forged payload digest becomes accepted."""
    receipt = _receipt()
    raw = topology.canonical_json_bytes(receipt)

    assert topology.parse_receipt(raw) == receipt

    with pytest.raises(topology.TopologyError, match="canonical"):
        topology.parse_receipt(raw + b"\n")
    receipt["outcome"] = "FAIL"
    with pytest.raises(topology.TopologyError, match="self-hash"):
        topology.parse_receipt(topology.canonical_json_bytes(receipt))


def test_locked_inventory_installs_exact_bytes_once_and_rejects_tampering(tmp_path: Path) -> None:
    """Break caught: an inventory or installed-evidence mapping can silently drift."""
    tracked = Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv")
    rows = topology.load_inventory(tracked)
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        installed = topology.install_inventory(tracked, evidence)
        assert installed.read_bytes() == tracked.read_bytes()
        with pytest.raises(FileExistsError):
            topology.install_inventory(tracked, evidence)
    assert len(rows) == 30
    changed = tmp_path / "changed.tsv"
    changed.write_bytes(tracked.read_bytes().replace(b"NATIVE_CAPABILITY_REQUIRED", b"NATIVE_CAPABILITY_REQUIREX", 1))
    with pytest.raises(topology.TopologyError, match="hash drift"):
        topology.load_inventory(changed)


def test_aggregate_rejects_partial_and_execution_bearing_deferred_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: partial or execution-bearing deferred evidence makes CI green."""
    rows = topology.load_inventory(Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"))
    run, head = "31641536482", "18f22198c65c7bc735aeb848d8fda55209d01e78"
    paths: list[Path] = []
    for code in topology.CODE_CLASSIFICATION:
        lane, node_ids = topology._expected_rows(rows, code)
        state, outcome = {"portable-source": ("AVAILABLE", "PASS"), "native-capabilities": ("UNAVAILABLE", "DEFERRED"), "external-authorities": ("ABSENT", "DEFERRED")}[lane]
        receipt = (
            _native_receipt(capability_or_authority_code=code)
            if lane == "native-capabilities"
            else _receipt(foundation_run_id=run, foundation_head_sha=head, lane=lane, capability_or_authority_code=code, expected_node_ids=list(node_ids), collected_node_ids=list(node_ids) if outcome == "PASS" else [], preflight_state=state, outcome=outcome)
        )
        path = tmp_path / f"{code}.json"
        if lane == "native-capabilities":
            path = _write_native_artifact_fixture(tmp_path, receipt)
        else:
            path.write_bytes(topology.canonical_json_bytes(receipt))
            path.chmod(0o600)
        paths.append(path)
    monkeypatch.setattr(topology, "validate_portable_closure_proof", lambda *_args, **_kwargs: {})
    summary = topology.aggregate_receipts(
        paths, rows=rows, foundation_run_id=run, foundation_head_sha=head,
        foundation_context=_native_context(), closure_proof_path=tmp_path / "closure-proof.json",
        sealed_custody={},
    )
    assert summary["runtime_proof"] == "COMPLETE_WITH_DEFERRED_RUNTIME_CHECKS"
    forged = _receipt(foundation_run_id=run, foundation_head_sha=head, lane="external-authorities", capability_or_authority_code="EXT-PHASE3B-CORPUS", expected_node_ids=list(topology._expected_rows(rows, "EXT-PHASE3B-CORPUS")[1]), collected_node_ids=["tests/control_api/test_phase3b_backfill.py::test_real_backfill_plan_has_only_approved_evidence"], preflight_state="ABSENT", outcome="DEFERRED")
    with pytest.raises(topology.TopologyError, match="DEFERRED receipt selected"):
        topology.validate_receipt(topology.canonical_json_bytes(forged), rows=rows, foundation_run_id=run, foundation_head_sha=head)


def test_aggregate_rejects_renamed_native_pass_without_governance_and_keeps_external_v1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: renaming native v2 bypasses its paired governance reader."""
    rows = topology.load_inventory(Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"))
    context = _native_context()
    run = str(context["foundation_run_id"])
    head = str(context["foundation_head_sha"])
    paths: list[Path] = []
    for code in sorted(topology.CODE_CLASSIFICATION):
        lane, expected = topology._expected_rows(rows, code)
        if code == "NATIVE-BWRAP-OS-SANDBOX":
            receipt = _native_receipt(
                capability_or_authority_code=code,
                collected_node_ids=list(expected), preflight_state="AVAILABLE",
                redacted_fact_class="NATIVE_CAPABILITY_VALIDATED",
                probe={
                    "command_id": "BWRAP_USER_PID_NET_ISOLATION_V1", "exit_code": 0,
                    "stdout_sha256": hashlib.sha256(b"").hexdigest(),
                    "stderr_sha256": hashlib.sha256(b"").hexdigest(),
                    "executable_sha256": "9" * 64,
                },
                selected_test_count=16, passed=16, failed=0, unavailable=0,
                outcome="PASS",
            )
            path = tmp_path / "renamed-native-pass.json"
        elif lane == "native-capabilities":
            receipt = _native_receipt(capability_or_authority_code=code)
            path = tmp_path / f"{code}.json"
        else:
            receipt = _receipt(
                foundation_run_id=run, foundation_head_sha=head, lane=lane,
                capability_or_authority_code=code, expected_node_ids=list(expected),
                collected_node_ids=[], preflight_state="ABSENT",
                redacted_fact_class="AUTHORITY_ROOT_ABSENT", outcome="DEFERRED",
            )
            path = tmp_path / f"{code}.json"
        if lane == "native-capabilities" and path.name != "renamed-native-pass.json":
            path = _write_native_artifact_fixture(tmp_path, receipt)
        else:
            path.write_bytes(topology.canonical_json_bytes(receipt))
            path.chmod(0o600)
        paths.append(path)
    monkeypatch.setattr(topology, "validate_portable_closure_proof", lambda *_a, **_k: {})

    with pytest.raises(topology.TopologyError, match="canonical receipt filename set"):
        topology.aggregate_receipts(
            paths, rows=rows, foundation_run_id=run, foundation_head_sha=head,
            foundation_context=context, closure_proof_path=tmp_path / "closure-proof.json",
            sealed_custody={},
        )

    renamed = tmp_path / "renamed-native-pass.json"
    canonical = tmp_path / "NATIVE-BWRAP-OS-SANDBOX.json"
    canonical = _write_native_artifact_fixture(
        tmp_path,
        _native_receipt(capability_or_authority_code="NATIVE-BWRAP-OS-SANDBOX"),
    )
    canonical_paths = [canonical if path == renamed else path for path in paths]
    assert topology.aggregate_receipts(
        list(reversed(canonical_paths)), rows=rows,
        foundation_run_id=run, foundation_head_sha=head,
        foundation_context=context, closure_proof_path=tmp_path / "closure-proof.json",
        sealed_custody={},
    )["runtime_proof"] == "COMPLETE_WITH_DEFERRED_RUNTIME_CHECKS"
    with pytest.raises(topology.TopologyError, match="canonical receipt filename set"):
        topology.aggregate_receipts(
            [*canonical_paths, renamed], rows=rows,
            foundation_run_id=run, foundation_head_sha=head,
            foundation_context=context,
            closure_proof_path=tmp_path / "closure-proof.json",
            sealed_custody={},
        )


def test_receipt_rejects_a_json_number_even_when_its_text_is_a_valid_run_id() -> None:
    """Break caught: alternate JSON types bypass the v1 string-only canonical protocol."""
    receipt = _receipt(foundation_run_id=31641536482)
    with pytest.raises(topology.TopologyError, match="foundation run"):
        topology.parse_receipt(topology.canonical_json_bytes(receipt))


def test_receipt_rejects_unredacted_fact_payload_and_stale_or_wrong_mapping() -> None:
    """Break caught: redacted receipt fields carry details or stale/mapped evidence passes."""
    rows = topology.load_inventory(Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"))
    run, head = "31641536482", "18f22198c65c7bc735aeb848d8fda55209d01e78"
    code = "NATIVE-USERNS-ROOT-PROVISION"
    _, expected = topology._expected_rows(rows, code)
    receipt = _receipt(foundation_run_id=run, foundation_head_sha=head, lane="native-capabilities", capability_or_authority_code=code, expected_node_ids=list(expected), collected_node_ids=[], preflight_state="UNAVAILABLE", outcome="DEFERRED", redacted_fact_class="/home/operator/secret")
    with pytest.raises(topology.TopologyError, match="redacted"):
        topology.parse_receipt(topology.canonical_json_bytes(receipt))
    receipt = _receipt(foundation_run_id="31641536481", foundation_head_sha=head, lane="native-capabilities", capability_or_authority_code=code, expected_node_ids=list(expected), collected_node_ids=[], preflight_state="UNAVAILABLE", outcome="DEFERRED")
    with pytest.raises(topology.TopologyError, match="stale"):
        topology.validate_receipt(topology.canonical_json_bytes(receipt), rows=rows, foundation_run_id=run, foundation_head_sha=head)
    receipt = _receipt(foundation_run_id=run, foundation_head_sha=head, lane="external-authorities", capability_or_authority_code=code, expected_node_ids=list(expected), collected_node_ids=[], preflight_state="ABSENT", outcome="DEFERRED")
    with pytest.raises(topology.TopologyError, match="mapping"):
        topology.validate_receipt(topology.canonical_json_bytes(receipt), rows=rows, foundation_run_id=run, foundation_head_sha=head)


def test_publish_is_no_clobber_and_fake_path_cannot_supply_userns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Break caught: evidence is overwritten or a PATH fake claims native capability."""
    receipt = _receipt()
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        topology.publish_receipt(receipt, evidence)
        with pytest.raises(FileExistsError):
            topology.publish_receipt(receipt, evidence)
    fake_dir = tmp_path / "fake-bin"
    fake_dir.mkdir()
    fake = fake_dir / "unshare"
    fake.write_text("#!/bin/sh\nprintf x > \"$TG03C_FAKE_MARKER\"\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    marker = tmp_path / "fake-was-run"
    monkeypatch.setenv("PATH", str(fake_dir))
    monkeypatch.setenv("TG03C_FAKE_MARKER", str(marker))
    state, _ = topology._native_preflight("NATIVE-USERNS-ROOT-PROVISION")
    assert state in {"AVAILABLE", "UNAVAILABLE", "BROKEN"}
    assert not marker.exists()


def test_external_preflight_distinguishes_absent_partial_and_invalid(tmp_path: Path) -> None:
    """Break caught: a dangling or partial authority is deferred as absent."""
    absent = tmp_path / "absent"
    assert topology._external_preflight("EXT-PHASE3B-CORPUS", corpus_root=absent)[0] == "ABSENT"
    dangling = tmp_path / "dangling"
    dangling.symlink_to(absent)
    assert topology._external_preflight("EXT-PHASE3B-CORPUS", corpus_root=dangling)[0] == "INVALID"
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        partial = Path(raw)
        partial.chmod(0o700)
        assert topology._external_preflight("EXT-PHASE3B-CORPUS", corpus_root=partial)[0] == "PARTIAL"


def test_foundation_context_requires_current_github_run_and_checked_out_head(monkeypatch: pytest.MonkeyPatch) -> None:
    """Break caught: local defaults or another run/head can mint a receipt."""
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    with pytest.raises(topology.TopologyError, match="GitHub run"):
        topology.require_foundation_context("31641536482", "18f22198c65c7bc735aeb848d8fda55209d01e78")


def test_foundation_context_diagnostic_probe_requires_full_v1_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: a diagnostic labels a merely present or forged context as validated."""
    run_id = "31641536482"
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    monkeypatch.delenv("FOUNDATION_VALIDATION_DATE", raising=False)
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        root = Path(raw)
        valid_path = topology._capture_foundation_context(
            root / "valid",
            clock=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc),
        )
        malformed_path = root / "malformed.json"
        malformed_path.write_bytes(b"{}")
        forged_path = root / "forged.json"
        forged = topology.json.loads(valid_path.read_text(encoding="utf-8"))
        forged["foundation_validation_date"] = "2026-08-14"
        forged_path.write_bytes(topology.canonical_json_bytes(forged))

        assert topology._foundation_context_is_valid_for_diagnostics(
            valid_path, run_id=run_id, head_sha=head_sha,
        ) is True
        assert topology._foundation_context_is_valid_for_diagnostics(
            malformed_path, run_id=run_id, head_sha=head_sha,
        ) is False
        assert topology._foundation_context_is_valid_for_diagnostics(
            forged_path, run_id=run_id, head_sha=head_sha,
        ) is False
        monkeypatch.setenv("FOUNDATION_VALIDATION_DATE", "P0_02_OVERRIDE_SENTINEL")
        assert topology._foundation_context_is_valid_for_diagnostics(
            valid_path, run_id=run_id, head_sha=head_sha,
        ) is True


def test_exact_collection_rejects_xpass_observation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Break caught: a non-strict xfail XPASS becomes a portable PASS."""
    node = "tests/example.py::test_exact"
    report = tmp_path / "governance.json"

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        report.write_text('{"tests":[{"test_node_id":"tests/example.py::test_exact","outcome":"passed","wasxfail":true}]}', encoding="utf-8")
        return subprocess.CompletedProcess([], 0)

    monkeypatch.setattr(topology.subprocess, "run", fake_run)
    with pytest.raises(topology.TopologyError, match="xfail"):
        topology._run_exact((node,), report)


def test_governance_plugin_labels_xfail_and_xpass_for_exact_lane_rejection(tmp_path: Path) -> None:
    """Break caught: the reporter collapses marker outcomes into ordinary pass/skip."""
    reporter = governance_plugin._GovernanceReporter("root", tmp_path / "report.json", tmp_path, (), ("test_*.py",))
    for node, passed, skipped in (("tests/xpass.py::test_case", True, False), ("tests/xfail.py::test_case", False, True)):
        reporter.pytest_runtest_logreport(type("Report", (), {"wasxfail": "expected", "passed": passed, "skipped": skipped, "failed": False, "nodeid": node, "when": "call"})())
    assert reporter.records["tests/xpass.py::test_case"]["outcome"] == "xpassed"
    assert reporter.records["tests/xfail.py::test_case"]["outcome"] == "xfailed"


def test_aggregate_rejects_duplicate_missing_and_unlisted_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: a code receipt can be duplicated, omitted, or replaced by an unlisted node."""
    rows = topology.load_inventory(Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"))
    run, head = "31641536482", "18f22198c65c7bc735aeb848d8fda55209d01e78"
    paths: list[Path] = []
    for code in topology.CODE_CLASSIFICATION:
        lane, expected = topology._expected_rows(rows, code)
        state, outcome = {"portable-source": ("AVAILABLE", "PASS"), "native-capabilities": ("UNAVAILABLE", "DEFERRED"), "external-authorities": ("ABSENT", "DEFERRED")}[lane]
        receipt = (
            _native_receipt(capability_or_authority_code=code)
            if lane == "native-capabilities"
            else _receipt(foundation_run_id=run, foundation_head_sha=head, lane=lane, capability_or_authority_code=code, expected_node_ids=list(expected), collected_node_ids=list(expected) if outcome == "PASS" else [], preflight_state=state, outcome=outcome)
        )
        path = tmp_path / f"{code}.json"
        path.write_bytes(topology.canonical_json_bytes(receipt))
        path.chmod(0o600)
        paths.append(path)
    monkeypatch.setattr(topology, "validate_portable_closure_proof", lambda *_args, **_kwargs: {})
    with pytest.raises(topology.TopologyError, match="receipt set"):
        topology.aggregate_receipts(
            paths[:-1] + [paths[0]], rows=rows, foundation_run_id=run,
            foundation_head_sha=head, foundation_context=_native_context(),
            closure_proof_path=tmp_path / "closure-proof.json", sealed_custody={},
        )
    altered = _receipt(foundation_run_id=run, foundation_head_sha=head, lane="native-capabilities", capability_or_authority_code="NATIVE-USERNS-ROOT-PROVISION", expected_node_ids=["tests/not-inventory.py::test_hidden"], collected_node_ids=[], preflight_state="UNAVAILABLE", outcome="DEFERRED")
    with pytest.raises(topology.TopologyError, match="mapping"):
        topology.validate_receipt(topology.canonical_json_bytes(altered), rows=rows, foundation_run_id=run, foundation_head_sha=head)


def test_fake_or_invalid_trusted_userns_binary_is_broken(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Break caught: a fake fixed-path userns executable receives AVAILABLE."""
    fake = tmp_path / "unshare"
    fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setattr(topology, "TRUSTED_UNSHARE", fake)
    assert topology._native_preflight("NATIVE-USERNS-ROOT-PROVISION")[0] == "BROKEN"


def test_receipt_rejects_stale_head_and_forbidden_native_state() -> None:
    """Break caught: a receipt from another head or BROKEN native state is accepted."""
    rows = topology.load_inventory(Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"))
    run, head = "31641536482", "18f22198c65c7bc735aeb848d8fda55209d01e78"
    code = "NATIVE-USERNS-ROOT-PROVISION"
    lane, expected = topology._expected_rows(rows, code)
    stale = _receipt(foundation_run_id=run, foundation_head_sha="0" * 40, lane=lane, capability_or_authority_code=code, expected_node_ids=list(expected), collected_node_ids=[] ,preflight_state="UNAVAILABLE", outcome="DEFERRED")
    with pytest.raises(topology.TopologyError, match="stale"):
        topology.validate_receipt(topology.canonical_json_bytes(stale), rows=rows, foundation_run_id=run, foundation_head_sha=head)
    forbidden = _native_receipt(preflight_state="BROKEN", outcome="DEFERRED", redacted_fact_class="NATIVE_PROBE_INVALID")
    with pytest.raises(topology.TopologyError, match="DEFERRED"):
        topology.validate_receipt(topology.canonical_json_bytes(forbidden), rows=rows, foundation_run_id=run, foundation_head_sha=head, foundation_context=_native_context())


@pytest.mark.parametrize("outcome", ("skipped", "deselected", "xfailed", "xpassed"))
def test_exact_collection_rejects_nonexecuted_or_xfail_observations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str) -> None:
    """Break caught: a skip, deselection, xfail, or XPASS is treated as execution."""
    node = "tests/example.py::test_exact"
    report = tmp_path / f"{outcome}.json"

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        report.write_text('{"tests":[{"test_node_id":"tests/example.py::test_exact","outcome":"' + outcome + '"}]}', encoding="utf-8")
        return subprocess.CompletedProcess([], 0)

    monkeypatch.setattr(topology.subprocess, "run", fake_run)
    with pytest.raises(topology.TopologyError):
        topology._run_exact((node,), report)


def _write_direct(path: Path, content: str = "fixture\n", *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755 if executable else 0o600)


def _complete_corpus_fixture(root: Path) -> None:
    root.mkdir(mode=0o700)
    for relative, directory in topology.PHASE3B_REQUIRED_ENTRIES:
        path = root / relative
        if directory:
            path.mkdir(parents=True, mode=0o700)
            path.chmod(0o700)
        else:
            _write_direct(path)


def _valid_phase3b_analysis() -> SimpleNamespace:
    return SimpleNamespace(
        inventory_hash="dbc94142b6773bb5a79c7bc889e7323ca92c03e5375d0a596b679c3f01c7b4ce",
        decision_total=16517, cost_sessions=20, asset_count=17, asset_source_files=2209,
    )


def test_external_authority_inventory_classifies_missing_uv_with_closure_and_symlinked_corpus_child(tmp_path: Path) -> None:
    """Break caught: partial authority is deferred or a symlinked required child is trusted."""
    del tmp_path
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        root = Path(raw)
        root.chmod(0o700)
        legacy = root / "legacy"
        for relative, _ in topology.LEGACY_CLOSURE_ENTRIES:
            _write_direct(legacy / relative, executable=relative.endswith("python"))
        assert topology._external_preflight("EXT-LEGACY-UV-AUTHORITY", uv_path=root / "missing-uv", legacy_root=legacy)[0] == "PARTIAL"
        corpus = root / "corpus"
        _complete_corpus_fixture(corpus)
        child = corpus / "memory/decisions.jsonl"
        child.unlink()
        child.symlink_to(corpus / "asset_registry.py")
        assert topology._external_preflight("EXT-PHASE3B-CORPUS", corpus_root=corpus, corpus_validator=lambda _root: _valid_phase3b_analysis())[0] == "INVALID"


def test_fully_valid_external_fixtures_reach_valid_without_network(tmp_path: Path) -> None:
    """Break caught: even a fully bound authority cannot take the VALID pre-test path."""
    del tmp_path
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        root = Path(raw)
        root.chmod(0o700)
        corpus = root / "corpus"
        _complete_corpus_fixture(corpus)
        assert topology._external_preflight("EXT-PHASE3B-CORPUS", corpus_root=corpus, corpus_validator=lambda _root: _valid_phase3b_analysis()) == ("VALID", "AUTHORITY_COMPLETE_VALIDATED")
        legacy = root / "legacy"
        for relative, _ in topology.LEGACY_CLOSURE_ENTRIES:
            _write_direct(legacy / relative, executable=relative.endswith("python"))
        uv = root / "uv"
        _write_direct(uv, "#!/bin/sh\nif [ \"$1\" = --version ]; then printf 'fixture-uv 1.0\\n'; fi\n", executable=True)
        expected = topology.hashlib.sha256(uv.read_bytes()).hexdigest()
        assert topology._external_preflight("EXT-LEGACY-UV-AUTHORITY", uv_path=uv, legacy_root=legacy, expected_uv_sha256=expected, expected_uv_version="fixture-uv 1.0") == ("VALID", "AUTHORITY_COMPLETE_VALIDATED")


def test_ci_portable_keeps_artifact_evidence_outside_deleted_tmp_root() -> None:
    """Break caught: receipts are published under ci_tmpdir and deleted before upload."""
    makefile = Path("Makefile").read_text(encoding="utf-8")
    portable = makefile.split("ci-portable:\n", 1)[1].split("\n\nci-portable-private:", 1)[0]
    assert 'TEST_EVIDENCE_DIR="$$ci_tmpdir' not in portable
    assert "TEST_EVIDENCE_DIR ?= /tmp/trading-agent-test-evidence" in makefile


def test_valid_external_preflight_is_the_only_path_that_selects_external_nodes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Break caught: external tests execute before a full VALID preflight."""
    monkeypatch.setenv("GITHUB_RUN_ID", "31641536482")
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    selected: list[tuple[str, ...]] = []

    def exact(nodes: tuple[str, ...], report: Path) -> tuple[str, ...]:
        selected.append(nodes)
        return _passing_exact(nodes, report)

    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "artifact"
        topology.reserve_topology_evidence(evidence, run_id="31641536482", head_sha=head)
        _seal_portable_root_baseline(
            monkeypatch, evidence, raw, run_id="31641536482", head_sha=head,
        )
        paths = topology.run_lane(
            lane="external-authorities", inventory=Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"),
            evidence_root=evidence, run_id="31641536482", head_sha=head,
            external_preflight=lambda _code: ("VALID", "AUTHORITY_COMPLETE_VALIDATED"), exact_runner=exact,
        )
        assert len(paths) == 2
    assert sorted(len(nodes) for nodes in selected) == [3, 3]


def test_runner_boundary_byte_identical_custody_replacement_cannot_publish_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: a same-byte replacement after precheck leaves a green root record."""
    run_id = "31641536482"
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        monkeypatch.setenv("GITHUB_RUN_ID", run_id)
        context = topology._capture_foundation_context(
            evidence, clock=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc),
        )
        topology.reserve_topology_evidence(
            evidence, run_id=run_id, head_sha=head, foundation_context_path=context,
        )
        _seal_portable_root_baseline(
            monkeypatch, evidence, raw, run_id=run_id, head_sha=head,
            foundation_context_path=context,
        )
        extension = Path(os.environ["PACKAGE6_FD_CUSTODY_EXTENSION_PATH"])
        replacement = Path(raw) / "same-byte-replacement.so"
        replacement.write_bytes(extension.read_bytes())
        invoked = False

        def replace_at_runner_boundary(nodes: tuple[str, ...], report: Path) -> tuple[str, ...]:
            nonlocal invoked
            invoked = True
            os.replace(replacement, extension)
            return _passing_exact(nodes, report)

        with pytest.raises(topology.TopologyError, match="custody"):
            topology.run_lane(
                lane="portable-source",
                inventory=Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"),
                evidence_root=evidence,
                run_id=run_id,
                head_sha=head,
                exact_runner=replace_at_runner_boundary,
                foundation_context_path=context,
            )

        assert invoked
        assert not any((evidence / "capability-topology").glob("SRC-*.json"))


def test_empty_remainder_replacement_after_provisional_write_cannot_publish_or_aggregate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: empty remainder publishes final PASS evidence before custody exit."""
    run_id = "31641536482"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        topology.reserve_topology_evidence(evidence, run_id=run_id, head_sha=head)
        _seal_portable_root_baseline(monkeypatch, evidence, raw, run_id=run_id, head_sha=head)
        extension = Path(os.environ["PACKAGE6_FD_CUSTODY_EXTENSION_PATH"])
        replacement = Path(raw) / "same-byte-replacement.so"
        replacement.write_bytes(extension.read_bytes())
        final = evidence / "capability-topology/portable-root-remainder.governance.json"
        real_publish = topology._publish_no_clobber

        def replace_after_empty_provisional_write(path: Path, content: bytes) -> None:
            real_publish(path, content)
            if path.name == ".portable-root-remainder.governance.json.executing":
                os.replace(replacement, extension)

        monkeypatch.setattr(topology, "_publish_no_clobber", replace_after_empty_provisional_write)

        with pytest.raises(topology.TopologyError, match="custody"):
            topology.execute_portable_root_remainder(
                inventory=Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"),
                evidence_root=evidence,
                run_id=run_id,
                head_sha=head,
            )

        assert not final.exists()
        assert not list(final.parent.glob(".portable-root-remainder.governance.json.executing"))
        with pytest.raises(topology.TopologyError, match="exact governance record"):
            topology.reconcile_portable_root_accounting(
                inventory=Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"),
                evidence_root=evidence,
                run_id=run_id,
                head_sha=head,
            )


def test_exact_pytest_child_inherits_the_retained_custody_descriptor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: a custody FD is checked only by the parent and not retained by pytest."""
    observed: dict[str, object] = {}

    def fake_run(_command, **kwargs):
        observed.update(kwargs)
        Path(kwargs["env"]["TEST_GOVERNANCE_REPORT"]).write_text(topology.json.dumps({
            "component": "root",
            "pytest_exit_status": 0,
            "tests": [{
                "test_node_id": "tests/example.py::test_exact",
                "component": "root",
                "outcome": "passed",
            }],
        }), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(topology.subprocess, "run", fake_run)
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        artifact = Path(raw) / "custody.so"
        artifact.write_bytes(b"custody")
        descriptor = os.open(artifact, os.O_RDONLY | os.O_CLOEXEC)
        try:
            with topology._governance_custody_policy({"sealed": "policy"}, descriptor):
                assert topology._run_exact(
                    ("tests/example.py::test_exact",), Path(raw) / "report.json",
                ) == ("tests/example.py::test_exact",)
        finally:
            os.close(descriptor)

    assert observed["pass_fds"] == (descriptor,)
    assert observed["env"]["TEST_GOVERNANCE_CUSTODY_FD"] == str(descriptor)


def test_standalone_native_deferred_lane_does_not_require_portable_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: a valid no-test native deferral is blocked by portable custody setup."""
    run_id = "31641536482"
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    @topology.contextmanager
    def absent_probe(code: str):
        yield topology.NativeProbeSession(
            code, "UNAVAILABLE", "NATIVE_COMPONENT_ABSENT",
            topology._native_probe_record(code, exit_code=topology.NATIVE_PROBE_NOT_EXECUTED),
            -1, None, None, None, None,
        )
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        context = topology._capture_foundation_context(
            evidence, clock=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc),
        )
        topology.reserve_topology_evidence(
            evidence, run_id=run_id, head_sha=head, foundation_context_path=context,
        )
        receipts = topology.run_lane(
            lane="native-capabilities",
            inventory=Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"),
            evidence_root=evidence,
            run_id=run_id,
            head_sha=head,
            foundation_context_path=context,
            native_probe_factory=absent_probe,
        )

        assert not (evidence / "capability-topology/portable-root-baseline.json").exists()
        assert len(receipts) == 2
        assert all(topology.parse_receipt(path.read_bytes())["outcome"] == "DEFERRED" for path in receipts)
        assert not list((evidence / "capability-topology").glob("*.governance.json"))


def test_standalone_external_deferred_lane_does_not_require_portable_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: a valid no-test external deferral is blocked by portable custody setup."""
    run_id = "31641536482"
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        topology.reserve_topology_evidence(evidence, run_id=run_id, head_sha=head)
        receipts = topology.run_lane(
            lane="external-authorities",
            inventory=Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"),
            evidence_root=evidence,
            run_id=run_id,
            head_sha=head,
            external_preflight=lambda _code: ("ABSENT", "AUTHORITY_ROOT_ABSENT"),
        )

        assert not (evidence / "capability-topology/portable-root-baseline.json").exists()
        assert len(receipts) == 2
        assert all(topology.parse_receipt(path.read_bytes())["outcome"] == "DEFERRED" for path in receipts)
        assert not list((evidence / "capability-topology").glob("*.governance.json"))


def test_topology_retry_fails_before_replacing_existing_governance_bytes(tmp_path: Path) -> None:
    """Break caught: a retry replaces topology governance evidence before receipt O_EXCL fails."""
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        topology.reserve_topology_evidence(evidence, run_id="31641536482", head_sha="a" * 40)
        observation = evidence / "capability-topology/SRC-SEALEDUV-BWRAP-PREFLIGHT.governance.json"
        original = b'{"sealed":"first"}'
        observation.write_bytes(original)
        with pytest.raises(topology.TopologyError, match="reserved or populated"):
            topology.reserve_topology_evidence(evidence, run_id="31641536482", head_sha="a" * 40)
        assert observation.read_bytes() == original


def test_topology_governance_publication_is_no_clobber(tmp_path: Path) -> None:
    """Break caught: governance reporter os.replace overwrites a reserved topology observation."""
    del tmp_path
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        report = Path(raw) / "governance.json"
        governance_plugin._atomic_json(report, {"sealed": "first"}, no_clobber=True)
        original = report.read_bytes()
        with pytest.raises(FileExistsError):
            governance_plugin._atomic_json(report, {"sealed": "second"}, no_clobber=True)
        assert report.read_bytes() == original


def test_external_rejects_intermediate_directory_symlinks(tmp_path: Path) -> None:
    """Break caught: leaf lstat passes through a symlinked Phase3B or legacy directory."""
    del tmp_path
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        root = Path(raw)
        corpus = root / "corpus"
        _complete_corpus_fixture(corpus)
        memory = corpus / "memory"
        moved_memory = corpus / "real-memory"
        memory.rename(moved_memory)
        memory.symlink_to(moved_memory, target_is_directory=True)
        assert topology._external_preflight("EXT-PHASE3B-CORPUS", corpus_root=corpus, corpus_validator=lambda _root: _valid_phase3b_analysis())[0] == "INVALID"
        legacy = root / "legacy"
        for relative, _ in topology.LEGACY_CLOSURE_ENTRIES:
            _write_direct(legacy / relative, executable=relative.endswith("python"))
        venv = legacy / ".venv"
        moved_venv = legacy / "real-venv"
        venv.rename(moved_venv)
        venv.symlink_to(moved_venv, target_is_directory=True)
        uv = root / "uv"
        _write_direct(uv, "#!/bin/sh\nexit 0\n", executable=True)
        assert topology._external_preflight("EXT-LEGACY-UV-AUTHORITY", uv_path=uv, legacy_root=legacy)[0] == "INVALID"


def test_external_rejects_parent_component_symlinks_for_every_supplied_authority_path(tmp_path: Path) -> None:
    """Break caught: validation starts at an authority leaf and follows a hostile parent link."""
    del tmp_path
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        root = Path(raw)
        trusted = root / "trusted"
        trusted.mkdir(mode=0o700)
        corpus = trusted / "corpus"
        _complete_corpus_fixture(corpus)
        legacy = trusted / "legacy"
        for relative, _ in topology.LEGACY_CLOSURE_ENTRIES:
            _write_direct(legacy / relative, executable=relative.endswith("python"))
        uv = trusted / "uv"
        payload = "#!/bin/sh\nif [ \"$1\" = --version ]; then printf 'fixture-uv 1.0\\n'; fi\n"
        _write_direct(uv, payload, executable=True)
        alias = root / "hostile-parent"
        alias.symlink_to(trusted, target_is_directory=True)
        expected = topology.hashlib.sha256(uv.read_bytes()).hexdigest()

        assert topology._external_preflight(
            "EXT-PHASE3B-CORPUS", corpus_root=alias / "corpus",
            corpus_validator=lambda _root: _valid_phase3b_analysis(),
        )[0] == "INVALID"
        assert topology._external_preflight(
            "EXT-LEGACY-UV-AUTHORITY", uv_path=uv, legacy_root=alias / "legacy",
            expected_uv_sha256=expected, expected_uv_version="fixture-uv 1.0",
        )[0] == "INVALID"
        assert topology._external_preflight(
            "EXT-LEGACY-UV-AUTHORITY", uv_path=alias / "uv", legacy_root=legacy,
            expected_uv_sha256=expected, expected_uv_version="fixture-uv 1.0",
        )[0] == "INVALID"


def test_completed_topology_retry_preserves_inventory_governance_and_receipt_before_collection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Break caught: a duplicate topology invocation mutates completed evidence before it is rejected."""
    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    monkeypatch.setenv("GITHUB_RUN_ID", "31641536482")
    calls: list[tuple[str, ...]] = []

    def exact(nodes: tuple[str, ...], report: Path) -> tuple[str, ...]:
        calls.append(nodes)
        return _passing_exact(nodes, report)

    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        context = topology._capture_foundation_context(
            evidence, clock=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc),
        )
        topology.reserve_topology_evidence(
            evidence, run_id="31641536482", head_sha=head,
            foundation_context_path=context,
        )
        _seal_portable_root_baseline(
            monkeypatch, evidence, raw, run_id="31641536482", head_sha=head,
            foundation_context_path=context,
        )
        receipts = topology.run_lane(
            lane="portable-source", inventory=Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"),
            evidence_root=evidence, run_id="31641536482", head_sha=head, exact_runner=exact,
            foundation_context_path=context,
        )
        installed = evidence / "t-g03a-hosted-failure-inventory.tsv"
        governance = evidence / "capability-topology/portable-defect-closure.governance.json"
        proof = evidence / "capability-topology/portable-defect-closure-proof.json"
        assert receipts == []
        preserved = (installed.read_bytes(), governance.read_bytes(), proof.read_bytes())

        with pytest.raises(topology.TopologyError, match="reserved or populated"):
            topology.reserve_topology_evidence(evidence, run_id="31641536482", head_sha=head)

        assert len(calls) == 1
        assert (installed.read_bytes(), governance.read_bytes(), proof.read_bytes()) == preserved


def test_retained_uv_rejects_named_replacement_after_descriptor_execution(tmp_path: Path) -> None:
    """Break caught: UV is digested then a replacement pathname executes or is accepted."""
    del tmp_path
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        root = Path(raw)
        legacy = root / "legacy"
        for relative, _ in topology.LEGACY_CLOSURE_ENTRIES:
            _write_direct(legacy / relative, executable=relative.endswith("python"))
        uv = root / "uv"
        payload = "#!/bin/sh\nif [ \"$1\" = --version ]; then printf 'fixture-uv 1.0\\n'; fi\n"
        _write_direct(uv, payload, executable=True)
        replacement = root / "replacement"
        _write_direct(replacement, payload, executable=True)
        expected = topology.hashlib.sha256(uv.read_bytes()).hexdigest()
        commands: list[list[str]] = []

        def swapping_runner(command, **kwargs):
            commands.append(command)
            result = subprocess.run(command, **kwargs)
            if command[1] == "--version":
                os.replace(replacement, uv)
            return result

        assert topology._external_preflight("EXT-LEGACY-UV-AUTHORITY", uv_path=uv, legacy_root=legacy, expected_uv_sha256=expected, expected_uv_version="fixture-uv 1.0", runner=swapping_runner)[0] == "INVALID"
        assert all(command[0].startswith("/proc/self/fd/") for command in commands)
