from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def make_boundary_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    route = root / "apps/dashboard/src/app/api/trading/mode/route.ts"
    route.parent.mkdir(parents=True)
    route.write_text(
        "import { writePrivateLocalStateFile } from '@/lib/trading/local-state';\n"
        "export function POST() { writePrivateLocalStateFile('.mode', 'paper\\n'); }\n",
        encoding="utf-8",
    )
    for path in (
        "packages/domain",
        "packages/engine_contracts",
        "packages/event_ledger",
        "packages/alpha_lifecycle",
        "services/paper_runtime",
        "apps/operator_cli",
        "apps/operator_api",
        "ops/systemd",
        "docs/implementation/hwc",
    ):
        (root / path).mkdir(parents=True, exist_ok=True)
    (root / "ops/systemd/core.service").write_text(
        "[Unit]\nDescription=core\n", encoding="utf-8"
    )
    inventory = {
        "schema_version": "hwc-authority-inventory-v1",
        "dashboard_routes": [
            {
                "route": "/mode",
                "classification": "TEMPORARY_GRANDFATHERED_STATE_WRITE",
                "canonical_owner": "DASHBOARD_COMPATIBILITY",
            }
        ],
    }
    inventory_path = root / "docs/implementation/hwc/hwc-authority-inventory-v1.json"
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "hwc@example.invalid")
    _git(root, "config", "user.name", "HWC Test")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "fixture")
    blob = _git(root, "rev-parse", "HEAD:apps/dashboard/src/app/api/trading/mode/route.ts")
    source_sha = hashlib.sha256(route.read_bytes()).hexdigest()
    policy = {
        "schema_version": "hwc-boundary-policy-v1",
        "route_inventory": "docs/implementation/hwc/hwc-authority-inventory-v1.json",
        "allowed_dashboard_local_write": "apps/dashboard/src/lib/trading/auth.ts",
        "grandfathered_state_writes": [
            {
                "path": "apps/dashboard/src/app/api/trading/mode/route.ts",
                "classification": "TEMPORARY_GRANDFATHERED_STATE_WRITE",
                "git_blob_sha": blob,
                "source_sha256": source_sha,
                "owner": "DASHBOARD_COMPATIBILITY",
                "authority": "REQUESTED_MODE_WRITE",
                "migration_task": "T-HWC-200A",
                "expires_at_gate": "HWC_DASHBOARD_AUTHORITY_REMOVED",
            }
        ],
        "python_interface_import_forbidden": [
            "packages/domain",
            "packages/engine_contracts",
            "packages/event_ledger",
            "packages/alpha_lifecycle",
            "services/paper_runtime",
        ],
        "operator_cli_forbidden_import_prefixes": [
            "apps.dashboard",
            "services",
            "engines.nautilus",
            "psycopg",
            "sqlalchemy",
        ],
        "dashboard_forbidden_patterns": ["node:child_process", "child_process"],
    }
    (root / "docs/implementation/hwc/hwc-boundary-policy-v1.json").write_text(
        json.dumps(policy), encoding="utf-8"
    )
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "policy")
    return root


def commit_all(root: Path, message: str = "mutation") -> None:
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", message)
