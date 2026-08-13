from __future__ import annotations

from datetime import datetime, timezone
import os
import pytest
from pathlib import Path
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
        receipt = _receipt(foundation_run_id=run, foundation_head_sha=head, lane=lane, capability_or_authority_code=code, expected_node_ids=list(node_ids), collected_node_ids=list(node_ids) if outcome == "PASS" else [], preflight_state=state, outcome=outcome)
        path = tmp_path / f"{code}.json"
        path.write_bytes(topology.canonical_json_bytes(receipt))
        paths.append(path)
    monkeypatch.setattr(topology, "validate_portable_closure_proof", lambda *_args, **_kwargs: {})
    summary = topology.aggregate_receipts(
        paths, rows=rows, foundation_run_id=run, foundation_head_sha=head,
        foundation_context={}, closure_proof_path=tmp_path / "closure-proof.json",
    )
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
        receipt = _receipt(foundation_run_id=run, foundation_head_sha=head, lane=lane, capability_or_authority_code=code, expected_node_ids=list(expected), collected_node_ids=list(expected) if outcome == "PASS" else [], preflight_state=state, outcome=outcome)
        path = tmp_path / f"{code}.json"
        path.write_bytes(topology.canonical_json_bytes(receipt))
        paths.append(path)
    monkeypatch.setattr(topology, "validate_portable_closure_proof", lambda *_args, **_kwargs: {})
    with pytest.raises(topology.TopologyError, match="receipt set"):
        topology.aggregate_receipts(
            paths[:-1] + [paths[0]], rows=rows, foundation_run_id=run,
            foundation_head_sha=head, foundation_context={},
            closure_proof_path=tmp_path / "closure-proof.json",
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
    monkeypatch.setattr(
        topology, "_native_preflight", lambda _code: ("UNAVAILABLE", "NATIVE_COMPONENT_ABSENT"),
    )
    with tempfile.TemporaryDirectory(dir="/tmp") as raw:
        evidence = Path(raw) / "evidence"
        topology.reserve_topology_evidence(evidence, run_id=run_id, head_sha=head)
        receipts = topology.run_lane(
            lane="native-capabilities",
            inventory=Path("tests/fixtures/t-g03a-hosted-failure-inventory.tsv"),
            evidence_root=evidence,
            run_id=run_id,
            head_sha=head,
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
