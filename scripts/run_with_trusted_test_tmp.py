"""Run one test command with a private trusted temporary directory."""

from __future__ import annotations

import argparse
import hashlib
import os
import signal
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.trusted_test_tmp import prepare_trusted_test_tmp


def prepare_native_custody(environment: dict[str, str], session_path: Path) -> None:
    path_key = "PACKAGE6_FD_CUSTODY_EXTENSION_PATH"
    digest_key = "PACKAGE6_FD_CUSTODY_EXTENSION_SHA256"
    if path_key in environment or digest_key in environment:
        if not environment.get(path_key) or not environment.get(digest_key):
            raise ValueError("native custody identity is incomplete")
    else:
        build = session_path / "native-custody"
        subprocess.run(
            ["make", "-C", str(ROOT / "native/package6_custodian"),
             f"BUILD_DIR={build}", f"PYTHON={sys.executable}", "build"],
            env=environment, check=True,
        )
        extensions = list((build / "python").glob("_package6_fd_custody*.so"))
        if len(extensions) != 1:
            raise RuntimeError("native build must produce exactly one custody extension")
        extension = extensions[0]
        if extension.is_symlink() or not extension.is_file():
            raise RuntimeError("native custody extension must be a regular file")
        environment[path_key] = str(extension)
        environment[digest_key] = hashlib.sha256(extension.read_bytes()).hexdigest()
    # Use the owning loader's no-follow ancestry, ownership, mode, digest and ABI checks.
    subprocess.run(
        [sys.executable, "-c", "from services.paper_runtime.evidence import _require_native_fd_custody; _require_native_fd_custody()"],
        cwd=ROOT, env=environment, check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--component", required=True)
    parser.add_argument("--cwd", type=Path, default=ROOT)
    parser.add_argument("--native-custody", action="store_true")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    arguments = parser.parse_args()
    command = arguments.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("a command is required after --")

    session = prepare_trusted_test_tmp(arguments.component)
    def terminated(signum, _frame):
        raise SystemExit(128 + signum)

    previous = signal.signal(signal.SIGTERM, terminated)
    try:
        environment = os.environ.copy()
        environment.pop("VIRTUAL_ENV", None)
        if arguments.native_custody:
            prepare_native_custody(environment, session.path)
        with subprocess.Popen(command, cwd=arguments.cwd, env=environment, start_new_session=True) as child:
            try:
                return child.wait()
            finally:
                # Stop the owned test group before removing its private temporary files.
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                # A descendant can ignore TERM after the group leader has exited.
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()
    finally:
        signal.signal(signal.SIGTERM, previous)
        session.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
