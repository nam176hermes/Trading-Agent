from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from packages.engine_contracts import canonical_json_bytes
from services.paper_runtime.nautilus_recovery import (
    NAUTILUS_RECOVERY_RECEIPT_SCHEMA,
    load_nautilus_recovery_receipt,
    write_nautilus_recovery_receipt,
)

from test_nautilus_reconciliation import _evidence


def test_recovery_receipt_is_canonical_generation_bound_and_no_clobber(
    tmp_path: Path,
) -> None:
    path = tmp_path / "recovery.json"
    receipt = write_nautilus_recovery_receipt(path, _evidence())
    raw = path.read_bytes()
    document = json.loads(raw)

    assert raw == canonical_json_bytes(document) + b"\n"
    assert document["schema"] == NAUTILUS_RECOVERY_RECEIPT_SCHEMA
    assert document["verdict"] == "RESUME_EXACT_PREFIX"
    assert document["engine_version"] == "1.231.0"
    assert document["closure_digest"] == _evidence().closure_digest
    assert document["authority_limits"] == {
        "live_authorized": False,
        "network_query_allowed": False,
        "production_authorized": False,
    }
    assert receipt.receipt_sha256 == hashlib.sha256(raw).hexdigest()
    assert load_nautilus_recovery_receipt(path) == receipt

    with pytest.raises(FileExistsError):
        write_nautilus_recovery_receipt(path, _evidence())
    assert path.read_bytes() == raw


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(schema="wrong"),
        lambda value: value.update(verdict="START_NEW"),
        lambda value: value.update(engine_version="1.227.0"),
        lambda value: value.update(closure_digest="f" * 64),
        lambda value: value.update(evidence_sha256="F" * 64),
        lambda value: value.update(extra=True),
        lambda value: value["authority_limits"].update(network_query_allowed=True),
    ],
)
def test_loader_rejects_changed_or_moving_recovery_authority(
    tmp_path: Path, mutation: object
) -> None:
    path = tmp_path / "recovery.json"
    write_nautilus_recovery_receipt(path, _evidence())
    document = json.loads(path.read_bytes())
    mutation(document)  # type: ignore[operator]
    path.write_bytes(canonical_json_bytes(document) + b"\n")

    with pytest.raises(ValueError):
        load_nautilus_recovery_receipt(path)


def test_receipt_path_must_be_a_new_regular_file_in_an_existing_directory(
    tmp_path: Path,
) -> None:
    with pytest.raises(FileNotFoundError):
        write_nautilus_recovery_receipt(tmp_path / "missing" / "receipt.json", _evidence())

    target = tmp_path / "target.json"
    target.write_bytes(b"foreign")
    link = tmp_path / "receipt.json"
    link.symlink_to(target)
    with pytest.raises(FileExistsError):
        write_nautilus_recovery_receipt(link, _evidence())
    assert target.read_bytes() == b"foreign"
