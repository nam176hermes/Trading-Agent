"""Command-line client for the canonical HWC APIs."""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlencode

from apps.operator_api.auth import load_private_token

from .http import BoundedJsonHttpClient, HttpClientError


EXIT_OK = 0
EXIT_CONFIGURATION = 3
EXIT_API = 4
_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class CliConfigurationError(ValueError):
    pass


class CliCommandError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _bounded_integer(value: str, lower: int, upper: int) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be an integer") from None
    if not lower <= number <= upper:
        raise argparse.ArgumentTypeError(f"must be in {lower}..{upper}")
    return number


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="trading-agent")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("capabilities")

    jobs = commands.add_parser("jobs").add_subparsers(
        dest="jobs_command", required=True
    )
    jobs_list = jobs.add_parser("list")
    jobs_list.add_argument(
        "--limit",
        type=lambda value: _bounded_integer(value, 1, 100),
        metavar="1..100",
        default=50,
    )
    jobs_list.add_argument(
        "--offset",
        type=lambda value: _bounded_integer(value, 0, 1_000_000),
        metavar="0..1000000",
        default=0,
    )
    jobs_show = jobs.add_parser("show")
    jobs_show.add_argument("job_id")
    jobs_cancel = jobs.add_parser("cancel")
    jobs_cancel.add_argument("job_id")

    mode = commands.add_parser("mode").add_subparsers(
        dest="mode_command", required=True
    )
    mode_paper = mode.add_parser("paper")
    mode_paper.add_argument("--idempotency-key", required=True)

    kill = commands.add_parser("kill-switch").add_subparsers(
        dest="kill_command", required=True
    )
    kill.add_parser("status")
    activate = kill.add_parser("activate")
    activate.add_argument("--reason", required=True)
    activate.add_argument("--idempotency-key", required=True)
    clear = kill.add_parser("clear")
    clear.add_argument("--idempotency-key", required=True)
    return parser


def _origin(environment: Mapping[str, str], key: str, default: str) -> str:
    value = environment.get(key, default)
    try:
        BoundedJsonHttpClient(value)
    except ValueError:
        raise CliConfigurationError("invalid API origin") from None
    return value


def _job_token(environment: Mapping[str, str]) -> str:
    token = environment.get("TRADING_JOB_API_TOKEN")
    if (
        token is None
        or not token
        or token.strip() != token
        or not token.isascii()
        or any(ord(character) < 0x21 for character in token)
    ):
        raise CliConfigurationError("job API credential is unavailable")
    return token


def _operator_token(environment: Mapping[str, str]) -> str:
    raw_path = environment.get("OPERATOR_API_CLI_TOKEN_FILE")
    if raw_path is None:
        raise CliConfigurationError("operator API credential is unavailable")
    try:
        return load_private_token(Path(raw_path)).decode("ascii")
    except Exception:
        raise CliConfigurationError("operator API credential is unavailable") from None


def _identity(value: str, name: str) -> str:
    if _IDENTITY.fullmatch(value) is None:
        raise CliConfigurationError(f"invalid {name}")
    return value


def _state(payload: dict[str, object]) -> dict[str, object]:
    data = payload.get("data")
    state = data.get("state") if isinstance(data, dict) else None
    if not isinstance(state, dict):
        raise CliCommandError("INVALID_RESPONSE")
    return state


def _command(
    command: dict[str, object],
    idempotency_key: str,
    correlation_id: str,
    *,
    expected_state_sha256: str | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "submit-operator-command-v1",
        "command_id": f"cmd_{secrets.token_hex(16)}",
        "idempotency_key": _identity(idempotency_key, "idempotency key"),
        "correlation_id": correlation_id,
        "expected_state_sha256": expected_state_sha256,
        "command": command,
    }


def _run(
    options: argparse.Namespace, environment: Mapping[str, str]
) -> dict[str, object]:
    correlation_id = f"corr_{secrets.token_hex(16)}"
    if options.command in {"status", "capabilities"}:
        client = BoundedJsonHttpClient(
            _origin(environment, "TRADING_CONTROL_API_ORIGIN", "http://127.0.0.1:8400"),
            correlation_id=correlation_id,
        )
        return client.get(
            "/v1/system/status" if options.command == "status" else "/v1/capabilities"
        )
    if options.command == "jobs":
        client = BoundedJsonHttpClient(
            _origin(environment, "TRADING_JOB_API_ORIGIN", "http://127.0.0.1:8401"),
            token=_job_token(environment),
            correlation_id=correlation_id,
        )
        if options.jobs_command == "list":
            query = urlencode({"limit": options.limit, "offset": options.offset})
            return client.get(f"/v1/jobs?{query}")
        job_id = _identity(options.job_id, "job identity")
        if options.jobs_command == "show":
            return client.get(f"/v1/jobs/{job_id}")
        return client.post(f"/v1/jobs/{job_id}/cancel", {})

    client = BoundedJsonHttpClient(
        _origin(environment, "TRADING_OPERATOR_API_ORIGIN", "http://127.0.0.1:8402"),
        token=_operator_token(environment),
        correlation_id=correlation_id,
    )
    if options.command == "mode":
        return client.post(
            "/v1/commands",
            _command(
                {"command_type": "SET_REQUESTED_MODE", "desired_mode": "PAPER"},
                options.idempotency_key,
                correlation_id,
            ),
        )
    if options.kill_command == "status":
        return client.get("/v1/state")
    if options.kill_command == "activate":
        reason = options.reason.strip()
        if not (1 <= len(reason) <= 256) or "\n" in reason or "\r" in reason:
            raise CliConfigurationError("invalid kill-switch reason")
        return client.post(
            "/v1/commands",
            _command(
                {
                    "command_type": "SET_KILL_SWITCH",
                    "desired_state": "ACTIVE",
                    "reason": reason,
                },
                options.idempotency_key,
                correlation_id,
            ),
        )

    state = _state(client.get("/v1/state"))
    if state.get("kill_switch_state") != "ACTIVE":
        raise CliCommandError("KILL_SWITCH_NOT_ACTIVE")
    digest = state.get("state_sha256")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise CliCommandError("INVALID_RESPONSE")
    return client.post(
        "/v1/commands",
        _command(
            {
                "command_type": "SET_KILL_SWITCH",
                "desired_state": "INACTIVE",
                "reason": None,
            },
            options.idempotency_key,
            correlation_id,
            expected_state_sha256=digest,
        ),
    )


def _print_error(code: str) -> None:
    print(
        json.dumps({"code": code}, sort_keys=True, separators=(",", ":")),
        file=sys.stderr,
    )


def main(arguments: list[str] | None = None) -> int:
    try:
        options = _parser().parse_args(arguments)
    except SystemExit as error:
        return int(error.code)
    try:
        result = _run(options, os.environ)
    except CliConfigurationError:
        _print_error("CONFIGURATION_ERROR")
        return EXIT_CONFIGURATION
    except CliCommandError as error:
        _print_error(error.code)
        return EXIT_API
    except HttpClientError as error:
        _print_error(error.code)
        return EXIT_API
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
