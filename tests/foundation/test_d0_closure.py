from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[2]
MATRIX = ROOT / "docs/implementation/d0-closure-matrix.json"
MAKEFILE = ROOT / "Makefile"
WORKFLOW = ROOT / ".github/workflows/foundation.yml"
PYPROJECT = ROOT / "pyproject.toml"
TRACK_C_PACKET_3_VERDICT = (
    ROOT / "docs/plans/track-c-p10-canonical-market-data/PACKET-3-VERDICT.md"
)

SOURCE_REQUIREMENTS = {
    "D0-PROPERTY-TESTS",
    "D0-CONTRACT-DRIFT",
    "D0.1-INVARIANTS",
    "D0.2-INVARIANTS",
    "D0-IMMUTABLE-SET-HASH",
    "D0-SNAPSHOT-SEMANTICS",
    "D0-OUTBOX-SEMANTICS",
    "D0-INBOX-SEMANTICS",
    "D0-END-TO-END-REPLAY",
    "D0-FULL-GATES",
}
RUNTIME_REQUIREMENT = "D0-POSTGRES-RUNTIME-PARITY"
REQUIRED_COMMANDS = {
    "make audit-release",
    "make check-contracts",
    "make test-all",
    "UV_CACHE_DIR=/home/thenam176/.cache/uv uv run pytest -q tests/runtime_release",
    "make build-dashboard",
    "make ci",
}


