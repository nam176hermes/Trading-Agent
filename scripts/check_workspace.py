"""Concise, read-only workspace preflight for local and CI workflows."""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.trusted_test_tmp import TrustedTestTmpError, select_trusted_test_tmp_root


GENERATED_FILES = (
    "generated/dashboard/api-schemas.ts",
    "generated/dashboard/api-types.ts",
    "generated/job-api/openapi/openapi.json",
    "generated/job-api/dashboard/api-types.ts",
    "generated/openapi/openapi.json",
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str


def _command(*arguments: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )


def _tool_major(name: str) -> tuple[int | None, str]:
    executable = shutil.which(name)
    if executable is None:
        return None, "missing"
    result = _command(executable, "--version", cwd=ROOT)
    if result.returncode != 0:
        return None, "version command failed"
    version = result.stdout.strip().splitlines()[0]
    match = re.search(r"(?:^|\s)v?(\d+)(?:\.|$)", version)
    return (int(match.group(1)), version) if match else (None, version)


def _native_custody_check(environment: Mapping[str, str]) -> Check:
    path_value = environment.get("PACKAGE6_FD_CUSTODY_EXTENSION_PATH")
    digest = environment.get("PACKAGE6_FD_CUSTODY_EXTENSION_SHA256")
    if not path_value and not digest:
        return Check("WARN", "native_custody", "authority pair not supplied")
    if not path_value or not digest:
        return Check("FAIL", "native_custody", "authority pair is incomplete")
    if _SHA256.fullmatch(digest) is None:
        return Check("FAIL", "native_custody", "digest is not canonical sha256")

    path = Path(path_value)
    try:
        metadata = path.lstat()
    except OSError:
        return Check("FAIL", "native_custody", "extension is unavailable")
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        return Check("FAIL", "native_custody", "extension is not a regular file")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != digest:
        return Check("FAIL", "native_custody", "extension digest mismatch")
    return Check("PASS", "native_custody", "authority pair verified")


def run_checks(root: Path, environment: Mapping[str, str]) -> list[Check]:
    checks: list[Check] = []
    requested_root = Path(os.path.abspath(root))
    git_root_result = _command("git", "rev-parse", "--show-toplevel", cwd=requested_root)
    if git_root_result.returncode == 0:
        git_root = Path(git_root_result.stdout.strip()).resolve()
        canonical = requested_root.resolve() == git_root == ROOT.resolve()
    else:
        canonical = False
    checks.append(
        Check("PASS" if canonical else "FAIL", "canonical_root", "canonical repository root" if canonical else "root mismatch")
    )

    worktree_config = _command(
        "git", "config", "--local", "--get-all", "core.worktree", cwd=requested_root
    )
    stale_worktree = worktree_config.returncode == 0 and bool(worktree_config.stdout.strip())
    checks.append(
        Check("FAIL" if stale_worktree else "PASS", "core_worktree", "unset" if not stale_worktree else "stale override present")
    )

    python_ok = sys.version_info[:2] == (3, 11)
    checks.append(Check("PASS" if python_ok else "FAIL", "python", f"{sys.version_info.major}.{sys.version_info.minor}"))
    for tool, expected in (("uv", 0), ("node", 22), ("npm", 10)):
        major, version = _tool_major(tool)
        ok = major is not None and (tool == "uv" or major == expected)
        checks.append(Check("PASS" if ok else "FAIL", tool, version))

    try:
        temp_root = select_trusted_test_tmp_root()
        checks.append(Check("PASS", "trusted_test_tmp", f"mode={stat.S_IMODE(temp_root.stat().st_mode):04o}"))
    except (OSError, TrustedTestTmpError) as error:
        checks.append(Check("FAIL", "trusted_test_tmp", type(error).__name__))

    live_values = {
        name: environment.get(name, "false").strip().lower()
        for name in ("LIVE_EXECUTION_ENABLED", "LIVE_TRADING_APPROVED")
    }
    live_ok = all(value == "false" for value in live_values.values())
    checks.append(Check("PASS" if live_ok else "FAIL", "live_flags", "disabled" if live_ok else "enabled or invalid"))
    checks.append(_native_custody_check(environment))

    missing = [relative for relative in GENERATED_FILES if not (requested_root / relative).is_file()]
    checks.append(Check("PASS" if not missing else "FAIL", "generated_contracts", "present" if not missing else f"missing={len(missing)}"))

    worktrees = _command("git", "worktree", "list", "--porcelain", cwd=requested_root)
    count = worktrees.stdout.count("worktree ") if worktrees.returncode == 0 else 0
    checks.append(Check("PASS" if count == 1 else "WARN", "worktree_count", str(count)))
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    arguments = parser.parse_args()
    checks = run_checks(arguments.root, os.environ)
    for check in checks:
        print(f"CHECK={check.status} name={check.name} detail={check.detail}")
    failed = any(check.status == "FAIL" for check in checks)
    warnings = sum(check.status == "WARN" for check in checks)
    print(f"SUMMARY={'FAIL' if failed else 'PASS'} warnings={warnings}")
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
