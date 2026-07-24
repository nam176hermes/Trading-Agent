import os
from pathlib import Path

from test_api import build_data_root, client_for


def snapshot(root: Path) -> dict[str, tuple[int, int]]:
    return {
        str(path.relative_to(root)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }


def test_all_get_requests_leave_legacy_sources_unchanged(tmp_path) -> None:
    build_data_root(tmp_path)
    client = client_for(tmp_path)
    before = snapshot(tmp_path)

    for path in (
        "/health/live",
        "/health/ready",
        "/v1/meta",
        "/v1/system/status",
        "/v1/market/latest",
        "/v1/signals",
        "/v1/decisions",
        "/v1/capabilities",
        "/v1/costs",
    ):
        response = client.get(path)
        assert response.status_code == 200

    assert snapshot(tmp_path) == before
    assert os.listdir(tmp_path / "reports") == ["report_fixture.json"]
