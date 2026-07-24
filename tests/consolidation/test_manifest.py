from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile

import pytest

import packages.consolidation.manifest as manifest_module
from packages.consolidation.authority import ComponentAuthority
from packages.consolidation.manifest import (
    ComponentManifest,
    ImportPolicy,
    ManifestEntry,
    ManifestError,
    canonical_manifest_bytes,
    propose_manifest,
    verify_manifest_source,
)


@pytest.fixture
def tmp_path() -> Path:
    """Keep Git-heavy fixtures on the Linux filesystem under WSL."""

    with tempfile.TemporaryDirectory(prefix="consolidation-manifest-", dir="/tmp") as directory:
        yield Path(directory)


def _git(repository: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        input=input_bytes,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout.strip()


def _init_repository(path: Path) -> None:
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.name", "Consolidation Test")
    _git(path, "config", "user.email", "consolidation@example.invalid")


def _commit_files(
    path: Path,
    files: dict[str, bytes],
    *,
    executable: tuple[str, ...] = (),
) -> None:
    for relative, content in files.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    _git(path, "add", ".")
    for relative in executable:
        _git(path, "update-index", "--chmod=+x", "--", relative)
    _git(path, "commit", "-qm", "fixture")


def _authority(
    repository: Path,
    *,
    source_prefix: str = ".",
    destination_prefix: str = "imported",
) -> ComponentAuthority:
    commit = _git(repository, "rev-parse", "HEAD").decode()
    tree_spec = "HEAD^{tree}" if source_prefix == "." else f"HEAD:{source_prefix}"
    tree = _git(repository, "rev-parse", tree_spec).decode()
    return ComponentAuthority(
        name="fixture",
        repository=repository,
        commit=commit,
        tree=tree,
        source_prefix=PurePosixPath(source_prefix),
        destination_prefix=PurePosixPath(destination_prefix),
    )


def _policy(**overrides: object) -> ImportPolicy:
    values: dict[str, object] = {
        "name": "fixture-regular-files-v1",
        "include_patterns": ("**",),
        "exclude_patterns": (),
        "forbidden_patterns": (),
    }
    values.update(overrides)
    return ImportPolicy(**values)  # type: ignore[arg-type]


def _fixture_manifest(tmp_path: Path) -> ComponentManifest:
    repository = tmp_path / "source"
    _init_repository(repository)
    _commit_files(repository, {"file.txt": b"fixture\n"})
    return propose_manifest(_authority(repository), _policy())


def _with_entries(
    manifest: ComponentManifest,
    entries: tuple[ManifestEntry, ...],
) -> ComponentManifest:
    entries_payload = [{
        "source_path": entry.source_path,
        "destination_path": entry.destination_path,
        "git_blob": entry.git_blob,
        "size": entry.size,
        "mode": entry.mode,
        "sha256": entry.sha256,
    } for entry in entries]
    aggregate_sha256 = hashlib.sha256(json.dumps(
        entries_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    return replace(manifest, entries=entries, aggregate_sha256=aggregate_sha256)


def test_manifest_is_immutable_canonical_and_deterministic(tmp_path: Path) -> None:
    repository = tmp_path / "source"
    _init_repository(repository)
    _commit_files(
        repository,
        {
            "z-last.bin": b"\x00\xffbinary\n",
            "A-first.txt": b"first\n",
            "unicode-\N{LATIN SMALL LETTER E WITH ACUTE}.txt": b"unicode\n",
            "script.sh": b"#!/bin/sh\nexit 0\n",
        },
        executable=("script.sh",),
    )
    authority = _authority(repository)

    first = propose_manifest(authority, _policy())
    second = propose_manifest(authority, _policy())
    first_bytes = canonical_manifest_bytes(first)
    payload = json.loads(first_bytes)

    assert first == second
    assert first_bytes == canonical_manifest_bytes(second)
    assert first_bytes.endswith(b"\n") and not first_bytes.endswith(b"\n\n")
    assert set(payload) == {
        "schema_version", "component", "source_repository", "source_commit",
        "source_tree", "source_prefix", "destination_prefix", "policy",
        "entries", "aggregate_sha256",
    }
    assert all(set(entry) == {
        "source_path", "destination_path", "git_blob", "size", "mode", "sha256",
    } for entry in payload["entries"])
    destinations = [entry["destination_path"] for entry in payload["entries"]]
    assert destinations == sorted(destinations, key=lambda value: value.encode("utf-8"))
    assert {entry["mode"] for entry in payload["entries"]} == {"100644", "100755"}
    assert next(entry for entry in payload["entries"] if entry["source_path"] == "z-last.bin")[
        "sha256"
    ] == hashlib.sha256(b"\x00\xffbinary\n").hexdigest()
    entries_bytes = json.dumps(
        payload["entries"], ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    assert payload["aggregate_sha256"] == hashlib.sha256(entries_bytes).hexdigest()
    with pytest.raises(FrozenInstanceError):
        first.component = "changed"  # type: ignore[misc]


def test_non_utf8_blob_contents_are_hashed_without_decoding(tmp_path: Path) -> None:
    repository = tmp_path / "source"
    _init_repository(repository)
    content = bytes(range(256))
    _commit_files(repository, {"opaque.dat": content})

    manifest = propose_manifest(_authority(repository), _policy())

    assert manifest.entries[0].size == len(content)
    assert manifest.entries[0].sha256 == hashlib.sha256(content).hexdigest()


def test_subtree_source_prefix_is_stripped_only_at_destination(tmp_path: Path) -> None:
    repository = tmp_path / "source"
    _init_repository(repository)
    _commit_files(
        repository,
        {
            "trading-agent/package.json": b"{}\n",
            "outside-secret.txt": b"outside selected tree\n",
        },
    )

    manifest = propose_manifest(
        _authority(
            repository,
            source_prefix="trading-agent",
            destination_prefix="apps/dashboard",
        ),
        _policy(),
    )

    assert [entry.source_path for entry in manifest.entries] == [
        "trading-agent/package.json",
    ]
    assert [entry.destination_path for entry in manifest.entries] == [
        "apps/dashboard/package.json",
    ]


def test_proposal_never_observes_modified_source_worktree(tmp_path: Path) -> None:
    repository = tmp_path / "source"
    _init_repository(repository)
    _commit_files(repository, {"tracked.txt": b"committed\n"})
    authority = _authority(repository)
    approved = propose_manifest(authority, _policy())

    (repository / "tracked.txt").write_bytes(b"modified worktree\n")
    (repository / "untracked-secret.txt").write_bytes(b"must not be read\n")

    observed = propose_manifest(authority, _policy())
    assert canonical_manifest_bytes(observed) == canonical_manifest_bytes(approved)
    assert observed.entries[0].sha256 == hashlib.sha256(b"committed\n").hexdigest()


@pytest.mark.parametrize("replacement_kind", ["blob", "tree"])
def test_proposal_ignores_git_replacement_refs(
    tmp_path: Path, replacement_kind: str,
) -> None:
    repository = tmp_path / "source"
    _init_repository(repository)
    original_content = b"approved object\n"
    _commit_files(repository, {"approved.txt": original_content})
    authority = _authority(repository)
    original_blob = _git(repository, "rev-parse", "HEAD:approved.txt").decode()

    if replacement_kind == "blob":
        replacement = _git(
            repository,
            "hash-object", "-w", "--stdin",
            input_bytes=b"replacement blob\n",
        ).decode()
        _git(repository, "replace", original_blob, replacement)
    else:
        (repository / "approved.txt").unlink()
        (repository / "replacement.txt").write_bytes(b"replacement tree\n")
        _git(repository, "add", "-A")
        _git(repository, "commit", "-qm", "replacement tree")
        replacement = _git(repository, "rev-parse", "HEAD^{tree}").decode()
        _git(repository, "replace", authority.tree, replacement)

    manifest = propose_manifest(authority, _policy())

    assert [entry.source_path for entry in manifest.entries] == ["approved.txt"]
    assert manifest.entries[0].git_blob == original_blob
    assert manifest.entries[0].sha256 == hashlib.sha256(original_content).hexdigest()


def test_rejects_cat_file_bytes_that_do_not_match_declared_blob(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "source"
    _init_repository(repository)
    _commit_files(repository, {"approved.txt": b"approved\n"})
    authority = _authority(repository)
    original_git_output = manifest_module._git_output

    def substituted_output(
        repository: Path,
        arguments: list[str],
        *,
        reason_code: str,
        path: str | None = None,
    ) -> bytes:
        if arguments[:2] == ["cat-file", "blob"]:
            return b"substituted bytes\n"
        return original_git_output(
            repository, arguments, reason_code=reason_code, path=path,
        )

    monkeypatch.setattr(manifest_module, "_git_output", substituted_output)

    with pytest.raises(ManifestError) as raised:
        propose_manifest(authority, _policy())
    assert raised.value.reason_code == "MANIFEST_GIT_OBJECT_INVALID"
    assert raised.value.path == "approved.txt"
    assert str(repository) not in str(raised.value)


def test_fixed_manifest_survives_head_advancing_but_rejects_commit_tamper(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "source"
    _init_repository(repository)
    _commit_files(repository, {"tracked.txt": b"first\n"})
    manifest = propose_manifest(_authority(repository), _policy())

    (repository / "tracked.txt").write_bytes(b"second\n")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-qm", "advance")

    verify_manifest_source(manifest)
    changed_commit = _git(repository, "rev-parse", "HEAD").decode()
    tampered = replace(manifest, source_commit=changed_commit)
    with pytest.raises(ManifestError) as raised:
        verify_manifest_source(tampered)
    assert raised.value.reason_code == "MANIFEST_SOURCE_MISMATCH"
    assert str(repository) not in str(raised.value)


def test_rejects_missing_blob_object(tmp_path: Path) -> None:
    repository = tmp_path / "source"
    _init_repository(repository)
    _commit_files(repository, {"tracked.txt": b"loose object\n"})
    blob = _git(repository, "rev-parse", "HEAD:tracked.txt").decode()
    (repository / ".git/objects" / blob[:2] / blob[2:]).unlink()

    with pytest.raises(ManifestError) as raised:
        propose_manifest(_authority(repository), _policy())
    assert raised.value.reason_code == "MANIFEST_GIT_OBJECT_INVALID"
    assert raised.value.path == "tracked.txt"
    assert str(repository) not in str(raised.value)


def test_rejects_policy_forbidden_path_with_relative_only_error(tmp_path: Path) -> None:
    repository = tmp_path / "source"
    _init_repository(repository)
    _commit_files(repository, {"safe.txt": b"safe\n", ".env.prod": b"secret\n"})

    with pytest.raises(ManifestError) as raised:
        propose_manifest(
            _authority(repository),
            _policy(forbidden_patterns=(".env*",)),
        )
    assert raised.value.reason_code == "MANIFEST_POLICY_FORBIDDEN"
    assert raised.value.path == ".env.prod"
    assert str(repository) not in str(raised.value)


@pytest.mark.parametrize("kind", ["symlink", "gitlink"])
def test_rejects_non_regular_git_modes(tmp_path: Path, kind: str) -> None:
    repository = tmp_path / "source"
    _init_repository(repository)
    _commit_files(repository, {"safe.txt": b"safe\n"})
    if kind == "symlink":
        os.symlink("safe.txt", repository / "unsafe-link")
        _git(repository, "add", "unsafe-link")
    else:
        commit = _git(repository, "rev-parse", "HEAD").decode()
        _git(
            repository,
            "update-index", "--add", "--cacheinfo", f"160000,{commit},nested-repository",
        )
    _git(repository, "commit", "-qm", kind)

    with pytest.raises(ManifestError) as raised:
        propose_manifest(_authority(repository), _policy())
    assert raised.value.reason_code == "MANIFEST_MODE_FORBIDDEN"
    assert raised.value.path == ("unsafe-link" if kind == "symlink" else "nested-repository")


def test_rejects_newline_path_without_reflecting_it(tmp_path: Path) -> None:
    repository = tmp_path / "source"
    _init_repository(repository)
    _commit_files(repository, {"bad\nname.txt": b"unsafe\n"})

    with pytest.raises(ManifestError) as raised:
        propose_manifest(_authority(repository), _policy())
    assert raised.value.reason_code == "MANIFEST_PATH_INVALID"
    assert raised.value.path is None
    assert "bad\nname" not in str(raised.value)


@pytest.mark.parametrize(
    "names",
    [
        ("Readme.md", "README.md"),
        ("caf\N{LATIN SMALL LETTER E WITH ACUTE}.txt", "cafe\N{COMBINING ACUTE ACCENT}.txt"),
    ],
)
def test_rejects_case_and_unicode_normalization_collisions(
    tmp_path: Path, names: tuple[str, str],
) -> None:
    repository = tmp_path / "source"
    _init_repository(repository)
    _commit_files(repository, {names[0]: b"one\n", names[1]: b"two\n"})

    with pytest.raises(ManifestError) as raised:
        propose_manifest(_authority(repository), _policy())
    assert raised.value.reason_code == "MANIFEST_PATH_COLLISION"
    assert raised.value.path in names


@pytest.mark.parametrize("bad_path", ["../escape", "/absolute", "bad\x00name", "bad\nname"])
def test_canonicalization_rejects_traversal_absolute_nul_and_newline_paths(
    tmp_path: Path, bad_path: str,
) -> None:
    manifest = _fixture_manifest(tmp_path)
    entry = replace(manifest.entries[0], destination_path=bad_path)
    tampered = replace(manifest, entries=(entry,))

    with pytest.raises(ManifestError) as raised:
        canonical_manifest_bytes(tampered)
    assert raised.value.reason_code == "MANIFEST_PATH_INVALID"
    assert raised.value.path is None


def test_canonicalization_rejects_duplicate_paths(tmp_path: Path) -> None:
    manifest = _fixture_manifest(tmp_path)
    tampered = replace(manifest, entries=(manifest.entries[0], manifest.entries[0]))

    with pytest.raises(ManifestError) as raised:
        canonical_manifest_bytes(tampered)
    assert raised.value.reason_code == "MANIFEST_PATH_COLLISION"
    assert raised.value.path == "file.txt"


@pytest.mark.parametrize(
    ("ancestor", "descendant"),
    [
        ("directory", "directory/file.txt"),
        ("Directory", "directory/file.txt"),
        (
            "caf\N{LATIN SMALL LETTER E WITH ACUTE}",
            "cafe\N{COMBINING ACUTE ACCENT}/file.txt",
        ),
    ],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_canonicalization_rejects_file_directory_ancestor_collisions(
    tmp_path: Path,
    ancestor: str,
    descendant: str,
    reverse: bool,
) -> None:
    manifest = _fixture_manifest(tmp_path)
    base = manifest.entries[0]
    ancestor_entry = replace(
        base,
        source_path=ancestor,
        destination_path=f"imported/{ancestor}",
    )
    descendant_entry = replace(
        base,
        source_path=descendant,
        destination_path=f"imported/{descendant}",
    )
    entries = (ancestor_entry, descendant_entry)
    if reverse:
        entries = tuple(reversed(entries))
    tampered = _with_entries(manifest, entries)

    with pytest.raises(ManifestError) as raised:
        canonical_manifest_bytes(tampered)
    assert raised.value.reason_code == "MANIFEST_PATH_COLLISION"
    assert raised.value.path in {ancestor, descendant}


def test_verify_rejects_aggregate_and_entry_tamper(tmp_path: Path) -> None:
    manifest = _fixture_manifest(tmp_path)
    wrong_aggregate = replace(manifest, aggregate_sha256="0" * 64)
    with pytest.raises(ManifestError) as aggregate:
        verify_manifest_source(wrong_aggregate)
    assert aggregate.value.reason_code == "MANIFEST_AGGREGATE_MISMATCH"

    entry = replace(manifest.entries[0], sha256="0" * 64)
    entries_payload = [{
        "source_path": entry.source_path,
        "destination_path": entry.destination_path,
        "git_blob": entry.git_blob,
        "size": entry.size,
        "mode": entry.mode,
        "sha256": entry.sha256,
    }]
    aggregate_sha256 = hashlib.sha256(json.dumps(
        entries_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    content_tamper = replace(manifest, entries=(entry,), aggregate_sha256=aggregate_sha256)
    with pytest.raises(ManifestError) as content:
        verify_manifest_source(content_tamper)
    assert content.value.reason_code == "MANIFEST_SOURCE_MISMATCH"
    assert content.value.path == "file.txt"


def test_git_calls_use_argument_arrays_and_minimal_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "source"
    _init_repository(repository)
    _commit_files(repository, {"safe.txt": b"safe\n"})
    original_run = subprocess.run
    observed: list[tuple[object, dict[str, object]]] = []

    def recording_run(arguments: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        observed.append((arguments, kwargs))
        return original_run(arguments, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setenv("SENSITIVE_TEST_MARKER", "must-not-propagate")
    monkeypatch.setattr(subprocess, "run", recording_run)

    propose_manifest(_authority(repository), _policy())

    library_calls = [call for call in observed if call[0][0] == "/usr/bin/git"]  # type: ignore[index]
    assert library_calls
    for arguments, kwargs in library_calls:
        assert isinstance(arguments, list)
        assert kwargs["check"] is True
        assert kwargs["text"] is False
        assert kwargs["stdin"] is subprocess.DEVNULL
        assert kwargs["timeout"] == 30
        assert kwargs.get("shell", False) is False
        assert "SENSITIVE_TEST_MARKER" not in kwargs["env"]  # type: ignore[operator]
        assert kwargs["env"]["GIT_NO_REPLACE_OBJECTS"] == "1"  # type: ignore[index]


def test_manifest_git_timeout_is_sanitized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = tmp_path / "source"
    _init_repository(repository)
    _commit_files(repository, {"safe.txt": b"safe\n"})
    authority = _authority(repository)

    def timeout(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired(cmd="git", timeout=30)

    monkeypatch.setattr(manifest_module, "verify_component_authority", lambda _: None)
    monkeypatch.setattr(subprocess, "run", timeout)

    with pytest.raises(ManifestError) as raised:
        propose_manifest(authority, _policy())
    assert raised.value.reason_code == "MANIFEST_GIT_OBJECT_INVALID"
    assert raised.value.path is None
    assert str(repository) not in str(raised.value)


def test_proposal_completes_with_inherited_fifo_stdin(tmp_path: Path) -> None:
    repository = tmp_path / "source"
    _init_repository(repository)
    _commit_files(repository, {"safe.txt": b"safe\n"})
    authority = _authority(repository)
    fifo = tmp_path / "inherited-stdin"
    os.mkfifo(fifo)
    fifo_descriptor = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
    script = """
from pathlib import Path, PurePosixPath
from packages.consolidation.authority import ComponentAuthority
from packages.consolidation.manifest import ImportPolicy, propose_manifest

authority = ComponentAuthority(
    name="fixture",
    repository=Path({repository!r}),
    commit={commit!r},
    tree={tree!r},
    source_prefix=PurePosixPath("."),
    destination_prefix=PurePosixPath("imported"),
)
manifest = propose_manifest(authority, ImportPolicy("fixture-v1", ("**",)))
assert len(manifest.entries) == 1
print("PASS")
""".format(
        repository=str(authority.repository),
        commit=authority.commit,
        tree=authority.tree,
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[2],
            stdin=fifo_descriptor,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
            check=False,
        )
    finally:
        os.close(fifo_descriptor)

    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    assert completed.stdout == b"PASS\n"
