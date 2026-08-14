from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace

import pytest

from scripts import t_g03_capability_topology as topology


INVENTORY = Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv")


def _digest(document: object) -> str:
    return hashlib.sha256(
        json.dumps(
            document, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8"),
    ).hexdigest()


def _context() -> dict[str, object]:
    return {
        "schema_version": "t-g03a-foundation-context/v1",
        "foundation_run_id": "31641536482",
        "foundation_head_sha": "18f22198c65c7bc735aeb848d8fda55209d01e78",
        "foundation_validation_date": "2026-08-13",
        "foundation_context_sha256": "4" * 64,
    }


def _absent_authority(code: str) -> dict[str, object]:
    if code == "EXT-PHASE3B-CORPUS":
        return {
            "authority_kind": "PHASE3B_REVIEWED_CORPUS_V1",
            "regular_directory_status": "ABSENT",
            "expected_inventory_sha256": topology.PHASE3B_EXPECTED_INVENTORY_SHA256,
            "observed_inventory_sha256": topology.EMPTY_SHA256,
            "required_entry_manifest_sha256": topology.EMPTY_SHA256,
            "required_entry_count": 0,
            "expected_decision_total": 16517,
            "observed_decision_total": 0,
            "expected_cost_sessions": 20,
            "observed_cost_sessions": 0,
            "expected_asset_count": 17,
            "observed_asset_count": 0,
            "expected_asset_source_files": 2209,
            "observed_asset_source_files": 0,
        }
    return {
        "authority_kind": "LEGACY_UV_AND_CLOSURE_V1",
        "regular_file_status": "ABSENT",
        "expected_uv_sha256": topology.LEGACY_UV_SHA256,
        "observed_uv_sha256": topology.EMPTY_SHA256,
        "expected_uv_version": topology.LEGACY_UV_VERSION,
        "observed_uv_version": "",
        "expected_uid": os.geteuid(),
        "observed_uid": -1,
        "expected_gid": os.getegid(),
        "observed_gid": -1,
        "expected_mode": 0o755,
        "observed_mode": -1,
        "legacy_closure_manifest_sha256": topology.EMPTY_SHA256,
        "legacy_closure_entry_count": 0,
        "sync_command_id": "LEGACY_UV_SYNC_FROZEN_OFFLINE_V1",
        "sync_exit_code": -1,
        "sync_stdout_sha256": topology.EMPTY_SHA256,
        "sync_stderr_sha256": topology.EMPTY_SHA256,
    }


def _external_receipt(
    code: str = "EXT-PHASE3B-CORPUS", **overrides: object,
) -> dict[str, object]:
    context = _context()
    rows = topology.load_inventory(INVENTORY)
    expected = list(topology._expected_rows(rows, code)[1])
    document: dict[str, object] = {
        "schema_version": "t-g03a-external-authority-receipt/v2",
        "foundation_run_id": context["foundation_run_id"],
        "foundation_head_sha": context["foundation_head_sha"],
        "foundation_validation_date": context["foundation_validation_date"],
        "foundation_context_sha256": context["foundation_context_sha256"],
        "inventory_sha256": topology.LOCKED_INVENTORY_SHA256,
        "lane": "external-authorities",
        "capability_or_authority_code": code,
        "expected_node_ids": expected,
        "collected_node_ids": [],
        "preflight_state": "ABSENT",
        "redacted_fact_class": (
            "AUTHORITY_ROOT_ABSENT"
            if code == "EXT-PHASE3B-CORPUS"
            else "AUTHORITY_EXECUTABLE_ABSENT"
        ),
        "authority": _absent_authority(code),
        "selected_test_count": 0,
        "passed": 0,
        "failed": 0,
        "unavailable": len(expected),
        "completeness_sha256": "",
        "outcome": "DEFERRED",
        "receipt_sha256": "",
    }
    document.update(overrides)
    document["completeness_sha256"] = _digest({
        key: value
        for key, value in document.items()
        if key not in {"completeness_sha256", "receipt_sha256"}
    })
    document["receipt_sha256"] = _digest({
        key: value for key, value in document.items() if key != "receipt_sha256"
    })
    return document


def _safe_fixture_root() -> tempfile.TemporaryDirectory[str]:
    anchor = Path(f"/run/user/{os.geteuid()}")
    info = anchor.lstat()
    assert info.st_uid == os.geteuid()
    assert info.st_gid == os.getegid()
    assert info.st_mode & 0o777 == 0o700
    return tempfile.TemporaryDirectory(dir=anchor)


def _write_regular(path: Path, content: bytes = b"fixture\n", *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(content)
    path.chmod(mode)


def _complete_phase3b(root: Path) -> None:
    root.mkdir(mode=0o700)
    for relative, is_directory in topology.PHASE3B_REQUIRED_ENTRIES:
        path = root / relative
        if is_directory:
            path.mkdir(parents=True, mode=0o700)
            path.chmod(0o700)
        else:
            _write_regular(path)


def _phase3b_analysis() -> SimpleNamespace:
    return SimpleNamespace(
        inventory_hash=topology.PHASE3B_EXPECTED_INVENTORY_SHA256,
        decision_total=16517,
        cost_sessions=20,
        asset_count=17,
        asset_source_files=2209,
    )


def _complete_legacy(root: Path) -> None:
    root.mkdir(mode=0o700)
    for relative, _ in topology.LEGACY_CLOSURE_ENTRIES:
        path = root / relative
        if relative == ".venv/bin/python":
            target = root / ".venv/bin/python3"
            _write_regular(target, b"python fixture\n", mode=0o700)
            path.symlink_to(target)
        else:
            _write_regular(path)


def _custody() -> dict[str, str]:
    return {
        **topology.PORTABLE_ROOT_POLICY,
        "native_custody_extension_identity": "1:2:1000:600:1",
        "native_custody_extension_sha256": "9" * 64,
    }


def _governance_raw(nodes: tuple[str, ...]) -> bytes:
    return json.dumps({
        "schema_version": 1,
        "component": "root",
        "pytest_exit_status": 0,
        "custody_policy": _custody(),
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
    }, sort_keys=True).encode("utf-8")


def _phase3b_pass_receipt() -> dict[str, object]:
    rows = topology.load_inventory(INVENTORY)
    code = "EXT-PHASE3B-CORPUS"
    expected = list(topology._expected_rows(rows, code)[1])
    authority = {
        **_absent_authority(code),
        "regular_directory_status": "PRIVATE_CURRENT_USER_DIRECTORY",
        "observed_inventory_sha256": topology.PHASE3B_EXPECTED_INVENTORY_SHA256,
        "required_entry_manifest_sha256": "8" * 64,
        "required_entry_count": len(topology.PHASE3B_REQUIRED_ENTRIES),
        "observed_decision_total": 16517,
        "observed_cost_sessions": 20,
        "observed_asset_count": 17,
        "observed_asset_source_files": 2209,
    }
    return _external_receipt(
        code,
        collected_node_ids=expected,
        preflight_state="VALID",
        redacted_fact_class="AUTHORITY_COMPLETE_VALIDATED",
        authority=authority,
        selected_test_count=len(expected),
        passed=len(expected),
        failed=0,
        unavailable=0,
        outcome="PASS",
    )


def test_external_v2_binds_exact_context_nodes_counts_authority_and_hashes() -> None:
    """Break caught: flat or weak external evidence omits context, authority, or counts."""
    rows = topology.load_inventory(INVENTORY)
    context = _context()
    receipt = _external_receipt()

    assert topology.validate_receipt(
        topology.canonical_json_bytes(receipt), rows=rows,
        foundation_run_id=str(context["foundation_run_id"]),
        foundation_head_sha=str(context["foundation_head_sha"]),
        foundation_context=context,
    ) == receipt

    for mutation in (
        {"foundation_context_sha256": "5" * 64},
        {"expected_node_ids": []},
        {"selected_test_count": False},
        {"unavailable": 2},
    ):
        forged = _external_receipt(**mutation)
        with pytest.raises(topology.TopologyError):
            topology.validate_receipt(
                topology.canonical_json_bytes(forged), rows=rows,
                foundation_run_id=str(context["foundation_run_id"]),
                foundation_head_sha=str(context["foundation_head_sha"]),
                foundation_context=context,
            )


def test_external_v1_is_stale_without_changing_native_v2() -> None:
    """Break caught: an external flat-v1 receipt remains accepted after migration."""
    rows = topology.load_inventory(INVENTORY)
    context = _context()
    code = "EXT-PHASE3B-CORPUS"
    expected = list(topology._expected_rows(rows, code)[1])
    stale: dict[str, object] = {
        "schema_version": "t-g03a-capability-receipt/v1",
        "foundation_run_id": context["foundation_run_id"],
        "foundation_head_sha": context["foundation_head_sha"],
        "inventory_sha256": topology.LOCKED_INVENTORY_SHA256,
        "lane": "external-authorities",
        "capability_or_authority_code": code,
        "expected_node_ids": expected,
        "collected_node_ids": [],
        "completeness_sha256": "",
        "preflight_state": "ABSENT",
        "redacted_fact_class": "AUTHORITY_ROOT_ABSENT",
        "outcome": "DEFERRED",
        "receipt_sha256": "",
    }
    stale["completeness_sha256"] = _digest({
        field: stale[field]
        for field in (
            "lane", "capability_or_authority_code", "expected_node_ids",
            "collected_node_ids",
        )
    })
    stale["receipt_sha256"] = _digest({
        key: value for key, value in stale.items() if key != "receipt_sha256"
    })

    with pytest.raises(topology.TopologyError, match="external v1"):
        topology.validate_receipt(
            topology.canonical_json_bytes(stale), rows=rows,
            foundation_run_id=str(context["foundation_run_id"]),
            foundation_head_sha=str(context["foundation_head_sha"]),
            foundation_context=context,
        )


def test_external_parent_chain_includes_the_filesystem_anchor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: checking descendants but trusting an unsafe anchor is incomplete."""
    safe = SimpleNamespace(
        st_mode=0o040755, st_uid=0, st_gid=0, st_dev=1, st_ino=1,
    )
    unsafe_anchor = SimpleNamespace(
        st_mode=0o040777, st_uid=0, st_gid=0, st_dev=1, st_ino=2,
    )

    def fake_lstat(path: Path) -> SimpleNamespace:
        return unsafe_anchor if path == Path("/") else safe

    monkeypatch.setattr(Path, "lstat", fake_lstat)
    assert topology._external_parent_chain_safe(Path("/authority/file")) is False


def test_phase3b_session_retains_root_and_revalidates_manifest_and_analysis() -> None:
    """Break caught: tuple preflight cannot detect required-entry drift before acceptance."""
    with _safe_fixture_root() as raw:
        root = Path(raw) / "corpus"
        _complete_phase3b(root)
        with topology._retained_external_authority(
            "EXT-PHASE3B-CORPUS",
            corpus_root=root,
            corpus_validator=lambda _root: _phase3b_analysis(),
        ) as session:
            assert (session.state, session.fact) == (
                "VALID", "AUTHORITY_COMPLETE_VALIDATED",
            )
            assert session.authority["required_entry_count"] == len(
                topology.PHASE3B_REQUIRED_ENTRIES,
            )
            assert set(session.authority).isdisjoint({"path", "root", "contents"})
            topology._postcheck_external_authority(session)
            (root / "asset_registry.py").write_bytes(b"changed\n")
            with pytest.raises(topology.TopologyError, match="authority"):
                topology._postcheck_external_authority(session)


def test_legacy_session_executes_only_retained_uv_and_detects_closure_drift() -> None:
    """Break caught: UV is closed after preflight or a version-only impostor is accepted."""
    commands: list[tuple[list[str], dict[str, object]]] = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        commands.append((command, kwargs))
        stdout = b"fixture-uv 1.0\n" if command[1:] == ["--version"] else b""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=b"")

    with _safe_fixture_root() as raw:
        root = Path(raw)
        legacy = root / "legacy"
        _complete_legacy(legacy)
        uv = root / "uv"
        _write_regular(uv, b"fixture uv authority\n", mode=0o755)
        expected = hashlib.sha256(uv.read_bytes()).hexdigest()
        with topology._retained_external_authority(
            "EXT-LEGACY-UV-AUTHORITY",
            uv_path=uv,
            legacy_root=legacy,
            expected_uv_sha256=expected,
            expected_uv_version="fixture-uv 1.0",
            runner=runner,
        ) as session:
            assert (session.state, session.fact) == (
                "VALID", "AUTHORITY_COMPLETE_VALIDATED",
            )
            assert [command[1:] for command, _ in commands] == [
                ["--version"], ["sync", "--frozen", "--extra", "test"],
            ]
            assert all(command[0].startswith("/proc/self/fd/") for command, _ in commands)
            assert all(kwargs["pass_fds"] == (session.descriptors[0],) for _, kwargs in commands)
            topology._postcheck_external_authority(session)
            (legacy / "uv.lock").write_bytes(b"changed\n")
            with pytest.raises(topology.TopologyError, match="authority"):
                topology._postcheck_external_authority(session)


def test_legacy_group_writable_exception_is_scoped_to_exact_real_component(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: the real 0775 exception is broadened to an arbitrary root."""

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        stdout = b"fixture-uv 1.0\n" if command[1:] == ["--version"] else b""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=b"")

    with _safe_fixture_root() as raw:
        root = Path(raw)
        uv = root / "uv"
        _write_regular(uv, b"fixture uv authority\n", mode=0o755)
        projects = root / "projects"
        projects.mkdir(mode=0o775)
        projects.chmod(0o775)
        arbitrary = projects / "arbitrary"
        _complete_legacy(arbitrary)
        with topology._retained_external_authority(
            "EXT-LEGACY-UV-AUTHORITY", uv_path=uv, legacy_root=arbitrary,
            expected_uv_sha256=hashlib.sha256(uv.read_bytes()).hexdigest(),
            expected_uv_version="fixture-uv 1.0", runner=runner,
        ) as session:
            assert session.state == "INVALID"

        exact_legacy = projects / "legacy"
        _complete_legacy(exact_legacy)
        monkeypatch.setattr(topology, "REAL_LEGACY_ROOT", exact_legacy)
        assert topology._external_parent_chain_safe(exact_legacy) is False
        assert topology._external_parent_chain_safe(
            exact_legacy, legacy_component_policy=True,
        ) is True
        assert topology._external_parent_chain_safe(
            arbitrary, legacy_component_policy=True,
        ) is False


@pytest.mark.parametrize(
    ("unsafe_mode", "unsafe_uid", "unsafe_gid"),
    (
        (0o040777, os.geteuid(), os.getegid()),
        (0o040755, os.geteuid() + 10000, os.getegid()),
        (0o040755, os.geteuid(), os.getegid() + 10000),
        (0o120777, os.geteuid(), os.getegid()),
        (0o100600, os.geteuid(), os.getegid()),
    ),
)
def test_real_legacy_ancestor_exception_rejects_world_foreign_symlink_and_special(
    monkeypatch: pytest.MonkeyPatch,
    unsafe_mode: int,
    unsafe_uid: int,
    unsafe_gid: int,
) -> None:
    """Break caught: the exact real-path exception weakens non-group-write policy."""
    real_legacy = topology.ROOT / "legacy/research-backend"
    unsafe_at = topology.ROOT.parent

    def fake_lstat(path: Path) -> SimpleNamespace:
        if path == unsafe_at:
            return SimpleNamespace(
                st_mode=unsafe_mode, st_uid=unsafe_uid, st_gid=unsafe_gid,
                st_dev=1, st_ino=2, st_size=0, st_mtime_ns=0, st_ctime_ns=0,
            )
        return SimpleNamespace(
            st_mode=0o040755, st_uid=0, st_gid=0,
            st_dev=1, st_ino=1, st_size=0, st_mtime_ns=0, st_ctime_ns=0,
        )

    monkeypatch.setattr(Path, "lstat", fake_lstat)
    assert topology._external_parent_chain_safe(
        real_legacy, legacy_component_policy=True,
    ) is False


def test_phase_and_uv_never_inherit_real_legacy_group_write_exception() -> None:
    """Break caught: the scoped legacy policy leaks into strict Phase or UV paths."""
    with _safe_fixture_root() as raw:
        root = Path(raw)
        ancestor = root / "group-writable"
        ancestor.mkdir(mode=0o775)
        ancestor.chmod(0o775)
        corpus = ancestor / "corpus"
        _complete_phase3b(corpus)
        uv = ancestor / "uv"
        _write_regular(uv, b"uv fixture\n", mode=0o755)

        phase_state, phase_descriptor, _ = topology._open_external_directory(
            corpus, exact_mode=0o700,
        )
        uv_state, uv_descriptor, _ = topology._open_external_regular_executable(uv)
        for descriptor in (phase_descriptor, uv_descriptor):
            if descriptor >= 0:
                os.close(descriptor)
        assert phase_state == "INVALID"
        assert uv_state == "INVALID"


@topology.contextmanager
def _valid_session_with_mutable_ancestor(kind: str, root: Path):
    if kind == "phase":
        ancestor = root / "phase-parent"
        ancestor.mkdir(mode=0o700)
        authority_root = ancestor / "corpus"
        _complete_phase3b(authority_root)
        with topology._retained_external_authority(
            "EXT-PHASE3B-CORPUS", corpus_root=authority_root,
            corpus_validator=lambda _root: _phase3b_analysis(),
        ) as session:
            assert session.state == "VALID"
            yield session, ancestor, authority_root
        return

    uv_parent = root / "uv-parent"
    uv_parent.mkdir(mode=0o700)
    uv = uv_parent / "uv"
    _write_regular(uv, b"fixture uv authority\n", mode=0o755)
    ancestor = root / "legacy-parent"
    ancestor.mkdir(mode=0o700)
    authority_root = ancestor / "legacy"
    _complete_legacy(authority_root)

    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        stdout = b"fixture-uv 1.0\n" if command[1:] == ["--version"] else b""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=b"")

    with topology._retained_external_authority(
        "EXT-LEGACY-UV-AUTHORITY", uv_path=uv, legacy_root=authority_root,
        expected_uv_sha256=hashlib.sha256(uv.read_bytes()).hexdigest(),
        expected_uv_version="fixture-uv 1.0", runner=runner,
    ) as session:
        assert session.state == "VALID"
        yield session, ancestor, authority_root


