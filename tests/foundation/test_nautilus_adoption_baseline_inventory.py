from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tomllib


ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / "docs/nautilus-adoption/baseline-inventory.json"


def _load_inventory() -> dict[str, object]:
    document = json.loads(INVENTORY.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    assert document["packet"] == "01A-baseline-inventory"
    return document


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_inventory_covers_each_component_dependency_authority() -> None:
    inventory = _load_inventory()
    components = inventory["component_dependency_authority"]
    assert isinstance(components, list)
    by_manifest = {entry["manifest"]: entry for entry in components}

    tracked_files = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split("\0")
    expected_python_manifests = {
        path for path in tracked_files if path.endswith("pyproject.toml")
    }
    assert expected_python_manifests <= set(by_manifest)
    assert "apps/dashboard/package.json" in by_manifest

    for manifest, entry in by_manifest.items():
        assert isinstance(entry, dict)
        manifest_path = ROOT / manifest
        lockfile_path = ROOT / entry["lockfile"]
        assert manifest_path.is_file(), manifest
        assert lockfile_path.is_file(), entry["lockfile"]
        assert entry["manifest_sha256"] == _sha256(manifest_path)
        assert entry["lockfile_sha256"] == _sha256(lockfile_path)


def test_root_manifest_inventory_covers_engine_cli_console_script() -> None:
    inventory = _load_inventory()
    components = inventory["component_dependency_authority"]
    assert isinstance(components, list)
    root_entries = [entry for entry in components if entry["manifest"] == "pyproject.toml"]
    assert len(root_entries) == 1
    root_entry = root_entries[0]
    assert isinstance(root_entry, dict)

    manifest_path = ROOT / "pyproject.toml"
    manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["project"]["scripts"]["trading-agent-nautilus"] == (
        "packages.nautilus_engine_cli.cli:main"
    )
    assert root_entry["manifest_sha256"] == _sha256(manifest_path)
    assert root_entry["lockfile"] == "uv.lock"
    assert root_entry["lockfile_sha256"] == _sha256(ROOT / "uv.lock")


def test_inventory_distinguishes_source_and_historical_migration_authorities() -> None:
    inventory = _load_inventory()
    alembic = inventory["alembic"]
    assert isinstance(alembic, dict)
    assert alembic["source_head"] == "0009_canonical_market_data"
    assert alembic["deployed_database_state"] == "not asserted by this source-only inventory"
    rows = alembic["authority_matrix"]
    assert isinstance(rows, list)
    by_revision = {row["revision"]: row for row in rows}
    assert set(by_revision) == {
        "0004_durable_research_jobs",
        "0006_job_transition_database_authority",
        "0007_job_event_chain_authority",
        "0008_trading_domain_ledger",
        "0009_canonical_market_data",
    }
    assert "NO_GO" in by_revision["0006_job_transition_database_authority"]["classification"]

    indirect_evidence = {
        "apps/job_api/config.py": "EXPECTED_REVISION = CANONICAL_DATABASE_REVISION",
    }
    for revision, row in by_revision.items():
        for relative in row["references"]:
            source = (ROOT / relative).read_text(encoding="utf-8")
            assert revision in source or indirect_evidence.get(relative) in source, (
                f"{relative} does not evidence {revision}"
            )


def test_inventory_covers_the_required_legacy_live_capable_archive_surface() -> None:
    inventory = _load_inventory()
    surfaces = inventory["legacy_live_capable_archive_surfaces"]
    assert isinstance(surfaces, list)
    paths = {
        item.split(":", 1)[0]
        for surface in surfaces
        for item in surface["paths"]
    }
    required = {
        "legacy/research-backend/live_execution_policy.py",
        "legacy/research-backend/kill_switch.py",
        "legacy/research-backend/execute_live.py",
        "legacy/research-backend/trading_agent.py",
        "legacy/research-backend/exchange/adapter.py",
        "legacy/research-backend/exchange/executor.py",
        "legacy/research-backend/exchange/ccxt_bridge.py",
        "legacy/research-backend/broker.py",
        "legacy/research-backend/asset_registry.py",
        "legacy/research-backend/exchange/secrets.py",
        "legacy/research-backend/runtime_paths.py",
    }
    assert required <= paths
    assert all(surface["classification"] == "ARCHIVE_ONLY" for surface in surfaces)
    adapter = (ROOT / "legacy/research-backend/exchange/adapter.py").read_text(encoding="utf-8")
    assert "create_oco_order" in adapter
