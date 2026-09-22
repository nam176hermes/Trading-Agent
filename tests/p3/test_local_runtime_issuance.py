"""The LOCAL issuer consumes the same Package6 validity as its runtime consumer."""
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path

import pytest

from scripts.p3_local_issue_runtime import _staged_material
from tests.foundation._package6_staging_fixture import package6_staging_lease
from tests.runtime_release.test_v2_runtime_config import _staging_authority


@pytest.mark.parametrize("fault", [None, "missing", "expired", "future", "too_long", "noncanonical"])
def test_issuer_reads_and_validates_package6_validity(
    tmp_path, monkeypatch, package6_staging_lease, fault
):
    stage, python, _ = _staging_authority(
        tmp_path, monkeypatch, lease=package6_staging_lease
    )
    path = package6_staging_lease.root / "authority/release-authority-v2.json"
    document = json.loads(path.read_bytes())
    now = datetime.now(UTC).replace(microsecond=0)
    timestamp = lambda value: value.strftime("%Y-%m-%dT%H:%M:%SZ")
    if fault == "missing":
        document.pop("validity")
        document["generated_at"] = timestamp(now)
        document["expires_at"] = timestamp(now + timedelta(minutes=10))
    elif fault == "expired":
        document["validity"] = {
            "generated_at_utc": timestamp(now - timedelta(minutes=10)),
            "expires_at_utc": timestamp(now - timedelta(seconds=1)),
        }
    elif fault == "future":
        document["validity"]["generated_at_utc"] = timestamp(now + timedelta(minutes=1))
    elif fault == "too_long":
        document["validity"]["expires_at_utc"] = timestamp(now + timedelta(minutes=31))
    elif fault == "noncanonical":
        document["validity"]["expires_at_utc"] = (now + timedelta(minutes=10)).isoformat()
    path.chmod(0o600)
    raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(raw)
    path.chmod(0o444)
    request = {
        "staging_authority_path": str(path),
        "application_root": str(stage / "application"),
        "application_python": str(python),
    }
    if fault:
        with pytest.raises(ValueError):
            _staged_material(request)
    else:
        material = _staged_material(request)
        assert material["authority_generated_at"] == document["validity"]["generated_at_utc"]
        assert material["authority_expires_at"] == document["validity"]["expires_at_utc"]
        assert material["static_authority_sha256"] == hashlib.sha256(raw).hexdigest()
        assert Path(material["application_root"]) == stage / "application"