def _drift_ancestor(ancestor: Path, authority_root: Path, drift: str) -> None:
    if drift == "mode":
        ancestor.chmod(0o777)
        return
    moved = ancestor.with_name(ancestor.name + "-moved")
    ancestor.rename(moved)
    if drift == "symlink":
        ancestor.symlink_to(moved, target_is_directory=True)
        return
    ancestor.mkdir(mode=0o700)
    (moved / authority_root.name).rename(ancestor / authority_root.name)


@pytest.mark.parametrize("kind", ("phase", "legacy"))
@pytest.mark.parametrize("drift", ("mode", "identity", "symlink"))
def test_valid_external_session_rejects_ancestor_drift(
    kind: str, drift: str,
) -> None:
    """Break caught: retained leaf identity hides ancestor policy drift."""
    with _safe_fixture_root() as raw:
        with _valid_session_with_mutable_ancestor(kind, Path(raw)) as (
            session, ancestor, authority_root,
        ):
            _drift_ancestor(ancestor, authority_root, drift)
            with pytest.raises(topology.TopologyError, match="authority changed"):
                topology._postcheck_external_authority(session)


@pytest.mark.parametrize("kind", ("phase", "legacy"))
@pytest.mark.parametrize("drift", ("mode", "identity", "symlink"))
@pytest.mark.parametrize("boundary", ("before-bundle", "after-bundle"))
def test_external_transaction_rechecks_ancestor_policy_at_both_boundaries(
    monkeypatch: pytest.MonkeyPatch, kind: str, drift: str, boundary: str,
) -> None:
    """Break caught: one Architecture-A pre-marker boundary trusts stale ancestry."""
    rows = topology.load_inventory(INVENTORY)
    with _safe_fixture_root() as raw:
        root = Path(raw)
        evidence = root / "evidence"
        with _valid_session_with_mutable_ancestor(kind, root) as (
            session, ancestor, authority_root,
        ):
            expected = topology._expected_rows(rows, session.code)[1]
            receipt = topology.make_external_receipt(
                context=_context(), code=session.code, expected=expected,
                collected=expected, session=session, outcome="PASS",
                selected_test_count=3, passed=3, failed=0, unavailable=0,
            )
            governance = _governance_raw(expected)
            if boundary == "before-bundle":
                _drift_ancestor(ancestor, authority_root, drift)
            else:
                real_publish = topology._publish_external_candidate_bundle

                def publish_then_drift(candidate: Path, destination: Path) -> None:
                    real_publish(candidate, destination)
                    _drift_ancestor(ancestor, authority_root, drift)

                monkeypatch.setattr(
                    topology, "_publish_external_candidate_bundle",
                    publish_then_drift,
                )

            with pytest.raises(topology.TopologyError, match="authority changed"):
                topology._publish_external_receipt_transaction(
                    receipt=receipt, evidence_root=evidence, session=session,
                    governance_raw=governance,
                )
            topology_root = evidence / "capability-topology"
            marker = topology_root / f"{session.code}.json"
            bundle = topology_root / f"{session.code}.artifacts"
            assert not marker.exists()
            assert bundle.exists() is (boundary == "after-bundle")


