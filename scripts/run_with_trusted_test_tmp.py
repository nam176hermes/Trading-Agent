"""Run one test command with a private trusted temporary directory."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.trusted_test_tmp import prepare_trusted_test_tmp


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--component", required=True)
    parser.add_argument("--cwd", type=Path, default=ROOT)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    command = arguments.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("a command is required after --")

    session = prepare_trusted_test_tmp(arguments.component)
    try:
        environment = os.environ.copy()
        environment.pop("VIRTUAL_ENV", None)
        result = subprocess.run(
            command,
            cwd=arguments.cwd,
            env=environment,
            check=False,
        )
        return result.returncode
    finally:
        session.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
