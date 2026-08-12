from __future__ import annotations

import os
import pytest
from pathlib import Path
import tempfile
import subprocess
from types import SimpleNamespace

from scripts import t_g03_capability_topology as topology
from scripts import test_governance_pytest as governance_plugin


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
    assert len(rows) == 62
    changed = tmp_path / "changed.tsv"
    changed.write_bytes(tracked.read_bytes().replace(b"PORTABLE_SOURCE_DEFECT", b"PORTABLE_SOURCE_DEFEKT", 1))
    with pytest.raises(topology.TopologyError, match="hash drift"):
        topology.load_inventory(changed)


def test_aggregate_rejects_partial_and_execution_bearing_deferred_receipts(tmp_path: Path) -> None:
    """Break caught: partial or execution-bearing deferred evidence makes CI green."""
    rows = topology.load_inventory(Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"))
    run, head = "31641536482", "18f22198c65c7bc735aeb848d8fda55209d01e78"
    paths: list[Path] = []
    for code in topology.CODE_CLASSIFICATION:
        lane, node_ids = topology._expected_rows(rows, code)
        state, outcome = {"portable-source": ("AVAILABLE", "PASS"), "native-capabilities": ("UNAVAILABLE", "DEFERRED"), "external-authorities": ("ABSENT", "DEFERRED")}[lane]
        receipt = _receipt(foundation_run_id=run, foundation_head_sha=head, lane=lane, capability_or_authority_code=code, expected_node_ids=list(node_ids), collected_node_ids=list(node_ids) if outcome == "PASS" else [], preflight_state=state, outcome=outcome)
        path = tmp_path / f"{code}.json"
        path.write_bytes(topology.canonical_json_bytes(receipt))
        paths.append(path)
    summary = topology.aggregate_receipts(paths, rows=rows, foundation_run_id=run, foundation_head_sha=head)
    assert summary["runtime_proof"] == "COMPLETE_WITH_DEFERRED_RUNTIME_CHECKS"
    forged = _receipt(foundation_run_id=run, foundation_head_sha=head, lane="external-authorities", capability_or_authority_code="EXT-PHASE3B-CORPUS", expected_node_ids=list(topology._expected_rows(rows, "EXT-PHASE3B-CORPUS")[1]), collected_node_ids=["tests/control_api/test_phase3b_backfill.py::test_real_backfill_plan_has_only_approved_evidence"], preflight_state="ABSENT", outcome="DEFERRED")
    with pytest.raises(topology.TopologyError, match="DEFERRED receipt selected"):
        topology.validate_receipt(topology.canonical_json_bytes(forged), rows=rows, foundation_run_id=run, foundation_head_sha=head)


def test_receipt_rejects_a_json_number_even_when_its_text_is_a_valid_run_id() -> None:
    """Break caught: alternate JSON types bypass the v1 string-only canonical protocol."""
    receipt = _receipt(foundation_run_id=31641536482)
    with pytest.raises(topology.TopologyError, match="foundation run"):
        topology.parse_receipt(topology.canonical_json_bytes(receipt))


def test_receipt_rejects_unredacted_fact_payload_and_stale_or_wrong_mapping() -> None:
    """Break caught: redacted receipt fields carry details or stale/mapped evidence passes."""
    rows = topology.load_inventory(Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"))
    run, head = "31641536482", "18f22198c65c7bc735aeb848d8fda55209d01e78"
    code = "SRC-SEMANTIC-FIXTURE-IDENTITY"
    _, expected = topology._expected_rows(rows, code)
    receipt = _receipt(foundation_run_id=run, foundation_head_sha=head, capability_or_authority_code=code, expected_node_ids=list(expected), collected_node_ids=list(expected), redacted_fact_class="/home/operator/secret")
    with pytest.raises(topology.TopologyError, match="redacted"):
        topology.parse_receipt(topology.canonical_json_bytes(receipt))
    receipt = _receipt(foundation_run_id="31641536481", foundation_head_sha=head, capability_or_authority_code=code, expected_node_ids=list(expected), collected_node_ids=list(expected))
    with pytest.raises(topology.TopologyError, match="stale"):
        topology.validate_receipt(topology.canonical_json_bytes(receipt), rows=rows, foundation_run_id=run, foundation_head_sha=head)
    receipt = _receipt(foundation_run_id=run, foundation_head_sha=head, lane="native-capabilities", capability_or_authority_code=code, expected_node_ids=list(expected), collected_node_ids=list(expected))
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
    assert topology._external_preflight("EXT-PHASE3B-CORPUS", corpus_root=dangling)[0] == "PARTIAL"
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        partial = Path(raw)
        partial.chmod(0o700)
        assert topology._external_preflight("EXT-PHASE3B-CORPUS", corpus_root=partial)[0] == "PARTIAL"


def test_foundation_context_requires_current_github_run_and_checked_out_head(monkeypatch: pytest.MonkeyPatch) -> None:
    """Break caught: local defaults or another run/head can mint a receipt."""
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    with pytest.raises(topology.TopologyError, match="GitHub run"):
        topology.require_foundation_context("31641536482", "18f22198c65c7bc735aeb848d8fda55209d01e78")


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


def test_aggregate_rejects_duplicate_missing_and_unlisted_receipts(tmp_path: Path) -> None:
    """Break caught: a code receipt can be duplicated, omitted, or replaced by an unlisted node."""
    rows = topology.load_inventory(Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"))
    run, head = "31641536482", "18f22198c65c7bc735aeb848d8fda55209d01e78"
    paths: list[Path] = []
    for code in topology.CODE_CLASSIFICATION:
        lane, expected = topology._expected_rows(rows, code)
        state, outcome = {"portable-source": ("AVAILABLE", "PASS"), "native-capabilities": ("UNAVAILABLE", "DEFERRED"), "external-authorities": ("ABSENT", "DEFERRED")}[lane]
        receipt = _receipt(foundation_run_id=run, foundation_head_sha=head, lane=lane, capability_or_authority_code=code, expected_node_ids=list(expected), collected_node_ids=list(expected) if outcome == "PASS" else [], preflight_state=state, outcome=outcome)
        path = tmp_path / f"{code}.json"
        path.write_bytes(topology.canonical_json_bytes(receipt))
        paths.append(path)
    with pytest.raises(topology.TopologyError, match="receipt set"):
        topology.aggregate_receipts(paths[:-1] + [paths[0]], rows=rows, foundation_run_id=run, foundation_head_sha=head)
    altered = _receipt(foundation_run_id=run, foundation_head_sha=head, capability_or_authority_code="SRC-SEMANTIC-FIXTURE-IDENTITY", expected_node_ids=["tests/not-inventory.py::test_hidden"], collected_node_ids=["tests/not-inventory.py::test_hidden"])
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
    forbidden = _receipt(foundation_run_id=run, foundation_head_sha=head, lane=lane, capability_or_authority_code=code, expected_node_ids=list(expected), collected_node_ids=[], preflight_state="BROKEN", outcome="DEFERRED", redacted_fact_class="NATIVE_PROBE_INVALID")
    with pytest.raises(topology.TopologyError, match="state-to-lane"):
        topology.validate_receipt(topology.canonical_json_bytes(forbidden), rows=rows, foundation_run_id=run, foundation_head_sha=head)


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
    legacy = tmp_path / "legacy"
    for relative, _ in topology.LEGACY_CLOSURE_ENTRIES:
        _write_direct(legacy / relative, executable=relative.endswith("python"))
    assert topology._external_preflight("EXT-LEGACY-UV-AUTHORITY", uv_path=tmp_path / "missing-uv", legacy_root=legacy)[0] == "PARTIAL"
    corpus = tmp_path / "corpus"
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

    def exact(nodes: tuple[str, ...], _report: Path) -> tuple[str, ...]:
        selected.append(nodes)
        return nodes

    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "artifact"
        topology.reserve_topology_evidence(evidence, run_id="31641536482", head_sha=head)
        paths = topology.run_lane(
            lane="external-authorities", inventory=Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"),
            evidence_root=evidence, run_id="31641536482", head_sha=head,
            external_preflight=lambda _code: ("VALID", "AUTHORITY_COMPLETE_VALIDATED"), exact_runner=exact,
        )
        assert len(paths) == 2
    assert sorted(len(nodes) for nodes in selected) == [3, 3]


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