def test_external_architecture_a_accepts_only_exact_bundle_then_marker() -> None:
    """Break caught: an external PASS can still use flat receipt/governance leaves."""
    rows = topology.load_inventory(INVENTORY)
    context = _context()
    receipt = _phase3b_pass_receipt()
    expected = tuple(receipt["expected_node_ids"])
    governance = _governance_raw(expected)
    with _safe_fixture_root() as raw:
        topology_root = Path(raw) / "capability-topology"
        topology_root.mkdir(mode=0o700)
        candidate = topology._stage_external_candidate(
            topology_root, receipt, governance,
        )
        marker = topology_root / "EXT-PHASE3B-CORPUS.json"
        topology._publish_external_candidate_bundle(
            candidate, marker.with_suffix(".artifacts"),
        )
        topology._publish_external_acceptance_marker(
            marker, topology.canonical_json_bytes(receipt),
        )

        accepted, executed = topology.validate_external_artifact_set(
            marker, rows=rows, foundation_context=context,
            sealed_custody=_custody(),
        )
        assert accepted == receipt
        assert executed == expected
        assert marker.read_bytes() == (
            marker.with_suffix(".artifacts") / "receipt.json"
        ).read_bytes()
        assert set(path.name for path in marker.with_suffix(".artifacts").iterdir()) == {
            "receipt.json", "governance.json", "manifest.json",
        }
        assert not marker.with_suffix(".governance.json").exists()


@pytest.mark.parametrize("tamper", ("marker", "manifest", "governance", "filename"))
def test_external_architecture_a_rejects_bound_artifact_tamper(tamper: str) -> None:
    """Break caught: one mutable or renamed bundle component can qualify PASS."""
    rows = topology.load_inventory(INVENTORY)
    receipt = _phase3b_pass_receipt()
    governance = _governance_raw(tuple(receipt["expected_node_ids"]))
    with _safe_fixture_root() as raw:
        topology_root = Path(raw) / "capability-topology"
        topology_root.mkdir(mode=0o700)
        marker = topology_root / "EXT-PHASE3B-CORPUS.json"
        bundle = marker.with_suffix(".artifacts")
        candidate = topology._stage_external_candidate(
            topology_root, receipt, governance,
        )
        topology._publish_external_candidate_bundle(candidate, bundle)
        topology._publish_external_acceptance_marker(
            marker, topology.canonical_json_bytes(receipt),
        )
        candidate_marker = marker
        if tamper == "marker":
            marker.write_bytes(b"{}")
        elif tamper == "manifest":
            manifest = bundle / "manifest.json"
            manifest.write_bytes(manifest.read_bytes() + b"\n")
        elif tamper == "governance":
            (bundle / "governance.json").unlink()
        else:
            candidate_marker = topology_root / "renamed.json"
            marker.rename(candidate_marker)

        with pytest.raises((topology.TopologyError, OSError)):
            topology.validate_external_artifact_set(
                candidate_marker, rows=rows, foundation_context=_context(),
                sealed_custody=_custody(),
            )


