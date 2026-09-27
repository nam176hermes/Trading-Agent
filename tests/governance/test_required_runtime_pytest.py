"""CT20: the required-runtime wrapper must reject a selection with any skip, at collection or at execution."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

import pytest

WRAPPER = Path(__file__).resolve().parents[2] / "scripts/run_required_runtime_pytest.py"
PASSING = "def test_passes():\n    assert True\n"


def _run_wrapper(tmp_path: Path, modules: dict[str, str], *selection: str) -> subprocess.CompletedProcess[str]:
    """Run the real wrapper as a subprocess over a task-owned suite with its own config (no repo plugins)."""
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    for name, body in modules.items():
        (tmp_path / name).write_text(body, encoding="utf-8")
    environment = {**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(
        [sys.executable, str(WRAPPER), "-p", "no:cacheprovider", "--color=no", *(selection or tuple(modules))],
        cwd=tmp_path, env=environment, text=True, capture_output=True, check=False,
    )


def test_required_runtime_rejects_module_collection_skip(tmp_path: Path) -> None:
    result = _run_wrapper(tmp_path, {
        "test_pass.py": PASSING,
        "test_skipped_module.py": 'import pytest\npytest.skip("runtime unavailable", allow_module_level=True)\n',
    })

    assert "1 passed, 1 skipped" in result.stdout      # pytest itself reports success
    assert result.returncode != 0
    assert "required runtime tests skipped: 1" in result.stderr


def test_required_runtime_rejects_runtime_skip(tmp_path: Path) -> None:
    result = _run_wrapper(tmp_path, {
        "test_pass.py": PASSING,
        "test_runtime_skip.py": 'import pytest\ndef test_skips():\n    pytest.skip("no database")\n',
    })

    assert result.returncode != 0
    assert "required runtime tests skipped: 1" in result.stderr


@pytest.mark.parametrize(("modules", "selection"), [
    ({"test_broken.py": "import missing_module_for_ct20\n"}, ()),        # collection error
    ({"test_pass.py": PASSING}, ("test_pass.py::test_absent",)),          # nothing selected
])
def test_required_runtime_preserves_pytest_failures(tmp_path: Path, modules: dict[str, str],
                                                    selection: tuple[str, ...]) -> None:
    assert _run_wrapper(tmp_path, modules, *selection).returncode != 0


def test_required_runtime_accepts_complete_passing_selection(tmp_path: Path) -> None:
    result = _run_wrapper(tmp_path, {"test_pass.py": PASSING, "test_other.py": PASSING})

    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout
