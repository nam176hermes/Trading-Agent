#!/usr/bin/env python3
"""Read-only audit of the standalone canonical trading-agent Git root."""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.consolidation import (  # noqa: E402
    AuthorityError,
    SourceAuthority,
    load_source_authority,
    parse_source_authority,
)
from import_component_snapshot import CliError, CodeArgumentParser  # noqa: E402
from verify_component_snapshot import verify_embedded_snapshot, verify_snapshot  # noqa: E402


_GIT = "/usr/bin/git"
_GIT_TIMEOUT_SECONDS = 30
_GIT_EXECUTION_GUARDS = (
    "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
)
_GIT_ID = re.compile(r"[0-9a-f]{40}\Z")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_GIT_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "HOME": "/nonexistent",
    "LANG": "C",
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
}
_REQUIRED = (
    "AGENTS.md",
    "pyproject.toml",
    "uv.lock",
    "ops/consolidation/source-authority.json",
    "ops/consolidation/backend-source-manifest.json",
    "ops/consolidation/dashboard-source-manifest.json",
    "legacy/research-backend/AGENTS.md",
    "legacy/research-backend/pyproject.toml",
    "legacy/research-backend/uv.lock",
    "apps/dashboard/AGENTS.md",
    "apps/dashboard/package.json",
    "apps/dashboard/package-lock.json",
)
_COMPONENTS = {
    "backend": (
        "legacy/research-backend/pyproject.toml",
        "legacy/research-backend",
        "ops/consolidation/backend-source-manifest.json",
    ),
    "dashboard": (
        "apps/dashboard/package.json",
        "apps/dashboard",
        "ops/consolidation/dashboard-source-manifest.json",
    ),
}
_FORBIDDEN_PATTERNS = (
    ".env", ".env.*", "**/.env", "**/.env.*", ".keys.enc", "**/.keys.enc",
    ".mode", "**/.mode", ".kill_switch", "**/.kill_switch", "node_modules/**",
    "**/node_modules/**", ".next/**", "**/.next/**", "__pycache__/**",
    "**/__pycache__/**", "*.pyc", "**/*.pyc", "coverage/**", "**/coverage/**",
    ".cache/**", "**/.cache/**", ".pytest_cache/**", "**/.pytest_cache/**",
    ".mypy_cache/**", "**/.mypy_cache/**", ".ruff_cache/**", "**/.ruff_cache/**",
    ".venv/**", "**/.venv/**", "build/**", "**/build/**", "dist/**",
    "**/dist/**", "runtime/**", "**/runtime/**", "data/runtime/**",
    "**/data/runtime/**", "*.log", "**/*.log", "*.sqlite", "**/*.sqlite",
    "*.sqlite3", "**/*.sqlite3", "apps/dashboard/credentials/**",
    "apps/dashboard/**/credentials/**", "apps/dashboard/*credential*",
    "apps/dashboard/**/*credential*", "apps/dashboard/*secret*",
    "apps/dashboard/**/*secret*", "apps/dashboard/secrets/**",
    "apps/dashboard/runtime/**",
    "apps/dashboard/data/runtime/**", "legacy/research-backend/db/trading.db",
    "legacy/research-backend/jobs/**", "legacy/research-backend/job_artifacts/**",
    "legacy/research-backend/scratchpad/**",
    "legacy/research-backend/**/scratchpad/**",
    "legacy/research-backend/scratchpad*.json",
    "legacy/research-backend/scratchpad*.jsonl",
    "legacy/research-backend/**/scratchpad*.json",
    "legacy/research-backend/**/scratchpad*.jsonl",
    "legacy/research-backend/.dexter/**",
    "legacy/research-backend/.codegraph/**", "legacy/research-backend/.cache/**",
    "legacy/research-backend/decisions/**", "legacy/research-backend/memory/**",
    "legacy/research-backend/models/**", "legacy/research-backend/signals/**",
    "legacy/research-backend/reports/**", "legacy/research-backend/.venv/**",
    "legacy/research-backend/run_status.json",
    "legacy/research-backend/live_prices.json",
    "legacy/research-backend/decisions_scored.jsonl",
    "legacy/research-backend/strategy.json",
    "legacy/research-backend/*.json", "legacy/research-backend/**/*.json",
    "legacy/research-backend/*.jsonl", "legacy/research-backend/**/*.jsonl",
)
_SOURCE_SUFFIXES = frozenset({
    ".py", ".sh", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".toml", ".yaml", ".yml",
})
_SOURCE_NAMES = frozenset({"AGENTS.md", "README.md", "Makefile"})
_GLOBAL_SOURCE_MARKERS = (
    b".local/share/" + b"codex-worktrees",
    b"/home/thenam176/projects/" + b"trading-dashboard",
    b"/home/thenam176/projects/" + b"trading-agent-migration",
)
_COMPONENT_SOURCE_MARKERS = (
    b"/home/thenam176/" + b".hermes",
    b"~/" + b".hermes",
)


def _safe_relative(value: str | None) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        return None
    if path.as_posix() != value:
        return None
    return value


def _git(root: Path, arguments: list[str], code: str = "E_ROOT") -> bytes:
    try:
        return subprocess.run(
            [_GIT, *_GIT_EXECUTION_GUARDS, "-C", os.fspath(root), *arguments],
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=dict(_GIT_ENV),
            text=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        raise CliError(code) from None


def _root(path: Path) -> Path:
    text = os.fspath(path)
    try:
        if not path.is_absolute() or os.path.normpath(text) != text:
            raise OSError
        if path.resolve(strict=True) != path:
            raise OSError
        metadata = path.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise OSError
        git_metadata = (path / ".git").lstat()
        if not stat.S_ISDIR(git_metadata.st_mode) or stat.S_ISLNK(git_metadata.st_mode):
            raise CliError("E_ROOT", ".git")
    except CliError:
        raise
    except OSError:
        raise CliError("E_ROOT") from None
    try:
        top = Path(_git(path, ["rev-parse", "--show-toplevel"]).decode("utf-8").strip())
        common_text = _git(path, ["rev-parse", "--git-common-dir"]).decode("utf-8").strip()
        common = Path(common_text)
        if not common.is_absolute():
            common = path / common
        common = common.resolve(strict=True)
    except (UnicodeDecodeError, OSError, CliError):
        raise CliError("E_ROOT", ".git") from None
    if top != path or common != path / ".git":
        raise CliError("E_ROOT", ".git")
    return path


def _nested_git(root: Path) -> None:
    try:
        for directory, names, filenames in os.walk(root, followlinks=False):
            directory_path = Path(directory)
            relative_directory = directory_path.relative_to(root)
            if relative_directory == Path("."):
                if ".git" in names:
                    names.remove(".git")
            elif ".git" in names or ".git" in filenames:
                raise CliError("E_NESTED_GIT", (relative_directory / ".git").as_posix())
            for name in list(names):
                if (directory_path / name).is_symlink():
                    names.remove(name)
    except CliError:
        raise
    except OSError:
        raise CliError("E_ROOT") from None


def _index(root: Path) -> dict[str, str]:
    raw = _git(root, ["ls-files", "-s", "-z"], "E_ROOT")
    if raw and not raw.endswith(b"\0"):
        raise CliError("E_ROOT")
    result: dict[str, str] = {}
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, encoded_path = record.split(b"\t", 1)
            mode, object_id, stage = metadata.decode("ascii").split(" ", 2)
            path = encoded_path.decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            raise CliError("E_ROOT") from None
        if _safe_relative(path) is None or stage != "0" or _GIT_ID.fullmatch(object_id) is None:
            raise CliError("E_ROOT")
        if mode == "160000":
            raise CliError("E_NESTED_GIT", path)
        if mode == "120000":
            raise CliError("E_TRACKED_LINK", path)
        result[path] = mode
    return result


def _forbidden(paths: set[str]) -> None:
    for path in sorted(paths, key=lambda value: value.encode("utf-8")):
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in _FORBIDDEN_PATTERNS):
            raise CliError("E_FORBIDDEN", path)


def _required(paths: set[str]) -> None:
    for path in _REQUIRED:
        if path not in paths:
            raise CliError("E_REQUIRED", path)