def test_external_publication_preserves_foreign_occupancy_without_unlink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: retry cleanup deletes a foreign bundle or marker before failing."""
    receipt = _external_receipt()
    session = topology.ExternalAuthoritySession(
        "EXT-PHASE3B-CORPUS", "ABSENT", "AUTHORITY_ROOT_ABSENT",
        _absent_authority("EXT-PHASE3B-CORPUS"), (), lambda: None,
    )
    original_unlink = Path.unlink
    unlinked: list[Path] = []

    def observed_unlink(path: Path, *args: object, **kwargs: object) -> None:
        unlinked.append(path)
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", observed_unlink)
    with _safe_fixture_root() as raw:
        root = Path(raw)
        foreign_bundle_root = root / "bundle-conflict"
        topology_root = foreign_bundle_root / "capability-topology"
        topology_root.mkdir(parents=True, mode=0o700)
        bundle = topology_root / "EXT-PHASE3B-CORPUS.artifacts"
        bundle.mkdir(mode=0o700)
        foreign = bundle / "foreign"
        foreign.write_bytes(b"preserve me")
        foreign.chmod(0o600)

        with pytest.raises(topology.TopologyError):
            topology._publish_external_receipt_transaction(
                receipt=receipt, evidence_root=foreign_bundle_root,
                session=session, governance_raw=None,
            )
        assert foreign.read_bytes() == b"preserve me"

        foreign_marker_root = root / "marker-conflict"
        topology_root = foreign_marker_root / "capability-topology"
        topology_root.mkdir(parents=True, mode=0o700)
        marker = topology_root / "EXT-PHASE3B-CORPUS.json"
        marker.write_bytes(b"foreign marker")
        marker.chmod(0o600)
        with pytest.raises(topology.TopologyError):
            topology._publish_external_receipt_transaction(
                receipt=receipt, evidence_root=foreign_marker_root,
                session=session, governance_raw=None,
            )
        assert marker.read_bytes() == b"foreign marker"
        assert unlinked == []


def test_external_ambiguous_marker_success_requires_exact_retained_reread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: a post-rename exception is either blindly failed or trusted."""
    rows = topology.load_inventory(INVENTORY)
    receipt = _external_receipt()
    with _safe_fixture_root() as raw:
        topology_root = Path(raw) / "capability-topology"
        topology_root.mkdir(mode=0o700)
        marker = topology_root / "EXT-PHASE3B-CORPUS.json"
        candidate = topology._stage_external_candidate(topology_root, receipt, None)
        topology._publish_external_candidate_bundle(
            candidate, marker.with_suffix(".artifacts"),
        )
        real_publish = topology._publish_external_acceptance_marker

        def ambiguous(path: Path, content: bytes) -> None:
            real_publish(path, content)
            raise OSError("status lost after successful marker rename")

        monkeypatch.setattr(
            topology, "_publish_external_acceptance_marker", ambiguous,
        )
        topology._publish_external_marker_or_resolve(
            marker, topology.canonical_json_bytes(receipt), None,
        )
        accepted, executed = topology.validate_external_artifact_set(
            marker, rows=rows, foundation_context=_context(),
            sealed_custody=_custody(),
        )
        assert accepted == receipt
        assert executed == ()


