from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "scripts" / "check_broad_handler_inventory.py"
START = "<!-- P9_BROAD_HANDLER_INVENTORY_START -->\n```text\n"
END = "\n```\n<!-- P9_BROAD_HANDLER_INVENTORY_END -->"


def _document(rows: str) -> str:
    return f"# inventory\n\n{START}{rows}{END}\n\n# trailing text\n"


def _source() -> str:
    return """def boundary():
    try:
        work()
    except Exception:
        return
"""


def _row(line: int = 4) -> str:
    return f"src/boundary.py|{line}|Exception|PRODUCTION_CRITICAL|RETURN"


def _tracked_repository(tmp_path: Path, document: str | None = None) -> tuple[Path, Path, Path]:
    repository = tmp_path / "repository"
    source = repository / "src" / "boundary.py"
    inventory = repository / "docs" / "inventory.md"
    source.parent.mkdir(parents=True)
    inventory.parent.mkdir(parents=True)
    source.write_text(_source())
    inventory.write_text(_document(_row()) if document is None else document)
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(["git", "-C", str(repository), "add", "src/boundary.py", "docs/inventory.md"], check=True)
    return repository, source, inventory


def _run(repository: Path, inventory: Path, operation: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(TOOL),
            "--root",
            str(repository),
            "--inventory",
            str(inventory),
            operation,
        ],
        text=True,
        capture_output=True,
        check=False,
    )


def _inventory_parts(document: str) -> tuple[str, str, str]:
    prefix, remainder = document.split(START, 1)
    rows, suffix = remainder.split(END, 1)
    return prefix, rows, suffix


def test_check_fails_when_a_tracked_handler_line_moves(tmp_path: Path) -> None:
    repository, source, inventory = _tracked_repository(tmp_path)
    before = inventory.read_bytes()
    source.write_text("\n" + _source())

    result = _run(repository, inventory, "--check")

    assert result.returncode == 1
    assert "inventory drift" in result.stderr
    assert inventory.read_bytes() == before


def test_check_fails_when_a_tracked_broad_handler_is_added(tmp_path: Path) -> None:
    repository, source, inventory = _tracked_repository(tmp_path)
    before = inventory.read_bytes()
    source.write_text(_source() + "\ntry:\n    work()\nexcept BaseException:\n    pass\n")

    result = _run(repository, inventory, "--check")

    assert result.returncode == 1
    assert "inventory drift" in result.stderr
    assert inventory.read_bytes() == before


def test_check_recognizes_qualified_builtins_broad_exception_types(tmp_path: Path) -> None:
    document = _document("src/boundary.py|6|builtins.BaseException|PRODUCTION_CRITICAL|RETURN")
    repository, source, inventory = _tracked_repository(tmp_path, document)
    source.write_text(
        "import builtins\n\n"
        "def boundary():\n"
        "    try:\n"
        "        work()\n"
        "    except builtins.BaseException:\n"
        "        return\n"
    )

    result = _run(repository, inventory, "--check")

    assert result.returncode == 0, result.stderr


def test_handler_disposition_ignores_markers_in_nested_scopes(tmp_path: Path) -> None:
    document = _document("src/boundary.py|4|Exception|PRODUCTION_CRITICAL|OTHER")
    repository, source, inventory = _tracked_repository(tmp_path, document)
    source.write_text(
        "def boundary():\n"
        "    try:\n"
        "        work()\n"
        "    except Exception:\n"
        "        def nested():\n"
        "            return\n"
        "        work()\n"
    )

    result = _run(repository, inventory, "--check")

    assert result.returncode == 0, result.stderr


def test_write_updates_only_the_marked_inventory_block(tmp_path: Path) -> None:
    repository, _source_path, inventory = _tracked_repository(tmp_path, _document("stale|1|Exception|TESTS|OTHER"))
    before_prefix, _before_rows, before_suffix = _inventory_parts(inventory.read_text())

    result = _run(repository, inventory, "--write")

    assert result.returncode == 0, result.stderr
    after_prefix, after_rows, after_suffix = _inventory_parts(inventory.read_text())
    assert after_prefix == before_prefix
    assert after_suffix == before_suffix
    assert after_rows == _row()


def test_two_write_runs_produce_identical_inventory_bytes(tmp_path: Path) -> None:
    repository, _source_path, inventory = _tracked_repository(tmp_path, _document("stale|1|Exception|TESTS|OTHER"))

    first = _run(repository, inventory, "--write")
    first_bytes = inventory.read_bytes()
    second = _run(repository, inventory, "--write")

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert inventory.read_bytes() == first_bytes


@pytest.mark.parametrize(
    "document",
    [
        "# inventory\n\n" + START + "broken",
        _document(_row()) + START + "duplicate",
        _document(_row()) + END,
    ],
    ids=("missing-end", "duplicate-start", "duplicate-end"),
)
def test_malformed_or_duplicate_markers_fail_closed_without_writing(
    tmp_path: Path,
    document: str,
) -> None:
    repository, _source_path, inventory = _tracked_repository(tmp_path, document)
    before = inventory.read_bytes()

    result = _run(repository, inventory, "--write")

    assert result.returncode == 1
    assert "inventory markers" in result.stderr
    assert inventory.read_bytes() == before


def test_scanner_ignores_untracked_python_files(tmp_path: Path) -> None:
    repository, _source_path, inventory = _tracked_repository(tmp_path)
    (repository / "untracked.py").write_text("try:\n    work()\nexcept BaseException:\n    pass\n")

    result = _run(repository, inventory, "--check")

    assert result.returncode == 0, result.stderr
