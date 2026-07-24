#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.runtime_release.offline_wheelhouse import (
    MANIFEST_NAME,
    verify_offline_wheelhouse,
    write_wheelhouse_manifest,
)


PIP_VERSION = "25.1.1"
_FAILURE = "runtime release wheelhouse preparation failed"


def _run(command: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def _clean_environment() -> dict[str, str]:
    retained = {
        "HOME": os.environ.get("HOME", ""),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "SSL_CERT_FILE": os.environ.get("SSL_CERT_FILE", ""),
    }
    return {key: value for key, value in retained.items() if value}


def _python_identity(python: str, *, cwd: Path, env: dict[str, str]) -> str:
    result = _run(
        [
            python,
            "-I",
            "-c",
            "import platform; print(f'CPython {platform.python_version()}')",
        ],
        cwd=cwd,
        env=env,
    )
    identity = result.stdout.strip()
    if re.fullmatch(r"CPython 3\.11\.\d+", identity) is None:
        raise ValueError(_FAILURE)
    return identity


def _pip_tool_command(uv: str, python: str, *arguments: str) -> list[str]:
    return [
        uv,
        "tool",
        "run",
        "--python",
        python,
        "--no-python-downloads",
        "--from",
        f"pip=={PIP_VERSION}",
        "pip",
        *arguments,
    ]


def _repository_is_clean(repo: Path) -> None:
    top = _run(["git", "rev-parse", "--show-toplevel"], cwd=repo, env=_clean_environment()).stdout.strip()
    status = _run(
        ["git", "status", "--porcelain=v1", "--untracked-files=normal"],
        cwd=repo,
        env=_clean_environment(),
    ).stdout
    if top != os.fspath(repo) or status:
        raise ValueError(_FAILURE)


def _repository_has_git_metadata(repo: Path) -> bool:
    try:
        metadata = (repo / ".git").lstat()
    except OSError:
        return False
    return not stat.S_ISLNK(metadata.st_mode) and (
        stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)
    )


def _prepare(repo: Path, destination: Path, *, python: str, uv: str) -> dict[str, object]:
    if not repo.is_absolute() or not destination.is_absolute() or destination == Path("/"):
        raise ValueError(_FAILURE)
    repo = repo.resolve(strict=True)
    if not _repository_has_git_metadata(repo) or (repo / "uv.lock").is_symlink():
        raise ValueError(_FAILURE)
    _repository_is_clean(repo)
    lock = repo / "uv.lock"
    lock_digest = hashlib.sha256(lock.read_bytes()).hexdigest()
    if destination.name != lock_digest:
        raise ValueError(_FAILURE)
    if destination.exists() or destination.is_symlink():
        digest = verify_offline_wheelhouse(destination, lock)
        document = json.loads((destination / MANIFEST_NAME).read_text(encoding="utf-8"))
        return {"aggregate_sha256": digest, "artifacts": len(document["artifacts"]), "reused": True}

    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination.parent.chmod(0o700)
    base_env = _clean_environment()
    python_identity = _python_identity(python, cwd=repo, env=base_env)

    with tempfile.TemporaryDirectory(prefix=".runtime-release-wheelhouse.", dir=destination.parent) as temporary:
        stage = Path(temporary)
        wheelhouse = stage / "wheelhouse"
        wheelhouse.mkdir(mode=0o700)
        requirements = stage / "requirements.txt"
        tool_cache = stage / "tool-cache"
        tool_cache.mkdir(mode=0o700)
        tool_env = {**base_env, "UV_CACHE_DIR": os.fspath(tool_cache)}

        _run(
            [
                uv,
                "export",
                "--project",
                os.fspath(repo),
                "--frozen",
                "--no-dev",
                "--no-editable",
                "--no-emit-project",
                "--format",
                "requirements.txt",
                "--output-file",
                os.fspath(requirements),
            ],
            cwd=repo,
            env=tool_env,
        )
        _run(
            _pip_tool_command(
                uv,
                python,
                "download",
                "--no-cache-dir",
                "--no-deps",
                "--require-hashes",
                "--only-binary=:all:",
                "--index-url",
                "https://pypi.org/simple",
                "--dest",
                os.fspath(wheelhouse),
                "--requirement",
                os.fspath(requirements),
            ),
            cwd=repo,
            env=tool_env,
        )
        pip_version = _run(
            _pip_tool_command(uv, python, "--version"),
            cwd=repo,
            env=tool_env,
        ).stdout.split()
        if pip_version[:2] != ["pip", PIP_VERSION]:
            raise ValueError(_FAILURE)

        probe_venv = stage / "probe-venv"
        _run(
            [python, "-m", "venv", "--without-pip", "--copies", os.fspath(probe_venv)],
            cwd=repo,
            env=base_env,
        )
        probe_cache = stage / "probe-cache"
        probe_env = {
            **base_env,
            "UV_CACHE_DIR": os.fspath(probe_cache),
            "UV_COMPILE_BYTECODE": "0",
            "UV_OFFLINE": "1",
            "VIRTUAL_ENV": os.fspath(probe_venv),
        }
        _run(
            [
                uv,
                "pip",
                "sync",
                os.fspath(requirements),
                "--python",
                os.fspath(probe_venv / "bin/python"),
                "--require-hashes",
                "--strict",
                "--only-binary=:all:",
                "--offline",
                "--no-index",
                "--find-links",
                os.fspath(wheelhouse),
                "--no-cache",
                "--link-mode",
                "copy",
                "--no-python-downloads",
            ],
            cwd=repo,
            env=probe_env,
        )

        manifest = write_wheelhouse_manifest(
            wheelhouse,
            lock,
            python_identity=python_identity,
            downloader=f"pip {PIP_VERSION}",
        )
        document = json.loads(manifest.read_text(encoding="utf-8"))
        wheelhouse.chmod(0o700)
        try:
            wheelhouse.rename(destination)
            destination.chmod(0o555)
            if verify_offline_wheelhouse(destination, lock) != document["aggregate_sha256"]:
                raise ValueError(_FAILURE)
        except Exception:
            if destination.exists() and not destination.is_symlink():
                destination.chmod(0o700)
                shutil.rmtree(destination)
            raise
        return {
            "aggregate_sha256": document["aggregate_sha256"],
            "artifacts": len(document["artifacts"]),
            "reused": False,
        }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare and seal the exact offline wheelhouse for the root runtime release"
    )
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--uv", default=shutil.which("uv") or "uv")
    args = parser.parse_args()
    try:
        result = _prepare(args.repo, args.destination, python=args.python, uv=args.uv)
    except (OSError, ValueError, subprocess.CalledProcessError):
        print(_FAILURE, file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