def test_external_absence_uses_architecture_a_and_host_require_pass_rejects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: portable absence is mislabeled PASS or host qualification accepts it."""
    run_id = "31641536482"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    with _safe_fixture_root() as raw:
        root = Path(raw)
        evidence = root / "evidence"
        monkeypatch.setenv("GITHUB_RUN_ID", run_id)
        context_path = topology._capture_foundation_context(
            evidence, clock=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc),
        )
        topology.reserve_topology_evidence(
            evidence, run_id=run_id, head_sha=head,
            foundation_context_path=context_path,
        )

        @topology.contextmanager
        def absent_factory(code: str):
            with topology._retained_external_authority(
                code,
                corpus_root=root / "absent-corpus",
                uv_path=root / "absent-uv",
                legacy_root=root / "absent-legacy",
            ) as session:
                yield session

        invoked: list[tuple[str, ...]] = []
        publications = topology.run_lane(
            lane="external-authorities", inventory=INVENTORY,
            evidence_root=evidence, run_id=run_id, head_sha=head,
            foundation_context_path=context_path,
            external_session_factory=absent_factory,
            exact_runner=lambda nodes, _report: invoked.append(nodes) or nodes,
        )
        assert len(publications) == 2
        assert invoked == []
        rows = topology._installed_inventory_rows(INVENTORY, evidence)
        context = topology.load_foundation_context(
            context_path, run_id=run_id, head_sha=head,
        )
        topology_root = evidence / "capability-topology"
        assert topology.validate_external_artifacts(
            topology_root, rows=rows, foundation_context=context,
            sealed_custody=_custody(), require_pass=False,
        ) == "DEFERRED"
        with pytest.raises(topology.TopologyError, match="requires PASS"):
            topology.validate_external_artifacts(
                topology_root, rows=rows, foundation_context=context,
                sealed_custody=_custody(), require_pass=True,
            )


@pytest.mark.parametrize("state", ("PARTIAL", "INVALID", "DRIFTED"))
def test_external_nonqualifying_state_publishes_strict_fail_and_stops_lane(
    monkeypatch: pytest.MonkeyPatch, state: str,
) -> None:
    """Break caught: a present broken authority is downgraded to portable deferral."""
    run_id = "31641536482"
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True,
    ).stdout.strip()
    monkeypatch.setenv("GITHUB_RUN_ID", run_id)
    with _safe_fixture_root() as raw:
        evidence = Path(raw) / "evidence"
        context_path = topology._capture_foundation_context(
            evidence, clock=lambda: datetime(2026, 8, 13, tzinfo=timezone.utc),
        )
        topology.reserve_topology_evidence(
            evidence, run_id=run_id, head_sha=head,
            foundation_context_path=context_path,
        )

        @topology.contextmanager
        def invalid_factory(code: str):
            yield topology.ExternalAuthoritySession(
                code, state, "AUTHORITY_INVALID",
                topology._invalid_external_authority(code), (), lambda: None,
            )

        with pytest.raises(topology.TopologyError, match=f"preflight is {state}"):
            topology.run_lane(
                lane="external-authorities", inventory=INVENTORY,
                evidence_root=evidence, run_id=run_id, head_sha=head,
                foundation_context_path=context_path,
                external_session_factory=invalid_factory,
            )
        marker = evidence / "capability-topology/EXT-LEGACY-UV-AUTHORITY.json"
        receipt = topology.parse_receipt(marker.read_bytes())
        assert receipt["schema_version"] == topology.EXTERNAL_RECEIPT_SCHEMA
        assert receipt["outcome"] == "FAIL"
        assert receipt["preflight_state"] == state
        assert not (marker.with_suffix(".artifacts") / "governance.json").exists()


def test_validate_external_cli_carries_the_explicit_require_pass_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: the future host caller cannot distinguish DEFERRED from PASS."""
    observed: list[bool] = []
    monkeypatch.setattr(topology, "_active_foundation_identity", lambda: ("7", "a" * 40))
    monkeypatch.setattr(topology, "load_foundation_context", lambda *_a, **_k: _context())
    monkeypatch.setattr(topology, "_installed_inventory_rows", lambda *_a, **_k: ())
    monkeypatch.setattr(
        topology, "load_portable_root_baseline",
        lambda *_a, **_k: {"collector_policy": _custody()},
    )
    monkeypatch.setattr(
        topology, "validate_external_artifacts",
        lambda *_a, require_pass, **_k: observed.append(require_pass) or "PASS",
    )

    assert topology.main([
        "validate-external", "--require-pass",
        "--evidence-root", "/tmp/test-external-cli-evidence",
        "--foundation-context-path", "/tmp/test-external-cli-context.json",
    ]) == 0
    assert observed == [True]


def test_standalone_external_make_target_builds_custody_and_baseline_once() -> None:
    """Break caught: VALID external exact nodes cannot run from the standalone target."""
    source = Path("Makefile").read_text(encoding="utf-8")
    recipe = source.split("test-external-authorities:\n", 1)[1].split(
        "\n\ntest-portable-root-remainder:", 1,
    )[0]

    assert "package6-custodian-external-authorities" in recipe
    assert "PACKAGE6_FD_CUSTODY_EXTENSION_PATH" in recipe
    assert "PACKAGE6_FD_CUSTODY_EXTENSION_SHA256" in recipe
    assert recipe.count("scripts.t_g03_capability_topology collect-baseline") == 1
    assert recipe.count(
        "scripts.t_g03_capability_topology run-lane --lane external-authorities",
    ) == 1
    assert recipe.index(" collect-baseline ") < recipe.index(" run-lane ")
