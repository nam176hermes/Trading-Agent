from __future__ import annotations

import json
from pathlib import Path
import stat
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "docs/implementation/p0-maintainability-hotspots.json"
SCHEMA_VERSION = "p0-maintainability-hotspots/v1"
EXPECTED_BASELINE_SHA = "e0baa410cdcf0de4344d58ad82fd8a56788f84df"
FROZEN_FOR_GROWTH = "FROZEN_FOR_GROWTH"
MONITOR = "MONITOR"
EXPECTED_HOTSPOTS = [
    {
        "path": "scripts/t_g03_capability_topology.py",
        "status": FROZEN_FOR_GROWTH,
        "baseline_bytes": 362662,
        "max_net_growth_bytes": 16430,
        "responsibility_id": "P0_CAPABILITY_TOPOLOGY",
        "baseline_first_party_imports": [
            "scripts.check_test_governance",
            "scripts.materialize_nautilus_runtime_closure",
            "scripts.materialize_sealed_uv_exec",
            "scripts.prepare_nautilus_llvm_toolchain",
            "scripts.prepare_nautilus_toolchain",
            "scripts.validate_disposable_postgres_approval",
            "scripts.validate_disposable_postgres_fixture_plan",
            "trading_control.phase3b_sources",
        ],
    },
    {
        "path": "scripts/check_artifact_firewall.py",
        "status": FROZEN_FOR_GROWTH,
        "baseline_bytes": 141810,
        "max_net_growth_bytes": 0,
        "responsibility_id": "P0_ARTIFACT_FIREWALL",
        "baseline_first_party_imports": [
            "scripts.check_test_governance",
            "scripts.t_g03_capability_topology",
        ],
    },
    {
        "path": "scripts/check_p0_ci_closure.py",
        "status": MONITOR,
        "baseline_bytes": 43300,
        "responsibility_id": "P0_CLOSURE_CHECKER",
        "baseline_first_party_imports": [
            "scripts.check_artifact_firewall",
            "scripts.t_g03_capability_topology",
        ],
    },
]

CHECKER = ROOT / "scripts/check_p0_maintainability.py"
CHARACTERIZATION_INDEX = (
    ROOT / "docs/implementation/p0-m1-characterization-index.json"
)
CHARACTERIZATION_SCHEMA_VERSION = "p0-m1-characterization-index/v1"
EXPECTED_CHARACTERIZATION_BASELINE_SHA = "e0baa410cdcf0de4344d58ad82fd8a56788f84df"
REQUIRED_C05_FAILURE_NODE = (
    "tests/governance/test_t_g03_portable_defect_closure.py::"
    "test_closure_proof_rejects_failure_stale_artifact_and_tampering"
)
REQUIRED_CHARACTERIZATIONS = {
    "C01": "final semantic projection ignores run-custody-only identity",
    "C02": "semantic projection changes when governed meaning changes",
    "C03": "sealed foundation validation date is authoritative",
    "C04": "CLI/env date override fails closed",
    "C05": "portable defect remains fail-closed",
    "C06": "native absent authority is deferred, not passed",
    "C07": "native present-invalid authority fails",
    "C08": "external absent authority is deferred",
    "C09": "external present-invalid authority fails",
    "C10": "native evidence publication is append-only/no rollback",
    "C11": "canonical acceptance is no-clobber/create-if-absent",
    "C12": "wrong head/context/inventory binding fails",
    "C13": "artifact manifest/checksum mismatch fails",
    "C14": "symlink/path substitution fails",
    "C15": "secret-bearing evidence fails",
    "C16": "portable lane cannot imply host qualification",
}
P1_BOUNDARY_PROOF_NODES = (
    "tests/governance/test_p0_m1_p1_boundary.py::"
    "test_make_graph_rejects_bare_make_executable",
    "tests/governance/test_p0_m1_p1_boundary.py::"
    "test_make_graph_rejects_command_variable_after_time_option",
    "tests/governance/test_p0_m1_p1_boundary.py::"
    "test_make_graph_rejects_literal_make_command_alias",
    "tests/governance/test_p0_m1_p1_boundary.py::"
    "test_make_graph_rejects_make_derived_command_alias",
    "tests/governance/test_p0_m1_p1_boundary.py::"
    "test_make_graph_rejects_make_function_derived_command_alias",
    "tests/governance/test_p0_m1_p1_boundary.py::"
    "test_make_graph_rejects_multiword_shell_prefix_alias",
    "tests/governance/test_p0_m1_p1_boundary.py::"
    "test_make_graph_rejects_one_character_make_command_alias",
    "tests/governance/test_p0_m1_p1_boundary.py::"
    "test_make_graph_rejects_unassigned_command_variable",
    "tests/governance/test_p0_m1_p1_boundary.py::"
    "test_make_graph_rejects_variable_indirected_recursive_target",
    "tests/governance/test_p0_m1_p1_boundary.py::"
    "test_make_graph_traverses_same_root_dash_c_target",
    "tests/governance/test_p0_m1_p1_boundary.py::"
    "test_portable_make_graph_is_literal_and_cannot_reach_host_authority",
    "tests/test_p0_ci_closure.py::"
    "test_pending_source_matrix_is_an_executable_closed_contract",
)


def _run_checker(root: Path, manifest: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "python",
            str(CHECKER),
            "--root",
            str(root),
            "--manifest",
            str(manifest),
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _write_fixture_manifest(
    root: Path,
    *,
    status: str = FROZEN_FOR_GROWTH,
    path: str = "scripts/hotspot.py",
    baseline_bytes: int = 4,
    max_net_growth_bytes: int | None = 0,
    baseline_first_party_imports: list[str] | None = None,
) -> Path:
    hotspot: dict[str, object] = {
        "path": path,
        "status": status,
        "baseline_bytes": baseline_bytes,
        "responsibility_id": "P0_FIXTURE",
        "baseline_first_party_imports": baseline_first_party_imports or [],
    }
    if max_net_growth_bytes is not None:
        hotspot["max_net_growth_bytes"] = max_net_growth_bytes
    manifest = root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "baseline_sha": "HEAD",
                "hotspots": [hotspot],
            }
        ),
        encoding="utf-8",
    )
    return manifest


@pytest.fixture
def checker_repo(tmp_path: Path) -> Path:
    """A real Git repository with a committed four-byte hotspot baseline."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "p0@example.invalid")
    _git(root, "config", "user.name", "P0 Fixture")
    hotspot = root / "scripts/hotspot.py"
    hotspot.parent.mkdir()
    hotspot.write_bytes(b"pass")
    _git(root, "add", "scripts/hotspot.py")
    _git(root, "commit", "-qm", "baseline")
    return root


def test_p0_maintainability_hotspot_inventory_is_a_strict_custody_manifest() -> None:
    """Reject policy changes that make a hotspot untracked, ambiguous, or unsafe."""
    document = json.loads(MANIFEST.read_text(encoding="utf-8"))

    assert set(document) == {"schema_version", "baseline_sha", "hotspots"}
    assert document["schema_version"] == SCHEMA_VERSION
    assert isinstance(document["baseline_sha"], str)
    assert document["baseline_sha"] == EXPECTED_BASELINE_SHA
    assert subprocess.run(
        ["git", "cat-file", "-e", f"{document['baseline_sha']}^{{commit}}"],
        cwd=ROOT,
        check=False,
    ).returncode == 0
    assert subprocess.run(
        ["git", "merge-base", "--is-ancestor", document["baseline_sha"], "HEAD"],
        cwd=ROOT,
        check=False,
    ).returncode == 0

    hotspots = document["hotspots"]
    assert isinstance(hotspots, list)
    assert hotspots == EXPECTED_HOTSPOTS
    paths: set[str] = set()
    for hotspot in hotspots:
        assert isinstance(hotspot, dict)
        status = hotspot.get("status")
        expected_keys = {
            "path",
            "status",
            "baseline_bytes",
            "responsibility_id",
            "baseline_first_party_imports",
        }
        if status == FROZEN_FOR_GROWTH:
            expected_keys.add("max_net_growth_bytes")
        assert set(hotspot) == expected_keys
        assert status in {FROZEN_FOR_GROWTH, MONITOR}

        path = hotspot["path"]
        assert isinstance(path, str)
        assert path not in paths
        paths.add(path)
        candidate = ROOT / path
        assert candidate.resolve().is_relative_to(ROOT.resolve())
        assert candidate.exists()
        assert not candidate.is_symlink()
        assert stat.S_ISREG(candidate.stat().st_mode)

        assert type(hotspot["baseline_bytes"]) is int
        assert hotspot["baseline_bytes"] > 0
        baseline_size = subprocess.run(
            ["git", "cat-file", "-s", f"{document['baseline_sha']}:{path}"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        assert baseline_size.returncode == 0
        assert int(baseline_size.stdout) == hotspot["baseline_bytes"]
        assert isinstance(hotspot["responsibility_id"], str)
        assert hotspot["responsibility_id"]
        assert isinstance(hotspot["baseline_first_party_imports"], list)
        assert hotspot["baseline_first_party_imports"] == sorted(
            hotspot["baseline_first_party_imports"]
        )
        assert all(
            isinstance(import_name, str) and import_name
            for import_name in hotspot["baseline_first_party_imports"]
        )
        if status == FROZEN_FOR_GROWTH:
            assert type(hotspot["max_net_growth_bytes"]) is int
            assert hotspot["max_net_growth_bytes"] >= 0


def test_checker_rejects_missing_manifest(checker_repo: Path) -> None:
    """Removing the reviewed policy must prevent the guard from running."""
    result = _run_checker(checker_repo, checker_repo / "missing.json")
    assert result.returncode != 0
    assert "manifest" in result.stderr.lower()


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda document: document.update(unexpected=True), "unknown"),
        (lambda document: document["hotspots"].append(document["hotspots"][0].copy()), "duplicate"),
        (lambda document: document["hotspots"][0].update(path="/tmp/hotspot.py"), "absolute"),
        (lambda document: document["hotspots"][0].update(path="../hotspot.py"), "traversal"),
        (lambda document: document.update(baseline_sha="does-not-exist"), "baseline"),
    ],
)
def test_checker_rejects_invalid_manifest_contract(
    checker_repo: Path, change: object, expected: str
) -> None:
    """Malformed policy data must not weaken hotspot custody or baseline proof."""
    manifest = _write_fixture_manifest(checker_repo)
    document = json.loads(manifest.read_text(encoding="utf-8"))
    change(document)  # type: ignore[operator]
    manifest.write_text(json.dumps(document), encoding="utf-8")
    result = _run_checker(checker_repo, manifest)
    assert result.returncode != 0
    assert expected in result.stderr.lower()


def test_checker_rejects_baseline_object_path_absent(checker_repo: Path) -> None:
    """A current file absent from the pinned Git baseline cannot establish custody."""
    current_only = checker_repo / "scripts/current_only.py"
    current_only.write_bytes(b"pass")
    manifest = _write_fixture_manifest(checker_repo, path="scripts/current_only.py")
    result = _run_checker(checker_repo, manifest)
    assert result.returncode != 0
    assert "baseline" in result.stderr.lower()


def test_checker_rejects_import_baseline_not_derived_from_pinned_blob(
    checker_repo: Path,
) -> None:
    """A manifest cannot silently grandfather a dependency absent from its Git blob."""
    manifest = _write_fixture_manifest(
        checker_repo, baseline_first_party_imports=["services.market_data"]
    )
    result = _run_checker(checker_repo, manifest)
    assert result.returncode != 0
    assert "baseline first-party imports" in result.stderr.lower()


def test_checker_rejects_symlink_hotspot(checker_repo: Path) -> None:
    """A symlink can switch the checked source after policy review and is forbidden."""
    target = checker_repo / "scripts/target.py"
    target.write_bytes(b"pass")
    hotspot = checker_repo / "scripts/hotspot.py"
    hotspot.unlink()
    hotspot.symlink_to(target.name)
    result = _run_checker(checker_repo, _write_fixture_manifest(checker_repo))
    assert result.returncode != 0
    assert "symlink" in result.stderr.lower()


def test_checker_rejects_symlinked_parent_hotspot(checker_repo: Path) -> None:
    """Every logical path component must be a real directory, not an alias."""
    linked_hotspot = checker_repo / "linked/hotspot.py"
    linked_hotspot.parent.mkdir()
    linked_hotspot.write_bytes(b"pass")
    _git(checker_repo, "add", "linked/hotspot.py")
    _git(checker_repo, "commit", "-qm", "linked baseline")
    linked_hotspot.unlink()
    linked_hotspot.parent.rmdir()
    linked_hotspot.parent.symlink_to("scripts", target_is_directory=True)
    manifest = _write_fixture_manifest(checker_repo, path="linked/hotspot.py")
    result = _run_checker(checker_repo, manifest)
    assert result.returncode != 0
    assert "symlink" in result.stderr.lower()


def test_checker_rejects_non_regular_hotspot(checker_repo: Path) -> None:
    """Directories do not have a stable source-byte meaning and must be rejected."""
    hotspot = checker_repo / "scripts/hotspot.py"
    hotspot.unlink()
    hotspot.mkdir()
    result = _run_checker(checker_repo, _write_fixture_manifest(checker_repo))
    assert result.returncode != 0
    assert "regular" in result.stderr.lower()


@pytest.mark.parametrize(
    ("contents", "returncode"),
    [(b"pass", 0), (b"pas", 0), (b"pass!", 1)],
)
def test_checker_enforces_frozen_net_growth(
    checker_repo: Path, contents: bytes, returncode: int
) -> None:
    """A frozen hotspot may shrink but cannot exceed its approved byte ceiling."""
    (checker_repo / "scripts/hotspot.py").write_bytes(contents)
    result = _run_checker(checker_repo, _write_fixture_manifest(checker_repo))
    assert result.returncode == returncode
    assert "current_bytes=" in result.stderr
    assert "baseline_bytes=" in result.stderr
    assert "delta_bytes=" in result.stderr
    assert "status=FROZEN_FOR_GROWTH" in result.stderr


def test_checker_reports_monitor_growth_without_failing(checker_repo: Path) -> None:
    """MONITOR hotspots expose growth for review without imposing a size ceiling."""
    (checker_repo / "scripts/hotspot.py").write_bytes(b"pass\n#grow")
    manifest = _write_fixture_manifest(checker_repo, status=MONITOR, max_net_growth_bytes=None)
    result = _run_checker(checker_repo, manifest)
    assert result.returncode == 0
    assert result.stdout == "P0_MAINTAINABILITY_GUARD_PASS\n"
    assert "current_bytes=10" in result.stderr
    assert "baseline_bytes=4" in result.stderr
    assert "delta_bytes=6" in result.stderr
    assert "status=MONITOR" in result.stderr


@pytest.mark.parametrize("status", [FROZEN_FOR_GROWTH, MONITOR])
def test_checker_rejects_same_size_first_party_import_drift(
    checker_repo: Path, status: str
) -> None:
    """Status only controls size ceilings; new first-party coupling is always rejected."""
    hotspot = checker_repo / "scripts/hotspot.py"
    hotspot.write_bytes(b"import sys #\n")
    _git(checker_repo, "add", "scripts/hotspot.py")
    _git(checker_repo, "commit", "-qm", "import baseline")
    hotspot.write_bytes(b"import ops #\n")
    manifest = _write_fixture_manifest(
        checker_repo,
        status=status,
        baseline_bytes=13,
        max_net_growth_bytes=0 if status == FROZEN_FOR_GROWTH else None,
    )
    result = _run_checker(checker_repo, manifest)
    assert result.returncode != 0
    assert "first-party import drift" in result.stderr


@pytest.mark.parametrize(
    "import_name",
    [
        "services.market_data",
        "services.quant_research",
        "services.agent_reasoning",
        "packages.portfolio_strategy",
        "engines.nautilus.runtime",
    ],
)
def test_checker_rejects_new_frozen_runtime_import_without_review(
    checker_repo: Path, import_name: str
) -> None:
    """Frozen topology code cannot acquire runtime-domain dependencies unreviewed."""
    hotspot = checker_repo / "scripts/hotspot.py"
    hotspot.write_bytes(b"import sys #\n")
    _git(checker_repo, "add", "scripts/hotspot.py")
    _git(checker_repo, "commit", "-qm", "stdlib import baseline")
    hotspot.write_text(f"import {import_name}\n", encoding="utf-8")
    manifest = _write_fixture_manifest(
        checker_repo,
        baseline_bytes=13,
        max_net_growth_bytes=100,
    )
    result = _run_checker(checker_repo, manifest)
    assert result.returncode != 0
    assert "first-party import drift" in result.stderr
    assert import_name in result.stderr


def test_checker_rejects_unlisted_repository_package_import_without_review(
    checker_repo: Path,
) -> None:
    """A local package discovered from repository layout cannot bypass review."""
    package = checker_repo / "apps/control_api/local_authority"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "runtime.py").write_text("", encoding="utf-8")
    _git(checker_repo, "add", "apps/control_api/local_authority")
    _git(checker_repo, "commit", "-qm", "local package layout")
    hotspot = checker_repo / "scripts/hotspot.py"
    hotspot.write_text("import local_authority.runtime\n", encoding="utf-8")
    manifest = _write_fixture_manifest(checker_repo, max_net_growth_bytes=100)
    result = _run_checker(checker_repo, manifest)
    assert result.returncode != 0
    assert "first-party import drift" in result.stderr
    assert "local_authority.runtime" in result.stderr


def test_checker_rejects_new_from_import_module_without_review(checker_repo: Path) -> None:
    """A `from scripts import module` dependency is pinned at module granularity."""
    scripts = checker_repo / "scripts"
    (scripts / "reviewed_module.py").write_text("", encoding="utf-8")
    (scripts / "authority_module.py").write_text("", encoding="utf-8")
    hotspot = scripts / "hotspot.py"
    hotspot.write_text("from scripts import reviewed_module\n", encoding="utf-8")
    _git(checker_repo, "add", "scripts")
    _git(checker_repo, "commit", "-qm", "from import baseline")
    hotspot.write_text("from scripts import authority_module\n", encoding="utf-8")
    manifest = _write_fixture_manifest(
        checker_repo,
        baseline_bytes=len(b"from scripts import reviewed_module\n"),
        max_net_growth_bytes=100,
        baseline_first_party_imports=["scripts.reviewed_module"],
    )
    result = _run_checker(checker_repo, manifest)
    assert result.returncode != 0
    assert "first-party import drift" in result.stderr
    assert "scripts.authority_module" in result.stderr


def test_checker_rejects_new_from_import_nested_package_without_review(
    checker_repo: Path,
) -> None:
    """A nested source-root package pins `from` imports at module granularity."""
    package = checker_repo / "apps/control_api/control_api"
    repositories = package / "repositories"
    repositories.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (repositories / "__init__.py").write_text("", encoding="utf-8")
    (package / "authority.py").write_text("", encoding="utf-8")
    hotspot = checker_repo / "scripts/hotspot.py"
    hotspot.write_text("from control_api import repositories\n", encoding="utf-8")
    _git(checker_repo, "add", "apps/control_api/control_api", "scripts/hotspot.py")
    _git(checker_repo, "commit", "-qm", "nested package from import baseline")
    hotspot.write_text("from control_api import authority\n", encoding="utf-8")
    manifest = _write_fixture_manifest(
        checker_repo,
        baseline_bytes=len(b"from control_api import repositories\n"),
        max_net_growth_bytes=100,
        baseline_first_party_imports=["control_api.repositories"],
    )
    result = _run_checker(checker_repo, manifest)
    assert result.returncode != 0
    assert "first-party import drift" in result.stderr
    assert "control_api.authority" in result.stderr


def test_checker_has_no_automatic_policy_rewrite_mode() -> None:
    """Policy updates remain source-reviewed instead of accepting local drift by CLI."""
    result = subprocess.run(
        ["python", str(CHECKER), "--help"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0
    assert "--update" not in result.stdout
    assert "--accept-current" not in result.stdout


def test_p0_characterization_index_pins_collected_non_xfail_exact_nodes() -> None:
    """A missing, stale, wildcard, or xfail-only proof cannot satisfy a P0 contract."""
    document = json.loads(CHARACTERIZATION_INDEX.read_text(encoding="utf-8"))

    assert set(document) == {"schema_version", "baseline_sha", "contracts"}
    assert document["schema_version"] == CHARACTERIZATION_SCHEMA_VERSION
    assert document["baseline_sha"] == EXPECTED_CHARACTERIZATION_BASELINE_SHA
    assert subprocess.run(
        ["git", "cat-file", "-e", f"{document['baseline_sha']}^{{commit}}"],
        cwd=ROOT,
        check=False,
    ).returncode == 0
    assert subprocess.run(
        ["git", "merge-base", "--is-ancestor", document["baseline_sha"], "HEAD"],
        cwd=ROOT,
        check=False,
    ).returncode == 0

    contracts = document["contracts"]
    assert isinstance(contracts, list)
    ids = [contract["id"] for contract in contracts]
    assert ids == list(REQUIRED_CHARACTERIZATIONS)
    assert len(ids) == len(set(ids))
    c05 = next(contract for contract in contracts if contract["id"] == "C05")
    assert REQUIRED_C05_FAILURE_NODE in c05["test_node_ids"]

    all_nodes: list[str] = []
    for contract in contracts:
        assert set(contract) == {"id", "description", "test_node_ids"}
        assert contract["description"] == REQUIRED_CHARACTERIZATIONS[contract["id"]]
        nodes = contract["test_node_ids"]
        assert isinstance(nodes, list) and nodes
        assert nodes == sorted(set(nodes))
        for node in nodes:
            assert isinstance(node, str) and node.startswith("tests/")
            assert "::" in node and not any(character in node for character in "*?\n\r\t ")
            source = ROOT / node.split("::", 1)[0]
            assert source.is_file() and not source.is_symlink()
        all_nodes.extend(nodes)

    unique_nodes = list(dict.fromkeys(all_nodes))
    collected = subprocess.run(
        ["uv", "run", "pytest", "--collect-only", "-q", "--", *unique_nodes],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert collected.returncode == 0, collected.stdout + collected.stderr
    collected_nodes = {
        line for line in collected.stdout.splitlines() if line.startswith("tests/")
    }
    assert set(unique_nodes) <= collected_nodes

    xfail_collection = subprocess.run(
        [
            "uv", "run", "pytest", "--collect-only", "-q", "-m", "xfail",
            "--", *unique_nodes,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert xfail_collection.returncode in {0, 5}
    xfail_nodes = {
        line for line in xfail_collection.stdout.splitlines() if line.startswith("tests/")
    }
    for contract in contracts:
        assert not set(contract["test_node_ids"]) <= xfail_nodes


def test_checker_prints_only_unique_validated_characterization_nodes() -> None:
    """The execution helper must emit safe pytest arguments without duplicate work."""
    document = json.loads(CHARACTERIZATION_INDEX.read_text(encoding="utf-8"))
    expected = list(dict.fromkeys(
        node
        for contract in document["contracts"]
        for node in contract["test_node_ids"]
    ))
    result = subprocess.run(
        [
            "python", str(CHECKER),
            "--manifest", str(MANIFEST),
            "--characterization-index", str(CHARACTERIZATION_INDEX),
            "--print-characterization-nodes",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == expected


def test_p1_boundary_reuses_exact_executable_authority_proofs() -> None:
    """Portable CI cannot reach host authority or materialize enabled live gates."""
    result = subprocess.run(
        ["uv", "run", "pytest", "-q", "--", *P1_BOUNDARY_PROOF_NODES],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (lambda document: document.update(unexpected=True), "unknown"),
        (
            lambda document: document["contracts"].append(
                document["contracts"][0].copy()
            ),
            "duplicate",
        ),
        (lambda document: document["contracts"].pop(), "required"),
        (
            lambda document: document.update(
                baseline_sha="bed2af3dd048086766f329870c3b0384fa44959e"
            ),
            "required frozen baseline",
        ),
        (
            lambda document: document["contracts"][4].update(
                test_node_ids=[
                    "tests/governance/test_t_g03_portable_defect_closure.py::"
                    "test_portable_source_defects_are_closed_not_unresolved"
                ]
            ),
            "c05 behavioral proof",
        ),
        (
            lambda document: document["contracts"][0].update(test_node_ids=[]),
            "non-empty",
        ),
        (
            lambda document: document["contracts"][0].update(
                test_node_ids=["tests/**/*.py::test_placeholder"]
            ),
            "wildcard",
        ),
        (
            lambda document: document["contracts"][0].update(
                test_node_ids=["tests/missing.py::test_missing"]
            ),
            "regular file",
        ),
    ],
)
def test_checker_rejects_invalid_characterization_index(
    tmp_path: Path, change: object, expected: str,
) -> None:
    """Malformed index data cannot become executable pytest arguments."""
    document = json.loads(CHARACTERIZATION_INDEX.read_text(encoding="utf-8"))
    change(document)  # type: ignore[operator]
    index = tmp_path / "characterization-index.json"
    index.write_text(json.dumps(document), encoding="utf-8")
    result = subprocess.run(
        [
            "python", str(CHECKER),
            "--root", str(ROOT),
            "--manifest", str(MANIFEST),
            "--characterization-index", str(index),
            "--print-characterization-nodes",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert expected in result.stderr.lower()
