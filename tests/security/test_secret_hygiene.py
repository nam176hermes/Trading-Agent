from __future__ import annotations

import ast
from collections.abc import Iterator
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCANNER = ROOT / "scripts" / "verify_secret_hygiene.py"
PROVIDER_MODULES = {
    ROOT / "legacy/research-backend/news_collector.py": ("MARKETAUX_API_KEY",),
    ROOT / "legacy/research-backend/twelve_data.py": ("TWELVE_DATA_API_KEY",),
    ROOT / "legacy/research-backend/fallback.py": (
        "FINNHUB_API_KEY",
        "POLYGON_API_KEY",
    ),
}


@pytest.fixture
def linux_tmp_path() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="secret-hygiene-test-", dir="/tmp") as directory:
        yield Path(directory)


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(["git", "-C", str(repository), *arguments], check=True, capture_output=True)


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    return repository


def _track(repository: Path, relative: str, content: str) -> None:
    target = repository / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(repository, "add", "--", relative)


def _stage_regular_index_entry(repository: Path, relative: str) -> None:
    blob = subprocess.run(
        ["git", "-C", str(repository), "hash-object", "-w", "--stdin"],
        input=b"safe index content\n",
        capture_output=True,
        check=True,
    ).stdout.decode("ascii").strip()
    _git(repository, "update-index", "--add", "--cacheinfo", f"100644,{blob},{relative}")


def _run(repository: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCANNER), "--root", str(repository)],
        text=True,
        capture_output=True,
        check=False,
    )


def test_secret_hygiene_accepts_clean_tracked_tree(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _track(repository, "app.py", "API_URL = 'https://example.invalid'\n")

    result = _run(repository)

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_secret_hygiene_accepts_shell_reference_credential_assignment(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _track(repository, "script.sh", 'local secret="$2"\n')

    result = _run(repository)

    assert result.returncode == 0
    assert result.stderr == ""


def test_secret_hygiene_rejects_shell_default_with_literal_credential(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    marker = "synthetic-shell-fallback-secret"
    credential_name = "pass" + "word"
    content = credential_name + '="${TOKEN:-' + marker + '}"\n'
    _track(repository, "script.sh", content)

    result = _run(repository)

    assert result.returncode == 1
    assert "script.sh:1:literal-credential-assignment" in result.stderr
    assert marker not in result.stderr


def test_secret_hygiene_rejects_tracked_provider_token_without_printing_value(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    marker = "sk-" + "z" * 20
    _track(repository, "app.py", f"VALUE = '{marker}'\n")

    result = _run(repository)

    assert result.returncode == 1
    assert "app.py:1:provider-token" in result.stderr
    assert marker not in result.stderr


@pytest.mark.parametrize(
    ("relative", "payload_kind", "expected_rule"),
    [
        ("README.md", "provider", "provider-token"),
        ("notes.txt", "uri", "credential-uri"),
        (".pypirc", "assignment", "literal-credential-assignment"),
        ("credentials", "private-key", "private-key"),
    ],
)
def test_secret_hygiene_scans_tracked_documentation_text_and_credential_formats(
    tmp_path: Path,
    relative: str,
    payload_kind: str,
    expected_rule: str,
) -> None:
    repository = _repository(tmp_path)
    if payload_kind == "provider":
        marker = "sk-" + "r" * 20
        content = f"credential canary: {marker}\n"
    elif payload_kind == "uri":
        marker = "postgresql://" + "reader:secret@example.invalid/database"
        content = f"connection: {marker}\n"
    elif payload_kind == "assignment":
        marker = "synthetic-format-secret"
        content = "pass" + "word = " + repr(marker) + "\n"
    else:
        marker = "-----" + "BEGIN PRIVATE KEY" + "-----"
        content = marker + "\n"
    _track(repository, relative, content)

    result = _run(repository)

    assert result.returncode == 1
    assert f"{relative}:1:{expected_rule}" in result.stderr
    assert marker not in result.stderr


def test_secret_hygiene_rejects_provider_token_copied_into_test_source(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    marker = "sk-" + "a" * 20
    _track(repository, "tests/security/test_secret_hygiene.py", f"VALUE = '{marker}'\n")

    result = _run(repository)

    assert result.returncode == 1
    assert "tests/security/test_secret_hygiene.py:1:provider-token" in result.stderr
    assert marker not in result.stderr


def test_secret_hygiene_rejects_literal_credential_in_test_source(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    marker = "synthetic-" + "password-value"
    credential_name = "PASS" + "WORD"
    _track(repository, "tests/ordinary.py", credential_name + " = " + repr(marker) + "\n")

    result = _run(repository)

    assert result.returncode == 1
    assert "tests/ordinary.py:1:literal-credential-assignment" in result.stderr
    assert marker not in result.stderr


@pytest.mark.parametrize("credential_name", ["FINNHUB_KEY", "POLYGON_KEY"])
def test_secret_hygiene_rejects_provider_specific_key_assignment(
    tmp_path: Path,
    credential_name: str,
) -> None:
    repository = _repository(tmp_path)
    marker = "synthetic-provider-credential"
    _track(repository, "provider.py", f"{credential_name} = {marker!r}\n")

    result = _run(repository)

    assert result.returncode == 1
    assert "provider.py:1:literal-credential-assignment" in result.stderr
    assert marker not in result.stderr


def test_secret_hygiene_rejects_credential_uri_in_test_source(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    marker = "postgresql://" + "user:password@example.invalid/database"
    _track(repository, "tests/ordinary.py", "VALUE = " + repr(marker) + "\n")

    result = _run(repository)

    assert result.returncode == 1
    assert "tests/ordinary.py:1:credential-uri" in result.stderr
    assert marker not in result.stderr


def test_secret_hygiene_rejects_tracked_env_provider_token(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    marker = "sk-" + "b" * 20
    _track(repository, ".env.production", f"API_KEY='{marker}'\n")

    result = _run(repository)

    assert result.returncode == 1
    assert ".env.production:1:provider-token" in result.stderr
    assert marker not in result.stderr


def test_secret_hygiene_rejects_ignored_untracked_env_provider_token(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _track(repository, ".gitignore", ".env\n.env.*\n")
    marker = "sk-" + "i" * 20
    (repository / ".env").write_text(f"API_KEY='{marker}'\n", encoding="utf-8")

    result = _run(repository)

    assert result.returncode == 1
    assert ".env:1:provider-token" in result.stderr
    assert marker not in result.stderr


def test_secret_hygiene_rejects_untracked_literal_credential_without_printing_value(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _track(repository, "app.py", "VALUE = 'clean'\n")
    marker = "synthetic-untracked-credential"
    (repository / "config.py").write_text(f"API_KEY = '{marker}'\n", encoding="utf-8")

    result = _run(repository)

    assert result.returncode == 1
    assert "config.py:1:literal-credential-assignment" in result.stderr
    assert marker not in result.stderr


def test_secret_hygiene_rejects_symlink_candidate(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _track(repository, "app.py", "VALUE = 'clean'\n")
    (repository / "linked.py").symlink_to("app.py")
    _git(repository, "add", "linked.py")

    result = _run(repository)

    assert result.returncode == 2
    assert "linked.py" in result.stderr
    assert "VALUE =" not in result.stderr


def test_secret_hygiene_rejects_symlinked_parent_without_reading_outside(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _track(repository, ".gitignore", "/linked\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "app.py").write_text("VALUE = 'clean'\n", encoding="utf-8")
    (repository / "linked").symlink_to(outside, target_is_directory=True)
    _stage_regular_index_entry(repository, "linked/app.py")

    result = _run(repository)

    assert result.returncode == 2
    assert "linked/app.py" in result.stderr


def test_secret_hygiene_rejects_nonregular_candidate(linux_tmp_path: Path) -> None:
    repository = _repository(linux_tmp_path)
    _track(repository, "app.py", "VALUE = 'clean'\n")
    fifo = repository / "unsafe.py"
    os.mkfifo(fifo)
    _stage_regular_index_entry(repository, "unsafe.py")

    result = _run(repository)

    assert result.returncode == 2
    assert "unsafe.py" in result.stderr


def test_current_provider_modules_use_environment_keys_without_literal_assignments() -> None:
    for provider, environment_keys in PROVIDER_MODULES.items():
        source = provider.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(provider))
        for environment_key in environment_keys:
            assert f'os.environ.get("{environment_key}")' in source
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = {
                    target.id.lower()
                    for target in targets
                    if isinstance(target, ast.Name)
                }
                assert not (
                    names & {"api_key", "apikey", "api_token", "token", "secret", "key"}
                    and isinstance(value, ast.Constant)
                    and isinstance(value.value, str)
                    and value.value
                ), provider


def test_current_tree_passes_secret_hygiene_scan() -> None:
    result = _run(ROOT)

    assert result.returncode == 0, result.stderr
