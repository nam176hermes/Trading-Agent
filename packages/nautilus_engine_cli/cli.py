"""Closed, file-input command line interface for deterministic engine fixtures."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import NoReturn, Sequence
from uuid import uuid5

from pydantic import ValidationError

from packages import engine_contracts as contracts


_PROGRAM = "trading-agent-nautilus"
_SHA256_TOKEN = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_ENGINE_ID = "trading-agent-nautilus-fixture-v1"
_ENGINE_VERSION = "fixture-1.0.0"
_BACKTEST_EVENT = "BacktestFixtureCompleted"
_PAPER_EVENT = "PaperFixtureReady"


def _add_request_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("request", type=Path, metavar="request.json")
    parser.add_argument("request_sha256", type=Path, metavar="request.sha256")


def build_parser() -> argparse.ArgumentParser:
    """Build the intentionally closed command parser."""

    parser = argparse.ArgumentParser(prog=_PROGRAM)
    subcommands = parser.add_subparsers(dest="subcommand", required=True)

    subcommands.add_parser("capabilities")
    for name in ("validate-request", "backtest-fixture", "paper-fixture"):
        command_parser = subcommands.add_parser(name)
        _add_request_arguments(command_parser)
    return parser


def _read_regular_file(path: Path) -> bytes:
    """Read one regular non-symlink file without following a substituted link."""

    try:
        initial = path.lstat()
    except OSError as exc:
        raise ValueError(f"unable to inspect {path}") from exc
    if stat.S_ISLNK(initial.st_mode) or not stat.S_ISREG(initial.st_mode):
        raise ValueError(f"{path} must be a regular non-symlink file")

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"unable to read {path}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError(f"{path} must be a regular non-symlink file")
        with os.fdopen(descriptor, "rb", closefd=False) as request_file:
            return request_file.read()
    finally:
        os.close(descriptor)


def _read_sha256_token(path: Path) -> str:
    raw_sidecar = _read_regular_file(path)
    try:
        tokens = raw_sidecar.decode("ascii").split()
    except UnicodeDecodeError as exc:
        raise ValueError("request SHA-256 sidecar must be ASCII") from exc
    if len(tokens) != 1 or _SHA256_TOKEN.fullmatch(tokens[0]) is None:
        raise ValueError("request SHA-256 sidecar must contain one lowercase SHA-256 token")
    return tokens[0]


def _validated_request(request_path: Path, sidecar_path: Path) -> contracts.EngineCommandEnvelope:
    request_bytes = _read_regular_file(request_path)
    supplied_digest = _read_sha256_token(sidecar_path)
    actual_digest = hashlib.sha256(request_bytes).hexdigest()
    if not hmac.compare_digest(actual_digest, supplied_digest):
        raise ValueError("request SHA-256 does not match request bytes")
    try:
        json.loads(request_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("request JSON is invalid") from exc
    return contracts.EngineCommandEnvelope.model_validate_json(request_bytes)


def _capabilities() -> contracts.EngineCapabilities:
    return contracts.EngineCapabilities(
        schema_version=contracts.CURRENT_SCHEMA_VERSION,
        engine_id=_ENGINE_ID,
        engine_version=_ENGINE_VERSION,
        supported_commands=contracts.COMMAND_TYPES,
        supported_event_families=tuple(contracts.EventFamily),
        supported_modes=(contracts.EngineMode.BACKTEST, contracts.EngineMode.PAPER),
    )


def _fixture_event(
    request: contracts.EngineCommandEnvelope, event_name: str
) -> contracts.EngineEventEnvelope:
    payload = contracts.EngineEvent(
        event_type=event_name,
        family=contracts.EventFamily.ENGINE_LIFECYCLE,
    )
    return contracts.EngineEventEnvelope(
        message_id=uuid5(request.message_id, event_name),
        correlation_id=request.correlation_id,
        causation_id=request.message_id,
        engine_run_id=request.engine_run_id,
        stream_sequence=request.stream_sequence + 1,
        event_time=request.event_time,
        initialization_time=request.initialization_time,
        schema_version=request.schema_version,
        producer_identity=request.producer_identity,
        source_commit=request.source_commit,
        config_digest=request.config_digest,
        payload_digest=contracts.payload_digest(payload),
        payload=payload,
    )


def _write_success(value: object) -> None:
    sys.stdout.buffer.write(contracts.canonical_json_bytes(value))
    sys.stdout.buffer.write(b"\n")


def _fail(message: str) -> NoReturn:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def main(argv: Sequence[str] | None = None) -> None:
    """Run one closed fixture command and emit no non-result stdout."""

    arguments = build_parser().parse_args(argv)
    try:
        if arguments.subcommand == "capabilities":
            _write_success(_capabilities())
            return

        request = _validated_request(arguments.request, arguments.request_sha256)
        if arguments.subcommand == "validate-request":
            _write_success(request)
            return
        expected_command, event_name = {
            "backtest-fixture": ("RunBacktest", _BACKTEST_EVENT),
            "paper-fixture": ("StartPaperEngine", _PAPER_EVENT),
        }[arguments.subcommand]
        if request.payload.command_type != expected_command:
            raise ValueError(
                f"{arguments.subcommand} requires {expected_command}, "
                f"not {request.payload.command_type}"
            )
        _write_success(_fixture_event(request, event_name))
    except (OSError, ValidationError, ValueError) as exc:
        _fail(str(exc))


if __name__ == "__main__":
    main()
