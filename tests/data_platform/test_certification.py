from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_certification import certify_p2_data_platform


def test_p2_certification_is_repeatable_three_of_three(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)

    receipts = tuple(certify_p2_data_platform(store) for _ in range(3))

    assert receipts[0] == receipts[1] == receipts[2]
    assert receipts[0].query_parity is True
    assert receipts[0].pit_leakage_closed is True
    assert receipts[0].data_api_epoch == 2
    assert receipts[0].migration_head == "0019_p2_security_master"
    assert receipts[0].snapshot_sha256 != receipts[0].corrected_snapshot_sha256
    assert receipts[0].iceberg_enabled is False
    assert receipts[0].snapshot_row_count == 2
    assert len(receipts[0].receipt_sha256) == 64


def test_p2_certification_cli_emits_one_canonical_receipt() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/certify_p2_data_platform.py"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "p2-data-platform-certification-v2"
    assert payload["repetitions"] == 3
    assert len(payload["receipt_sha256"]) == 64
