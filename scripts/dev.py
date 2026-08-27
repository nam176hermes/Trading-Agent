"""Fast, deterministic developer commands outside the protected release Makefile."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
RUFF_VERSION = "ruff==0.16.5"
BASEDPYRIGHT_VERSION = "basedpyright==1.39.10"
PRODUCTION_PATHS = ("apps/control_api", "apps/job_api", "packages", "services", "scripts")


def _run(command: Sequence[str]) -> int:
    return subprocess.run(command, cwd=ROOT, check=False).returncode


def _lint() -> int:
    return _run(
        (
            "uvx",
            "--from",
            RUFF_VERSION,
            "ruff",
            "check",
            *PRODUCTION_PATHS,
            "--config",
            "ruff.toml",
            "--no-cache",
        )
    )


def _types() -> int:
    commands = (
        (
            "uvx",
            "--from",
            BASEDPYRIGHT_VERSION,
            "basedpyright",
            "--level",
            "error",
            "--project",
            "pyrightconfig.json",
        ),
        (
            "uvx",
            "--from",
            BASEDPYRIGHT_VERSION,
            "basedpyright",
            "--level",
            "error",
            "--pythonpath",
            "legacy/research-backend/.venv/bin/python",
            "--project",
            "pyrightconfig.legacy.json",
        ),
    )
    for command in commands:
        status = _run(command)
        if status:
            return status
    return 0


def _pytest(arguments: Sequence[str], *, debug: bool) -> int:
    if not arguments:
        print(
            "a focused pytest path or expression is required; use `make test-all` for the canonical suite",
            file=sys.stderr,
        )
        return 2
    output_arguments = ("-vv", "-s", "--tb=long") if debug else ("-q", "--capture=fd", "--tb=short")
    return _run(
        (
            sys.executable,
            "scripts/run_with_trusted_test_tmp.py",
            "--component",
            "root-pytest",
            "--",
            "uv",
            "run",
            "pytest",
            *output_arguments,
            *arguments,
        )
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fast local checks that preserve the protected canonical Makefile.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("doctor", help="validate workspace, Git, temp, and paper-only safety")
    subparsers.add_parser("lint", help="run pinned Ruff checks")
    subparsers.add_parser("types", help="run pinned root and legacy Basedpyright checks")
    subparsers.add_parser("static", help="run lint followed by both type checks")
    for name, help_text in (
        ("test", "run a focused quiet pytest selection"),
        ("test-debug", "run a focused verbose pytest selection"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("pytest_args", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    if arguments.command == "doctor":
        return _run((sys.executable, "scripts/check_workspace.py", "--root", str(ROOT)))
    if arguments.command == "lint":
        return _lint()
    if arguments.command == "types":
        return _types()
    if arguments.command == "static":
        lint_status = _lint()
        return lint_status if lint_status else _types()
    pytest_arguments = arguments.pytest_args
    if pytest_arguments[:1] == ["--"]:
        pytest_arguments = pytest_arguments[1:]
    return _pytest(pytest_arguments, debug=arguments.command == "test-debug")


if __name__ == "__main__":
    raise SystemExit(main())
