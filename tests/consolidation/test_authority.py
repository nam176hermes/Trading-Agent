from __future__ import annotations

from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

import pytest

from packages.consolidation.authority import (
    AuthorityError,
    load_source_authority,
    parse_source_authority,
)


ROOT = Path(__file__).resolve().parents[2]
AUTHORITY_PATH = ROOT / "ops/consolidation/source-authority.json"


@pytest.fixture
def tmp_path() -> Path:
    """Keep Git-heavy fixtures on the Linux filesystem under WSL."""

    with tempfile.TemporaryDirectory(prefix="consolidation-authority-", dir="/tmp") as directory:
        yield Path(directory)


def _git(repository: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()


def _repository(path: Path, files: dict[str, bytes] | None = None) -> tuple[str, str]:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "Consolidation Test")
    _git(path, "config", "user.email", "consolidation@example.invalid")
    for relative, content in (files or {"README.md": b"fixture\n"}).items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "fixture")
    commit = _git(path, "rev-parse", "HEAD").decode()
    tree = _git(path, "rev-parse", "HEAD^{tree}").decode()
    return commit, tree


def _document(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    repositories: dict[str, tuple[Path, str, str]] = {}
    for name in ("core", "backend", "dashboard"):
        repository = tmp_path / name
        files = (
            {"trading-agent/package.json": b"{}\n"}
            if name == "dashboard"
            else {"README.md": f"{name}\n".encode()}
        )
        commit, root_tree = _repository(repository, files)
        tree = (
            _git(repository, "rev-parse", f"{commit}:trading-agent").decode()
            if name == "dashboard"
            else root_tree
        )
        repositories[name] = (repository, commit, tree)

    data: dict[str, object] = {
        "schema_version": 1,
        "sealed_phase4b_metadata_sha256": "a" * 64,
        "components": {
            "core": {
                "repository": str(repositories["core"][0]),
                "commit": repositories["core"][1],
                "tree": repositories["core"][2],
                "source_prefix": ".",
                "destination_prefix": ".",
            },
            "backend": {
                "repository": str(repositories["backend"][0]),
                "commit": repositories["backend"][1],
                "tree": repositories["backend"][2],
                "source_prefix": ".",
                "destination_prefix": "legacy/research-backend",
            },
            "dashboard": {
                "repository": str(repositories["dashboard"][0]),
                "commit": repositories["dashboard"][1],
                "tree": repositories["dashboard"][2],
                "source_prefix": "trading-agent",
                "destination_prefix": "apps/dashboard",
            },
        },
    }
    document = tmp_path / "authority.json"
    document.write_text(json.dumps(data), encoding="utf-8")
    return document, data


def _rewrite(path: Path, data: dict[str, object]) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


def test_loads_exact_task_1_authority_as_immutable_values() -> None:
    authority = parse_source_authority(AUTHORITY_PATH)

    assert authority.schema_version == 1
    assert authority.sealed_phase4b_metadata_sha256 == (
        "f1ed595c86df4cc7ac7274272dd00798ddb497a98ca3a12a1ed9680769468d7c"
    )
    assert tuple(authority.components) == ("core", "backend", "dashboard")
    assert authority.components["core"].commit == (
        "d9d46fa363f26bd78f5560300d26913494e11e4d"
    )
    assert authority.components["dashboard"].source_prefix.as_posix() == "trading-agent"
    with pytest.raises(TypeError):
        authority.components["other"] = authority.components["core"]  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        authority.schema_version = 2  # type: ignore[misc]


def test_schema_only_parser_is_portable_but_strict_loader_requires_git_objects(
    tmp_path: Path,
) -> None:
    path, data = _document(tmp_path)
    for component in data["components"].values():  # type: ignore[union-attr]
        shutil.rmtree(Path(component["repository"]))

    authority = parse_source_authority(path)

    assert tuple(authority.components) == ("core", "backend", "dashboard")
    with pytest.raises(AuthorityError) as raised:
        load_source_authority(path)
    assert raised.value.reason_code == "AUTHORITY_GIT_OBJECT_INVALID"


@pytest.mark.parametrize("scope", ["root", "component"])
@pytest.mark.parametrize("operation", ["missing", "unknown"])
def test_rejects_non_exact_key_sets(
    tmp_path: Path, scope: str, operation: str,
) -> None:
    path, data = _document(tmp_path)
    target = data if scope == "root" else data["components"]["backend"]  # type: ignore[index]
    key = "schema_version" if scope == "root" else "tree"
    if operation == "missing":
        target.pop(key)  # type: ignore[union-attr]
    else:
        target["unexpected"] = "value"  # type: ignore[index]
    _rewrite(path, data)

    with pytest.raises(AuthorityError) as raised:
        load_source_authority(path)
    assert raised.value.reason_code == "AUTHORITY_SCHEMA_INVALID"
    assert str(tmp_path) not in str(raised.value)


def test_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    path = tmp_path / "authority.json"
    path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")

    with pytest.raises(AuthorityError) as raised:
        load_source_authority(path)
    assert raised.value.reason_code == "AUTHORITY_JSON_INVALID"


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("schema_version", 2, "AUTHORITY_SCHEMA_UNSUPPORTED"),
        ("sealed_phase4b_metadata_sha256", "A" * 64, "AUTHORITY_DIGEST_INVALID"),
    ],
)
def test_rejects_unsupported_schema_and_invalid_digest(
    tmp_path: Path, field: str, value: object, reason: str,
) -> None:
    path, data = _document(tmp_path)
    data[field] = value
    _rewrite(path, data)

    with pytest.raises(AuthorityError) as raised:
        load_source_authority(path)
    assert raised.value.reason_code == reason


