"""Narrow entry point for the local P1 paper runtime."""

from __future__ import annotations

import json
import os
import stat
import sys
from importlib.metadata import version as package_version
from typing import TYPE_CHECKING

if __package__ in {None, ""}:
    sys.path.insert(0, "/engine")
    __package__ = "runtime_v1"

from .bootstrap import (  # noqa: E402
    LINEAGE,
    PYTHON,
    REQUEST,
    SIDECAR,
    RuntimeBootstrapError,
    _kernel_arguments,
    require_engine_version,
    require_product_lineage,
)
from .control_channel import MAX_FRAMES, read_payload  # noqa: E402
from .dependency_scope import sealed_wheel_imports  # noqa: E402
from .generated_protocol import canonical_json_bytes  # noqa: E402
from .input_loader import RuntimeInputs, load_inputs  # noqa: E402

if TYPE_CHECKING:
    from .paper_runner import PaperCommandLoop, PaperExecution


PAPER_MAIN = "/engine/runtime_v1/paper_main.py"
PAPER_COMMAND = (PYTHON, "-I", "-S", PAPER_MAIN, REQUEST, SIDECAR)


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_uid,
        info.st_gid,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _product_lineage() -> dict[str, object]:
    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in items:
            if key in value:
                raise RuntimeBootstrapError("paper product lineage mount is invalid")
            value[key] = item
        return value

    descriptor = -1
    try:
        descriptor = os.open(LINEAGE, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o400
            or info.st_size <= 0
            or info.st_size > 1024
        ):
            raise RuntimeBootstrapError("paper product lineage mount is invalid")
        raw = os.read(descriptor, 1025)
        value = json.loads(raw, object_pairs_hook=pairs)
        if (
            type(value) is not dict
            or raw != canonical_json_bytes(value) + b"\n"
            or _identity(os.fstat(descriptor)) != _identity(info)
        ):
            raise RuntimeBootstrapError("paper product lineage mount is invalid")
        require_product_lineage(value)
        return value
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise RuntimeBootstrapError("paper product lineage mount is invalid") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _require_entry() -> None:
    if (
        __name__ != "__main__"
        or __spec__ is not None
        or __file__ != PAPER_MAIN
        or sys.implementation.name != "cpython"
        or (sys.version_info.major, sys.version_info.minor) != (3, 12)
        or sys.flags.isolated != 1
        or sys.flags.no_site != 1
        or sys.flags.ignore_environment != 1
        or sys.flags.no_user_site != 1
        or sys.flags.safe_path is not True
        or tuple(sys.orig_argv) != PAPER_COMMAND
        or tuple(sys.argv) != PAPER_COMMAND[3:]
        or _kernel_arguments() != tuple(os.fsencode(value) for value in PAPER_COMMAND)
        or tuple(sorted(os.environ.items())) != (("PWD", "/"),)
        or os.getcwd() != "/"
    ):
        raise RuntimeBootstrapError("paper runtime requires the fixed isolated entry")


def run_paper_stream(
    inputs: RuntimeInputs,
    command_stream: bytes,
) -> PaperExecution:
    from .paper_runner import run_commands

    return run_commands(inputs, command_stream, _product_lineage())


def open_paper_loop(inputs: RuntimeInputs) -> PaperCommandLoop:
    from .paper_runner import PaperCommandLoop

    return PaperCommandLoop(inputs, _product_lineage())


def main() -> int:
    loop = None
    try:
        _require_entry()
        with sealed_wheel_imports():
            from .paper_runner import PaperCommandLoop, PaperRuntimeRejected

            require_engine_version(package_version("nautilus_trader"))
            loop = PaperCommandLoop(load_inputs(), _product_lineage())
            for _ in range(MAX_FRAMES):
                raw = read_payload(sys.stdin.buffer)
                if raw is None:
                    loop.close_input()
                    return 0
                try:
                    step = loop.accept(raw)
                except PaperRuntimeRejected as rejected:
                    sys.stdout.buffer.write(rejected.response_stream)
                    sys.stdout.buffer.flush()
                    return 70
                sys.stdout.buffer.write(step.response_stream)
                sys.stdout.buffer.flush()
            raise ValueError("paper control stream exceeds maximum frames")
    except BaseException:
        os.write(2, canonical_json_bytes({"error": "PAPER_RUNTIME_FAILED"}) + b"\n")
        return 70
    finally:
        if loop is not None:
            loop.abort()


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main", "open_paper_loop", "run_paper_stream"]