def _assert_no_blank(value: object, location: str = "matrix") -> None:
    if value is None or value == "" or value == [] or value == {}:
        raise AssertionError(f"blank closure evidence at {location}")
    if isinstance(value, dict):
        for key, nested in value.items():
            assert isinstance(key, str) and key, f"blank key at {location}"
            _assert_no_blank(nested, f"{location}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _assert_no_blank(nested, f"{location}[{index}]")


def _test_functions(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
    }


def _load_matrix() -> dict[str, Any]:
    document = json.loads(MATRIX.read_text(encoding="utf-8"))
    assert set(document) == {
        "schema_version",
        "phase",
        "source_readiness",
        "runtime_postgres_parity",
        "requirements",
        "final_proof_commands",
    }
    _assert_no_blank(document)
    return document


def test_d0_closure_matrix_has_no_blank_or_unresolved_source_requirement() -> None:
    document = _load_matrix()
    assert document["schema_version"] == 1
    assert document["phase"] == "D0"
    assert document["source_readiness"] == "PASS"
    assert document["runtime_postgres_parity"] in {"PASS", "PENDING_APPROVAL"}

    rows = document["requirements"]
    assert isinstance(rows, list)
    by_id = {row["id"]: row for row in rows}
    assert set(by_id) == SOURCE_REQUIREMENTS | {RUNTIME_REQUIREMENT}
    assert all(by_id[requirement]["status"] == "PASS" for requirement in SOURCE_REQUIREMENTS)
    runtime = by_id[RUNTIME_REQUIREMENT]
    assert runtime["status"] == document["runtime_postgres_parity"]
    assert runtime["status"] in {"PASS", "PENDING_APPROVAL"}
    if runtime["status"] == "PENDING_APPROVAL":
        assert runtime["approval_boundary"]
        assert runtime["command"] == "make test-event-ledger-runtime-postgres"


def test_d0_closure_matrix_references_existing_implementation_and_test_proofs() -> None:
    document = _load_matrix()
    for row in document["requirements"]:
        for relative in row["implementation"]:
            assert (ROOT / relative).exists(), f"missing implementation proof: {relative}"
        for proof in row["proofs"]:
            path = ROOT / proof["path"]
            assert path.is_file(), f"missing proof path: {proof['path']}"
            if "test" in proof:
                assert proof["test"] in _test_functions(path), (
                    f"missing executable proof: {proof['path']}::{proof['test']}"
                )


def test_property_contract_and_closure_gates_are_collected_by_ci() -> None:
    document = _load_matrix()
    makefile = MAKEFILE.read_text(encoding="utf-8")
    workflow = WORKFLOW.read_text(encoding="utf-8")
    pyproject = PYPROJECT.read_text(encoding="utf-8")
    property_source = (ROOT / "tests/domain/test_decimal_properties.py").read_text(
        encoding="utf-8"
    )
    replay_source = (ROOT / "tests/event_ledger/test_replay.py").read_text(
        encoding="utf-8"
    )

    assert '"hypothesis==6.151.9"' in pyproject
    assert "@given" in property_source
    assert "@given" in replay_source
    assert 'testpaths = ["tests"]' in pyproject
    assert (
        'uv run pytest $(ROOT_PYTEST_ARGS) -q '
        '-m "not runtime_postgres and not host_coupled" tests'
    ) in makefile
    assert 'addopts = "-s"' in pyproject
    assert "test-all-private: audit check-d0-closure check-contracts" in makefile
    assert "check-d0-closure:" in makefile
    assert "uv run pytest -q tests/foundation/test_d0_closure.py" in makefile
    assert "run: make ci-portable" in workflow
    assert "uses: actions/upload-artifact@v4" in workflow
    assert (
        "path: ${{ runner.temp }}/trading-agent-ci-portable-publication."
        "${{ github.run_id }}.${{ github.run_attempt }}/artifact/**"
    ) in workflow
    assert "ci-private:\n\t$(MAKE) prepare-root-test-install" in makefile
    assert "\t$(MAKE) test-all-private check-test-skips check-critical-coverage " in makefile
    assert set(document["final_proof_commands"]) == REQUIRED_COMMANDS


def test_portable_component_snapshot_proof_is_root_test_local() -> None:
    makefile = MAKEFILE.read_text(encoding="utf-8")

    assert "test-portable-embedded-proof" in makefile.split(".PHONY:", 1)[1].split("\n\n", 1)[0]
    assert (
        "test-portable-embedded-proof: ROOT_PYTEST_ARGS := --portable-embedded-proof\n"
        "test-portable-embedded-proof: test"
    ) in makefile
    assert (
        "test-all-private: audit check-d0-closure check-contracts check-secrets test "
        "test-backend test-dashboard typecheck-dashboard lint-dashboard"
    ) in makefile
    assert (
        "test-all-portable-private: audit-portable check-d0-closure check-contracts "
        "check-secrets test-portable-embedded-proof test-backend test-dashboard "
        "typecheck-dashboard lint-dashboard"
    ) in makefile
    assert makefile.count("$(ROOT_PYTEST_ARGS)") == 1
    assert "export PYTEST_ADDOPTS" not in makefile
    assert "test-backend:\n\tcd legacy/research-backend && uv run --frozen --extra test pytest -q" in makefile
    assert "test-dashboard:\n\tcd apps/dashboard && npm test" in makefile


def test_canonical_ci_is_an_alias_for_the_portable_gate() -> None:
    makefile = MAKEFILE.read_text(encoding="utf-8")

    assert "ci: ci-portable" in makefile
    assert "ci:\n\t" not in makefile


def test_portable_ci_uses_a_private_linux_temp_root() -> None:
    makefile = MAKEFILE.read_text(encoding="utf-8")
    portable_recipe = makefile.split("ci-portable:\n", 1)[1].split(
        "\n\nci-portable-private:", 1
    )[0]
    portable_private_recipe = makefile.split("\nci-portable-private:\n", 1)[1].split(
        "\n\nci-common-private:", 1
    )[0]

    assert "ci-portable:\n\t@set -eu;" in makefile
    assert portable_private_recipe == (
            "\t$(MAKE) ci-common-private ci-portable-topology "
            "check-portable-defect-closure check-p0-baseline check-p0-maintainability "
            "check-test-governance-topology check-p0-ci-closure "
        "artifact-firewall-check audit-delivery-contract"
    )
    assert "scripts.check_artifact_firewall publish-error" in makefile
    assert "ci-common-private:\n\t$(MAKE) prepare-root-test-install" in makefile
    assert (
        'ci_tmpdir=$$(mktemp -d "$${RUNNER_TEMP:?}/'
        'trading-agent-ci-portable.XXXXXXXXXX")'
        in portable_recipe
    )
    assert "/tmp/trading-agent-ci-portable.XXXXXXXXXX" not in portable_recipe
    assert 'chmod 0700 "$$ci_tmpdir"' in portable_recipe
    assert 'test "$$(stat -c \'%u:%a\' -- "$$ci_tmpdir")" = "$$(id -u):700"' in portable_recipe
    assert "cleanup_ci_tmpdir() {" in portable_recipe
    assert 'find -P "$$ci_tmpdir" -xdev -type d -exec chmod u+rwx -- {} +' in portable_recipe
    assert "trap 'cleanup_ci_tmpdir' EXIT" in portable_recipe
    assert portable_recipe.count(
        'TMPDIR="$$ci_tmpdir" TEMP="$$ci_tmpdir" TMP="$$ci_tmpdir"'
    ) == 1
    assert portable_recipe.count("$(MAKE) ci-portable-private") == 1


def test_portable_ci_rejects_an_appended_duplicate_private_route_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    makefile = MAKEFILE.read_text(encoding="utf-8")
    approved_route = (
        "\t$(MAKE) ci-common-private ci-portable-topology "
        "check-portable-defect-closure check-p0-baseline check-p0-maintainability "
        "check-p1-nautilus-boundaries check-p1-nautilus-lineage "
        "check-p1-nautilus-pin-inventory "
        "check-test-governance-topology check-p0-ci-closure "
        "artifact-firewall-check audit-delivery-contract"
    )
    assert makefile.count(approved_route) == 1
    mutated_makefile = tmp_path / "Makefile"
    mutated_makefile.write_text(
        makefile.replace(
            approved_route,
            f"{approved_route} check-p0-baseline",
            1,
        ),
        encoding="utf-8",
    )
    monkeypatch.setitem(globals(), "MAKEFILE", mutated_makefile)

    with pytest.raises(AssertionError):
        test_portable_ci_uses_a_private_linux_temp_root()


def test_portable_ci_uses_common_test_target_after_one_source_reinstall() -> None:
    makefile = MAKEFILE.read_text(encoding="utf-8")

    assert "$(MAKE) prepare-root-test-install" in makefile
    assert "$(MAKE) audit-portable check-d0-closure check-contracts check-secrets test-backend" in makefile
    common_private = makefile.split("ci-common-private:\n", 1)[1].split("\n\nartifact-firewall-check:", 1)[0]
    assert common_private.count("prepare-root-test-install") == 1


def test_test_all_rebuilds_the_current_root_package_and_uses_private_tmp() -> None:
    makefile = MAKEFILE.read_text(encoding="utf-8")

    assert "prepare-root-test-install:" in makefile
    assert "uv sync --frozen --reinstall-package trading-agent-control-api" in makefile
    assert "test-all-private:" in makefile
    assert "mktemp -d /tmp/trading-agent-test-all.XXXXXXXXXX" in makefile
    assert 'TMPDIR="$$test_tmpdir" TEMP="$$test_tmpdir" TMP="$$test_tmpdir"' in makefile


def test_test_all_reinstalls_before_private_validation_under_parallel_make() -> None:
    makefile = MAKEFILE.read_text(encoding="utf-8")
    recipe = makefile.split("test-all:\n", 1)[1].split("\n\nci:", 1)[0]

    assert (
        'TMPDIR="$$test_tmpdir" TEMP="$$test_tmpdir" TMP="$$test_tmpdir" \\\n'
        '\t\t\t$(MAKE) prepare-root-test-install; \\\n'
        '\t\tTMPDIR="$$test_tmpdir" TEMP="$$test_tmpdir" TMP="$$test_tmpdir" \\\n'
        '\t\t\t$(MAKE) test-all-private'
    ) in recipe
    assert "$(MAKE) prepare-root-test-install test-all-private" not in recipe


def test_track_c_source_head_defers_runtime_activation() -> None:
    verdict = TRACK_C_PACKET_3_VERDICT.read_text(encoding="utf-8")
    migration_0008 = (
        ROOT / "alembic/versions/0008_trading_domain_ledger.py"
    ).read_text(encoding="utf-8")
    migration_0009 = (
        ROOT / "alembic/versions/0009_canonical_market_data.py"
    ).read_text(encoding="utf-8")
    job_store_config = (ROOT / "services/job_store/config.py").read_text(
        encoding="utf-8"
    )
    job_api_config = (ROOT / "apps/job_api/config.py").read_text(encoding="utf-8")
    release_v2 = (ROOT / "packages/runtime_release/v2.py").read_text(
        encoding="utf-8"
    )
    stage_verifier = (ROOT / "ops/release-v2/verify-stage.py").read_text(
        encoding="utf-8"
    )
    systemd_example = (ROOT / "ops/systemd/job-api.env.example").read_text(
        encoding="utf-8"
    )

    assert "SOURCE_HEAD_0009_RUNTIME_ACTIVATION_DEFERRED" in verdict
    assert "NO_GO" in verdict
    assert 'revision = "0008_trading_domain_ledger"' in migration_0008
    assert 'down_revision = "0007_job_event_chain_authority"' in migration_0008
    assert 'revision = "0009_canonical_market_data"' in migration_0009
    assert 'down_revision = "0008_trading_domain_ledger"' in migration_0009
    assert (
        'CANONICAL_DATABASE_REVISION = "0011_engine_backtest_worker_authority"'
        in job_store_config
    )
    assert "EXPECTED_REVISION = CANONICAL_DATABASE_REVISION" in job_api_config
    assert (
        'EXPECTED_DATABASE_REVISION = "0011_engine_backtest_worker_authority"'
        in release_v2
    )
    assert (
        '_EXPECTED_DATABASE_REVISION = "0011_engine_backtest_worker_authority"'
        in stage_verifier
    )
    assert (
        "TRADING_JOB_API_EXPECTED_REVISION=0011_engine_backtest_worker_authority"
        in systemd_example
    )