@pytest.mark.parametrize("field", ["commit", "tree"])
@pytest.mark.parametrize("value", ["f" * 39, "F" * 40, "g" * 40, 7])
def test_rejects_invalid_git_object_ids(
    tmp_path: Path, field: str, value: object,
) -> None:
    path, data = _document(tmp_path)
    data["components"]["backend"][field] = value  # type: ignore[index]
    _rewrite(path, data)

    with pytest.raises(AuthorityError) as raised:
        load_source_authority(path)
    assert raised.value.reason_code == "AUTHORITY_GIT_ID_INVALID"


def test_rejects_non_absolute_repository(tmp_path: Path) -> None:
    path, data = _document(tmp_path)
    data["components"]["backend"]["repository"] = "relative/repository"  # type: ignore[index]
    _rewrite(path, data)

    with pytest.raises(AuthorityError) as raised:
        load_source_authority(path)
    assert raised.value.reason_code == "AUTHORITY_REPOSITORY_INVALID"


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("name", "AUTHORITY_COMPONENTS_INVALID"),
        ("source", "AUTHORITY_PREFIX_INVALID"),
        ("destination", "AUTHORITY_PREFIX_INVALID"),
    ],
)
def test_rejects_wrong_component_names_and_prefixes(
    tmp_path: Path, mutation: str, reason: str,
) -> None:
    path, data = _document(tmp_path)
    components = data["components"]  # type: ignore[assignment]
    if mutation == "name":
        components["renamed"] = components.pop("backend")
    elif mutation == "source":
        components["backend"]["source_prefix"] = "research"
    else:
        components["dashboard"]["destination_prefix"] = "dashboard"
    _rewrite(path, data)

    with pytest.raises(AuthorityError) as raised:
        load_source_authority(path)
    assert raised.value.reason_code == reason


def test_rejects_source_tree_mismatch(tmp_path: Path) -> None:
    path, data = _document(tmp_path)
    data["components"]["backend"]["tree"] = data["components"]["core"]["tree"]  # type: ignore[index]
    _rewrite(path, data)

    with pytest.raises(AuthorityError) as raised:
        load_source_authority(path)
    assert raised.value.reason_code == "AUTHORITY_TREE_MISMATCH"


def test_rejects_repository_replaced_after_document_creation(tmp_path: Path) -> None:
    path, data = _document(tmp_path)
    repository = Path(data["components"]["backend"]["repository"])  # type: ignore[index,arg-type]
    shutil.rmtree(repository)
    _repository(repository, {"other.txt": b"different\n"})

    with pytest.raises(AuthorityError) as raised:
        load_source_authority(path)
    assert raised.value.reason_code == "AUTHORITY_GIT_OBJECT_INVALID"
    assert str(repository) not in str(raised.value)


def test_authority_ignores_git_commit_replacement_refs(tmp_path: Path) -> None:
    path, data = _document(tmp_path)
    backend = data["components"]["backend"]  # type: ignore[index]
    repository = Path(backend["repository"])
    original_commit = backend["commit"]
    (repository / "README.md").write_bytes(b"replacement commit\n")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-qm", "replacement")
    replacement_commit = _git(repository, "rev-parse", "HEAD").decode()
    _git(repository, "replace", original_commit, replacement_commit)

    authority = load_source_authority(path)

    assert authority.components["backend"].commit == original_commit
    assert authority.components["backend"].tree == backend["tree"]


def test_authority_git_calls_are_bounded_noninteractive_and_replace_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, _ = _document(tmp_path)
    original_run = subprocess.run
    observed: list[dict[str, object]] = []

    def recording_run(arguments: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        if isinstance(arguments, list) and arguments[0] == "/usr/bin/git":
            observed.append(kwargs)
        return original_run(arguments, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(subprocess, "run", recording_run)
    load_source_authority(path)

    assert observed
    for kwargs in observed:
        assert kwargs["stdin"] is subprocess.DEVNULL
        assert kwargs["timeout"] == 30
        assert kwargs["env"]["GIT_NO_REPLACE_OBJECTS"] == "1"  # type: ignore[index]


def test_authority_git_timeout_is_sanitized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, _ = _document(tmp_path)

    def timeout(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(cmd="git", timeout=30)

    monkeypatch.setattr(subprocess, "run", timeout)

    with pytest.raises(AuthorityError) as raised:
        load_source_authority(path)
    assert raised.value.reason_code == "AUTHORITY_GIT_OBJECT_INVALID"
    assert str(tmp_path) not in str(raised.value)
