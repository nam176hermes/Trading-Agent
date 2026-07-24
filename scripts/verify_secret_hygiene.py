#!/usr/bin/env python3
"""Bounded, read-only scan for credentials in the current Git worktree."""

from __future__ import annotations

import argparse
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys


MAX_FILES = 25_000
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 128 * 1024 * 1024
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
CLOEXEC = getattr(os, "O_CLOEXEC", 0)
GIT_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "HOME": "/nonexistent",
    "LANG": "C",
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
}
SKIPPED_COMPONENTS = frozenset({
    ".git", ".next", ".venv", "__pycache__", "build", "coverage", "dist",
    "node_modules", "vendor",
})

PRIVATE_KEY = re.compile(r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----")
PROVIDER_TOKEN = re.compile(
    r"(?:\bsk-(?:proj-|live-|test-)?[A-Za-z0-9_-]{16,}|"
    r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|AIza[A-Za-z0-9_-]{20,}|"
    r"(?:AKIA|ASIA)[A-Z0-9]{16}|xox[baprs]-[A-Za-z0-9-]{12,}))"
)
CREDENTIAL_URI = re.compile(
    r"\b[a-z][a-z0-9+.-]*://[^\s/@:]+:[^\s/@]+@", re.IGNORECASE
)
CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?im)^\s*(?:(?:const|let|var|export|readonly)\s+)?"
    r"(?:[A-Za-z_$][\w$]*\s+)?"
    r"(?:api[_-]?(?:key|token)|(?:finnhub|polygon)[_-]?(?:api[_-]?)?key|"
    r"access[_-]?token|auth[_-]?token|"
    r"client[_-]?secret|credential|password|private[_-]?key|secret|token)"
    r"\s*(?:=|:)\s*(?P<quote>['\"])(?P<credential_value>(?:[^\n\\]|\\.)+?)(?P=quote)"
)
PURE_SHELL_REFERENCE = re.compile(
    r"\$(?:\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_]*|[0-9@*#?!_-]+)\Z"
)


class ScannerError(Exception):
    """A filesystem or Git discovery error that invalidates the scan."""


def _safe_relative(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or "." in path.parts
        or ".." in path.parts
        or path.as_posix() != value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ScannerError("invalid Git path")
    return value


def _git_paths(root: Path) -> list[str]:
    commands = (
        ["git", "-C", os.fspath(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        [
            "git", "-C", os.fspath(root), "ls-files", "--others", "--ignored",
            "--exclude-standard", "-z", "--", ".env", ".env.*",
            ":(glob)**/.env", ":(glob)**/.env.*",
        ],
    )
    outputs: list[bytes] = []
    try:
        for command in commands:
            result = subprocess.run(
                command,
                check=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=GIT_ENV,
                timeout=30,
            )
            outputs.append(result.stdout)
    except (OSError, subprocess.SubprocessError) as error:
        raise ScannerError("Git discovery failed") from error
    paths: set[str] = set()
    for output in outputs:
        if output and not output.endswith(b"\0"):
            raise ScannerError("invalid Git output")
        for raw in output.split(b"\0"):
            if not raw:
                continue
            try:
                paths.add(_safe_relative(raw.decode("utf-8")))
            except UnicodeDecodeError as error:
                raise ScannerError("invalid Git path") from error
    if len(paths) > MAX_FILES:
        raise ScannerError("file count limit exceeded")
    return sorted(paths, key=os.fsencode)


def _read_candidate(root: Path, relative: str) -> bytes:
    parts = PurePosixPath(relative).parts
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | NOFOLLOW | CLOEXEC
    parent_descriptor = -1
    try:
        parent_descriptor = os.open(root, directory_flags)
        for component in parts[:-1]:
            child_descriptor = os.open(component, directory_flags, dir_fd=parent_descriptor)
            os.close(parent_descriptor)
            parent_descriptor = child_descriptor
        name = parts[-1]
        before = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
            raise ScannerError(f"unsafe candidate: {relative}")
        if before.st_size > MAX_FILE_BYTES:
            raise ScannerError(f"file size limit exceeded: {relative}")
        descriptor = os.open(name, os.O_RDONLY | NOFOLLOW | CLOEXEC, dir_fd=parent_descriptor)
        try:
            after = os.fstat(descriptor)
            if (
                not stat.S_ISREG(after.st_mode)
                or after.st_dev != before.st_dev
                or after.st_ino != before.st_ino
                or after.st_size != before.st_size
            ):
                raise ScannerError(f"unsafe candidate: {relative}")
            content = os.read(descriptor, after.st_size + 1)
            if len(content) != after.st_size:
                raise ScannerError(f"unstable candidate: {relative}")
            return content
        finally:
            os.close(descriptor)
    except ScannerError:
        raise
    except OSError as error:
        raise ScannerError(f"unreadable candidate: {relative}") from error
    finally:
        if parent_descriptor >= 0:
            os.close(parent_descriptor)


def _is_scannable_path(relative: str) -> bool:
    path = PurePosixPath(relative)
    if path.name in {"package-lock.json", "uv.lock"} or path.suffix == ".lock":
        return False
    if any(component in SKIPPED_COMPONENTS for component in path.parts):
        return False
    return True


def _line_number(content: str, offset: int) -> int:
    return content.count("\n", 0, offset) + 1


def _findings(relative: str, content: bytes) -> list[tuple[int, str]]:
    if not _is_scannable_path(relative) or b"\0" in content:
        return []
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return []
    rules: list[tuple[str, re.Pattern[str]]] = [
        ("private-key", PRIVATE_KEY),
        ("provider-token", PROVIDER_TOKEN),
        ("credential-uri", CREDENTIAL_URI),
        ("literal-credential-assignment", CREDENTIAL_ASSIGNMENT),
    ]
    findings: list[tuple[int, str]] = []
    for rule, expression in rules:
        for match in expression.finditer(text):
            if (
                rule == "literal-credential-assignment"
                and PURE_SHELL_REFERENCE.fullmatch(match.group("credential_value"))
            ):
                continue
            findings.append((_line_number(text, match.start()), rule))
    return findings


def scan(root: Path) -> list[tuple[str, int, str]]:
    total_bytes = 0
    findings: list[tuple[str, int, str]] = []
    for relative in _git_paths(root):
        content = _read_candidate(root, relative)
        total_bytes += len(content)
        if total_bytes > MAX_TOTAL_BYTES:
            raise ScannerError("total size limit exceeded")
        findings.extend((relative, line, rule) for line, rule in _findings(relative, content))
    return sorted(set(findings))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        root = arguments.root.resolve(strict=True)
        metadata = root.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise ScannerError("invalid root")
        findings = scan(root)
    except (OSError, ScannerError) as error:
        print(f"secret hygiene scanner error: {error}", file=sys.stderr)
        return 2
    for relative, line, rule in findings:
        print(f"{relative}:{line}:{rule}", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