def _status(root: Path) -> tuple[str, str | None]:
    raw = _git(root, ["status", "--porcelain=v1", "-z", "--untracked-files=all"], "E_ROOT")
    if not raw:
        return "clean", None
    try:
        record = raw.split(b"\0", 1)[0].decode("utf-8")
    except UnicodeDecodeError:
        return "dirty", None
    path = record[3:] if len(record) >= 4 else ""
    return "dirty", _safe_relative(path)


def _scan_sources(root: Path, paths: set[str]) -> None:
    for relative in sorted(paths, key=lambda value: value.encode("utf-8")):
        path = PurePosixPath(relative)
        if path.parts[0] in {"docs", ".superpowers"}:
            continue
        if path.suffix not in _SOURCE_SUFFIXES and path.name not in _SOURCE_NAMES:
            continue
        descriptor: int | None = None
        try:
            descriptor = os.open(root / relative, os.O_RDONLY | _NOFOLLOW | _CLOEXEC)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 4 * 1024 * 1024:
                raise OSError
            content = b""
            while chunk := os.read(descriptor, 65536):
                content += chunk
        except OSError:
            raise CliError("E_SOURCE_PATH", relative) from None
        finally:
            if descriptor is not None:
                os.close(descriptor)
        markers = _GLOBAL_SOURCE_MARKERS
        if relative.startswith(("legacy/research-backend/", "apps/dashboard/")):
            markers += _COMPONENT_SOURCE_MARKERS
        if any(marker in content for marker in markers):
            raise CliError("E_SOURCE_PATH", relative)


def _introduction(root: Path, sentinel: str, prefix: str) -> str:
    raw = _git(
        root,
        ["log", "--diff-filter=A", "--format=%H", "--", sentinel],
        "E_GIT_OBJECT",
    )
    try:
        commits = [line for line in raw.decode("ascii").splitlines() if line]
    except UnicodeDecodeError:
        raise CliError("E_GIT_OBJECT", sentinel) from None
    if len(commits) != 1 or _GIT_ID.fullmatch(commits[0]) is None:
        raise CliError("E_MANIFEST", sentinel)
    introduction = commits[0]
    try:
        parent = _git(
            root,
            ["rev-parse", "--verify", "--end-of-options", f"{introduction}^{{commit}}^"],
            "E_GIT_OBJECT",
        ).decode("ascii").strip()
    except UnicodeDecodeError:
        raise CliError("E_GIT_OBJECT", sentinel) from None
    if _GIT_ID.fullmatch(parent) is None:
        raise CliError("E_GIT_OBJECT", sentinel)
    try:
        present = subprocess.run(
            [
                _GIT, *_GIT_EXECUTION_GUARDS, "-C", os.fspath(root),
                "cat-file", "-e", f"{parent}:{prefix}",
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=dict(_GIT_ENV),
            text=False,
            timeout=_GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):
        raise CliError("E_GIT_OBJECT", sentinel) from None
    if present.returncode == 0:
        raise CliError("E_MANIFEST", prefix)
    return introduction


def _authority_availability(authority: SourceAuthority) -> tuple[bool, ...]:
    available: list[bool] = []
    for component in authority.components.values():
        try:
            component.repository.lstat()
        except FileNotFoundError:
            available.append(False)
        except OSError:
            raise CliError("E_AUTHORITY") from None
        else:
            available.append(True)
    return tuple(available)


def _audit_authority(path: Path) -> tuple[SourceAuthority, bool]:
    try:
        parsed = parse_source_authority(path)
    except AuthorityError:
        raise CliError("E_AUTHORITY") from None
    available = _authority_availability(parsed)
    if all(available):
        try:
            return load_source_authority(path), False
        except AuthorityError:
            raise CliError("E_AUTHORITY") from None
    if any(available):
        raise CliError("E_AUTHORITY")
    return parsed, True


def _evidence_blob(root: Path, revision: str, relative: str, code: str) -> str:
    try:
        object_id = _git(
            root,
            ["rev-parse", "--verify", "--end-of-options", f"{revision}:{relative}"],
            code,
        ).decode("ascii").strip()
    except UnicodeDecodeError:
        raise CliError(code) from None
    if _GIT_ID.fullmatch(object_id) is None:
        raise CliError(code)
    return object_id


def _immutable_evidence(root: Path, relative: str, code: str) -> None:
    raw = _git(root, ["log", "--diff-filter=A", "--format=%H", "--", relative], code)
    try:
        introductions = [line for line in raw.decode("ascii").splitlines() if line]
    except UnicodeDecodeError:
        raise CliError(code) from None
    if len(introductions) != 1 or _GIT_ID.fullmatch(introductions[0]) is None:
        raise CliError(code)
    introduction_blob = _evidence_blob(root, introductions[0], relative, code)
    head_blob = _evidence_blob(root, "HEAD", relative, code)
    try:
        working_blob = _git(
            root, ["hash-object", "--no-filters", "--", relative], code,
        ).decode("ascii").strip()
    except UnicodeDecodeError:
        raise CliError(code) from None
    if (
        _GIT_ID.fullmatch(working_blob) is None
        or introduction_blob != head_blob
        or head_blob != working_blob
    ):
        raise CliError(code)


def audit(root_path: Path, release: bool) -> dict[str, object]:
    root = _root(root_path)
    _nested_git(root)
    index = _index(root)
    paths = set(index)
    _forbidden(paths)
    status, dirty_path = _status(root)
    if release and status != "clean":
        raise CliError("E_DIRTY", dirty_path)
    _required(paths)
    authority_path = root / "ops/consolidation/source-authority.json"
    authority, portable = _audit_authority(authority_path)
    _scan_sources(root, paths)
    try:
        head = _git(
            root, ["rev-parse", "--verify", "--end-of-options", "HEAD^{commit}"], "E_ROOT",
        ).decode("ascii").strip()
        branch = _git(root, ["rev-parse", "--abbrev-ref", "HEAD"], "E_ROOT").decode("utf-8").strip()
    except UnicodeDecodeError:
        raise CliError("E_ROOT") from None
    if _GIT_ID.fullmatch(head) is None:
        raise CliError("E_ROOT")
    components: dict[str, object] = {
        "core": {"commit": authority.components["core"].commit, "result": "PASS"},
    }
    for name, (sentinel, prefix, manifest_path) in _COMPONENTS.items():
        introduction = _introduction(root, sentinel, prefix)
        revisions = (introduction, head) if portable else (introduction,)
        try:
            for revision in dict.fromkeys(revisions):
                if portable:
                    verify_embedded_snapshot(
                        authority, root / manifest_path, root, revision,
                    )
                else:
                    verify_snapshot(
                        authority_path, root / manifest_path, root, revision,
                    )
        except CliError as error:
            raise error from None
        components[name] = {"introduction": introduction, "result": "PASS"}
    if portable and any(_authority_availability(authority)):
        raise CliError("E_AUTHORITY")
    _immutable_evidence(root, "ops/consolidation/source-authority.json", "E_AUTHORITY")
    for _, _, manifest_path in _COMPONENTS.values():
        _immutable_evidence(root, manifest_path, "E_MANIFEST")
    return {
        "schema_version": 1,
        "root": os.fspath(root),
        "head": head,
        "branch": branch,
        "status": status,
        "components": components,
        "result": "PASS",
    }


def _parser() -> argparse.ArgumentParser:
    parser = CodeArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--release", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def _print_json_failure(error: str) -> None:
    failure = {
        "schema_version": 1,
        "root": "",
        "head": "",
        "branch": "",
        "status": "unknown",
        "components": {},
        "result": error,
    }
    print(json.dumps(failure, sort_keys=True, separators=(",", ":")))


def main(argv: list[str] | None = None) -> int:
    arguments: argparse.Namespace | None = None
    raw_arguments = sys.argv[1:] if argv is None else argv
    json_requested = "--json" in raw_arguments
    try:
        arguments = _parser().parse_args(argv)
        result = audit(arguments.root, arguments.release)
    except CliError as error:
        if json_requested:
            _print_json_failure(str(error))
        print(str(error), file=sys.stderr)
        return 1
    except SystemExit:
        raise
    except BaseException:
        if json_requested:
            _print_json_failure("E_ARGUMENT")
        print("E_ARGUMENT", file=sys.stderr)
        return 1
    assert arguments is not None
    if arguments.json:
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    else:
        component_text = ",".join(result["components"])
        print(
            f"head={result['head']} branch={result['branch']} status={result['status']} "
            f"components={component_text} result=PASS"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
