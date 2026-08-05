"""Black-box regression tests for the isolated engine fixture CLI."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid5

import pytest

from packages import engine_contracts as contracts


CLI = Path(sys.executable).with_name("trading-agent-nautilus")
BWRAP = shutil.which("bwrap")
VENV_ROOT = Path(sys.executable).parent.parent
VENV_INTERPRETER_ROOT = Path(os.readlink(VENV_ROOT / "bin" / "python")).parents[1]
VENV_INTERPRETER_REAL_ROOT = Path(
    os.path.realpath(VENV_ROOT / "bin" / "python")
).parents[1]


def _run(
    *arguments: str,
    environment: dict[str, str] | None = None,
    cwd: Path | None = None,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    assert CLI.is_file(), "the project console script must be registered"
    return subprocess.run(
        [str(CLI), *arguments],
        capture_output=True,
        check=False,
        cwd=cwd,
        text=True,
        env=environment,
        timeout=timeout,
    )


def _bubblewrap_mount_parent_arguments() -> list[str]:
    roots = (VENV_ROOT, VENV_INTERPRETER_ROOT, VENV_INTERPRETER_REAL_ROOT)
    directories = {Path("/inputs")}
    for root in roots:
        parent = root.parent
        while parent != Path("/"):
            directories.add(parent)
            parent = parent.parent
    arguments: list[str] = []
    for directory in sorted(directories, key=lambda value: (len(value.parts), str(value))):
        arguments.extend(("--dir", str(directory)))
    return arguments


def _bubblewrap_command(
    command: list[str],
    *,
    request_path: Path | None = None,
    sidecar_path: Path | None = None,
) -> list[str]:
    if BWRAP is None:
        pytest.fail("Bubblewrap is required for engine CLI OS-sandbox regressions")
    if (request_path is None) != (sidecar_path is None):
        raise ValueError("Bubblewrap requires both request inputs or neither")
    arguments = [
        BWRAP,
        "--die-with-parent",
        "--unshare-user",
        "--unshare-net",
        "--new-session",
        *_bubblewrap_mount_parent_arguments(),
        "--ro-bind",
        "/usr",
        "/usr",
        "--symlink",
        "/usr/lib",
        "/lib",
        "--symlink",
        "/usr/lib64",
        "/lib64",
    ]
    for root in dict.fromkeys(
        (VENV_ROOT, VENV_INTERPRETER_ROOT, VENV_INTERPRETER_REAL_ROOT)
    ):
        arguments.extend(("--ro-bind", str(root), str(root)))
    if request_path is not None and sidecar_path is not None:
        arguments.extend(
            (
                "--ro-bind",
                str(request_path),
                "/inputs/request.json",
                "--ro-bind",
                str(sidecar_path),
                "/inputs/request.sha256",
            )
        )
    arguments.extend(
        (
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--clearenv",
            "--setenv",
            "PYTHONDONTWRITEBYTECODE",
            "1",
            "--chdir",
            "/",
            *command,
        )
    )
    return arguments


def _run_in_bubblewrap(
    *arguments: str, timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    assert CLI.is_file(), "the project console script must be registered"
    request_path: Path | None = None
    sidecar_path: Path | None = None
    command = [str(CLI), arguments[0]]
    if len(arguments) == 3:
        request_path = Path(arguments[1])
        sidecar_path = Path(arguments[2])
        command.extend(("/inputs/request.json", "/inputs/request.sha256"))
    elif len(arguments) != 1:
        raise ValueError("CLI command must have no request inputs or exactly two")
    return subprocess.run(
        _bubblewrap_command(
            command,
            request_path=request_path,
            sidecar_path=sidecar_path,
        ),
        capture_output=True,
        check=False,
        text=True,
        timeout=timeout,
    )


def _run_python_in_bubblewrap(
    program: str, request_path: Path, sidecar_path: Path, *program_arguments: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _bubblewrap_command(
            [str(Path(sys.executable)), "-c", program, *program_arguments],
            request_path=request_path,
            sidecar_path=sidecar_path,
        ),
        capture_output=True,
        check=False,
        text=True,
        timeout=5,
    )


def _command(command_type: str) -> dict[str, object]:
    artifact = {
        "artifact_id": "11111111-1111-1111-1111-111111111111",
        "sha256": "a" * 64,
        "media_type": "application/json",
    }
    if command_type == "RunBacktest":
        return {
            "command_type": command_type,
            "engine_configuration": artifact,
            "instrument_catalog": artifact,
            "strategy_configuration": artifact,
            "market_data": artifact,
            "start_time": "2026-08-04T18:00:00Z",
            "end_time": "2026-08-04T18:30:00Z",
        }
    if command_type == "StartPaperEngine":
        return {
            "command_type": command_type,
            "engine_configuration": artifact,
            "instrument_catalog": artifact,
            "strategy_configuration": artifact,
        }
    return {"command_type": command_type}


def _request(command_type: str = "RunBacktest") -> dict[str, object]:
    payload = _command(command_type)
    return {
        "message_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "correlation_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "causation_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
        "engine_run_id": "dddddddd-dddd-dddd-dddd-dddddddddddd",
        "stream_sequence": 7,
        "event_time": "2026-08-04T18:30:00Z",
        "initialization_time": "2026-08-04T18:00:00Z",
        "schema_version": "1.0.0",
        "producer_identity": "trading-agent-control-plane",
        "source_commit": "e" * 40,
        "config_digest": "f" * 64,
        "payload_digest": contracts.payload_digest(payload),
        "payload": payload,
    }


def _request_files(
    tmp_path: Path, request: dict[str, object], *, sidecar: str | None = None
) -> tuple[Path, Path]:
    raw_request = json.dumps(request, indent=1, ensure_ascii=False).encode("utf-8")
    request_path = tmp_path / "request.json"
    request_path.write_bytes(raw_request)
    sidecar_path = tmp_path / "request.sha256"
    sidecar_path.write_text(
        sidecar if sidecar is not None else hashlib.sha256(raw_request).hexdigest(),
        encoding="ascii",
    )
    return request_path, sidecar_path


def _canonical_output(completed: subprocess.CompletedProcess[str]) -> dict[str, object]:
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    result = json.loads(completed.stdout)
    assert completed.stdout == contracts.canonical_json(result) + "\n"
    return result


def _assert_rejected(completed: subprocess.CompletedProcess[str]) -> None:
    assert completed.returncode != 0
    assert completed.stdout == ""
    assert completed.stderr


def _valid_arguments(tmp_path: Path, subcommand: str) -> tuple[str, ...]:
    if subcommand == "capabilities":
        return (subcommand,)
    command_type = "StartPaperEngine" if subcommand == "paper-fixture" else "RunBacktest"
    request_path, sidecar_path = _request_files(tmp_path, _request(command_type))
    return (subcommand, str(request_path), str(sidecar_path))


def _restricted_environment(
    tmp_path: Path,
    *,
    forbid_imports: bool = False,
    forbid_json_parsing: bool = False,
    remove_no_follow: bool = False,
    allow_socket_construction: bool = False,
) -> dict[str, str]:
    source = [
        "import socket",
        "import sys",
        "def audit_policy(event, arguments):",
        "    if event.startswith('socket.') and not (",
        f"        {allow_socket_construction!r} and event == 'socket.__new__'",
        "    ):",
        "        raise RuntimeError(f'audit policy blocked {event}')",
        "    if event in {",
        "        'subprocess.Popen', 'os.exec', 'os.fork', 'os.posix_spawn',",
        "        'os.spawn', 'os.system',",
        "    }:",
        "        raise RuntimeError(f'audit policy blocked {event}')",
        "    if event == 'open':",
        "        _, mode, flags = arguments",
        "        write_flags = (",
        "            getattr(__import__('os'), 'O_WRONLY', 0)",
        "            | getattr(__import__('os'), 'O_RDWR', 0)",
        "            | getattr(__import__('os'), 'O_CREAT', 0)",
        "            | getattr(__import__('os'), 'O_TRUNC', 0)",
        "            | getattr(__import__('os'), 'O_APPEND', 0)",
        "        )",
        "        if (isinstance(mode, str) and any(flag in mode for flag in 'wax+')) or flags & write_flags:",
        "            raise RuntimeError('audit policy blocked write-capable open')",
        "    if event in {",
        "        'os.chdir', 'os.chmod', 'os.chown', 'os.link', 'os.mkdir',",
        "        'os.remove', 'os.rename', 'os.replace', 'os.rmdir', 'os.symlink',",
        "        'os.setxattr', 'os.removexattr', 'os.truncate', 'os.utime',",
        "    }:",
        "        raise RuntimeError(f'audit policy blocked {event}')",
        "sys.addaudithook(audit_policy)",
        "import os",
        "def blocked_api(name):",
        "    def blocked(*args, **kwargs):",
        "        raise RuntimeError(f'audit policy blocked {name}')",
        "    return blocked",
        "for api in (",
        "    'execv', 'execve', 'execl', 'execle', 'execlp', 'execlpe',",
        "    'execvp', 'execvpe', 'fork', 'forkpty', 'mkfifo', 'posix_spawn',",
        "    'posix_spawnp', 'spawnl', 'spawnle', 'spawnlp', 'spawnlpe',",
        "    'spawnv', 'spawnve', 'spawnvp', 'spawnvpe', 'system',",
        "):",
        "    if hasattr(os, api):",
        "        setattr(os, api, blocked_api(f'os.{api}'))",
        "import subprocess",
        "subprocess.Popen = blocked_api('subprocess.Popen')",
    ]
    if remove_no_follow:
        source.extend(["import os", "del os.O_NOFOLLOW"])
    if forbid_json_parsing:
        source.extend(
            [
                "import json",
                "def blocked_json(*args, **kwargs):",
                "    raise AssertionError('request JSON parsed before hash verification')",
                "json.loads = blocked_json",
            ]
        )
    if forbid_imports:
        source.extend(
            [
                "import builtins",
                "forbidden = (",
                "    'nautilus_trader', 'nautilustrader', 'provider', 'worker',",
                "    'transport', 'ingestion', 'database', 'sqlalchemy', 'psycopg',",
                "    'alembic', 'asyncpg', 'broker', 'exchange', 'ccxt', 'alpaca',",
                "    'ib_insync', 'service', 'redis',",
                ")",
                "original_import = builtins.__import__",
                "def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):",
                "    if any(item in name.casefold() for item in forbidden):",
                "        raise AssertionError(f'forbidden import: {name}')",
                "    return original_import(name, globals, locals, fromlist, level)",
                "builtins.__import__ = guarded_import",
                "import importlib.abc",
                "class ForbiddenImportFinder(importlib.abc.MetaPathFinder):",
                "    def find_spec(self, fullname, path=None, target=None):",
                "        if any(item in fullname.casefold() for item in forbidden):",
                "            raise AssertionError(f'forbidden import: {fullname}')",
                "        return None",
                "sys.meta_path.insert(0, ForbiddenImportFinder())",
            ]
        )
    (tmp_path / "sitecustomize.py").write_text("\n".join(source) + "\n", encoding="utf-8")
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(tmp_path)
    return environment


def _filesystem_snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_capabilities_are_closed_and_canonical() -> None:
    result = _canonical_output(_run("capabilities"))

    assert result == {
        "schema_version": "1.0.0",
        "engine_id": "trading-agent-nautilus-fixture-v1",
        "engine_version": "fixture-1.0.0",
        "supported_commands": list(contracts.COMMAND_TYPES),
        "supported_event_families": [family.value for family in contracts.EventFamily],
        "supported_modes": ["BACKTEST", "PAPER"],
    }
    assert "LIVE" not in result["supported_modes"]


def test_validate_request_requires_hash_before_parsing_and_emits_canonical_envelope(
    tmp_path: Path,
) -> None:
    request = _request()
    request_path, sidecar_path = _request_files(tmp_path, request)

    validated = _canonical_output(
        _run("validate-request", str(request_path), str(sidecar_path))
    )

    assert validated == contracts.EngineCommandEnvelope.model_validate_json(
        json.dumps(request)
    ).model_dump(mode="json")

    bad_hash = _run("validate-request", str(request_path), str(sidecar_path) + ".missing")
    _assert_rejected(bad_hash)


@pytest.mark.parametrize(
    ("request_update", "sidecar"),
    [
        ({}, "0" * 64),
        ({"stream_sequence": 0}, None),
    ],
)
def test_validate_request_rejects_bad_hash_or_invalid_envelope_without_result(
    tmp_path: Path, request_update: dict[str, object], sidecar: str | None
) -> None:
    request = _request()
    request.update(request_update)
    request_path, sidecar_path = _request_files(tmp_path, request, sidecar=sidecar)

    completed = _run("validate-request", str(request_path), str(sidecar_path))

    _assert_rejected(completed)


@pytest.mark.parametrize("input_name", ["request", "sidecar"])
@pytest.mark.parametrize("input_kind", ["symlink", "directory"])
def test_request_inputs_must_be_regular_non_symlink_files(
    tmp_path: Path, input_name: str, input_kind: str
) -> None:
    request_path, sidecar_path = _request_files(tmp_path, _request())
    invalid_path = tmp_path / f"invalid-{input_name}"
    if input_kind == "symlink":
        invalid_path.symlink_to(
            request_path if input_name == "request" else sidecar_path
        )
    else:
        invalid_path.mkdir()

    completed = _run(
        "validate-request",
        str(invalid_path if input_name == "request" else request_path),
        str(invalid_path if input_name == "sidecar" else sidecar_path),
    )

    _assert_rejected(completed)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO regression is POSIX-only")
@pytest.mark.parametrize("input_name", ["request", "sidecar"])
def test_request_inputs_reject_fifo_without_hanging(tmp_path: Path, input_name: str) -> None:
    request_path, sidecar_path = _request_files(tmp_path, _request())
    fifo_path = tmp_path / f"{input_name}.fifo"
    try:
        os.mkfifo(fifo_path)
    except OSError as exc:
        if exc.errno in {errno.ENOTSUP, errno.EOPNOTSUPP}:
            pytest.skip("the temporary filesystem does not support FIFOs")
        raise

    completed = _run(
        "validate-request",
        str(fifo_path if input_name == "request" else request_path),
        str(fifo_path if input_name == "sidecar" else sidecar_path),
        timeout=2,
    )

    _assert_rejected(completed)


def test_request_rejects_when_secure_no_follow_opening_is_unavailable(
    tmp_path: Path,
) -> None:
    request_path, sidecar_path = _request_files(tmp_path, _request())

    completed = _run(
        "validate-request",
        str(request_path),
        str(sidecar_path),
        environment=_restricted_environment(tmp_path, remove_no_follow=True),
    )

    _assert_rejected(completed)
    assert "no-follow" in completed.stderr


@pytest.mark.parametrize(
    "sidecar_bytes",
    [
        ("a" * 64).upper().encode("ascii"),
        (("a" * 64) + " " + ("b" * 64)).encode("ascii"),
        b"\xff" * 64,
    ],
)
def test_request_sidecar_requires_one_lowercase_ascii_hash_token(
    tmp_path: Path, sidecar_bytes: bytes
) -> None:
    request_path, sidecar_path = _request_files(tmp_path, _request())
    sidecar_path.write_bytes(sidecar_bytes)

    _assert_rejected(_run("validate-request", str(request_path), str(sidecar_path)))


def test_hash_mismatch_rejects_malformed_request_before_json_parsing(tmp_path: Path) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_bytes(b"{ not valid JSON")
    sidecar_path = tmp_path / "request.sha256"
    sidecar_path.write_text("0" * 64, encoding="ascii")

    completed = _run(
        "validate-request",
        str(request_path),
        str(sidecar_path),
        environment=_restricted_environment(tmp_path, forbid_json_parsing=True),
    )

    _assert_rejected(completed)
    assert "request SHA-256 does not match request bytes" in completed.stderr
    assert "request JSON parsed before hash verification" not in completed.stderr


@pytest.mark.parametrize(
    ("subcommand", "command_type", "event_type"),
    [
        ("backtest-fixture", "RunBacktest", "BacktestFixtureCompleted"),
        ("paper-fixture", "StartPaperEngine", "PaperFixtureReady"),
    ],
)
def test_fixture_commands_emit_deterministic_lifecycle_events(
    tmp_path: Path, subcommand: str, command_type: str, event_type: str
) -> None:
    request = _request(command_type)
    request_path, sidecar_path = _request_files(tmp_path, request)

    first = _canonical_output(_run(subcommand, str(request_path), str(sidecar_path)))
    second = _canonical_output(_run(subcommand, str(request_path), str(sidecar_path)))

    assert first == second
    assert first == {
        **{
            key: value
            for key, value in request.items()
            if key
            in {
                "correlation_id",
                "engine_run_id",
                "event_time",
                "initialization_time",
                "schema_version",
                "producer_identity",
                "source_commit",
                "config_digest",
            }
        },
        "message_id": str(uuid5(UUID(str(request["message_id"])), event_type)),
        "causation_id": request["message_id"],
        "stream_sequence": request["stream_sequence"] + 1,
        "payload": {
            "event_type": event_type,
            "family": "ENGINE_LIFECYCLE",
            "attributes": [],
        },
        "payload_digest": contracts.payload_digest(
            {
                "event_type": event_type,
                "family": "ENGINE_LIFECYCLE",
                "attributes": [],
            }
        ),
    }
    assert contracts.EngineEventEnvelope.model_validate_json(json.dumps(first))


@pytest.mark.parametrize(
    ("subcommand", "command_type"),
    [
        ("backtest-fixture", "StartPaperEngine"),
        ("paper-fixture", "RunBacktest"),
    ],
)
def test_fixture_commands_reject_a_different_command_type(
    tmp_path: Path, subcommand: str, command_type: str
) -> None:
    request_path, sidecar_path = _request_files(tmp_path, _request(command_type))

    completed = _run(subcommand, str(request_path), str(sidecar_path))

    _assert_rejected(completed)


@pytest.mark.parametrize(
    "subcommand",
    ["capabilities", "validate-request", "backtest-fixture", "paper-fixture"],
)
def test_every_cli_path_runs_without_network_access(tmp_path: Path, subcommand: str) -> None:
    arguments = _valid_arguments(tmp_path, subcommand)

    _canonical_output(
        _run(*arguments, environment=_restricted_environment(tmp_path))
    )


@pytest.mark.parametrize(
    "subcommand",
    ["capabilities", "validate-request", "backtest-fixture", "paper-fixture"],
)
def test_every_cli_path_writes_no_files(tmp_path: Path, subcommand: str) -> None:
    arguments = _valid_arguments(tmp_path, subcommand)
    before = _filesystem_snapshot(tmp_path)

    _canonical_output(_run(*arguments, cwd=tmp_path))

    assert _filesystem_snapshot(tmp_path) == before


@pytest.mark.parametrize(
    "subcommand",
    ["capabilities", "validate-request", "backtest-fixture", "paper-fixture"],
)
def test_every_cli_path_uses_no_forbidden_engine_or_runtime_imports(
    tmp_path: Path, subcommand: str
) -> None:
    arguments = _valid_arguments(tmp_path, subcommand)

    _canonical_output(
        _run(
            *arguments,
            environment=_restricted_environment(tmp_path, forbid_imports=True),
        )
    )


@pytest.mark.parametrize(
    "subcommand",
    ["capabilities", "validate-request", "backtest-fixture", "paper-fixture"],
)
def test_every_cli_path_runs_in_bubblewrap_read_only_network_namespace(
    tmp_path: Path, subcommand: str
) -> None:
    arguments = _valid_arguments(tmp_path, subcommand)

    _canonical_output(_run_in_bubblewrap(*arguments, timeout=5))


@pytest.mark.parametrize(
    "subcommand",
    ["validate-request", "backtest-fixture", "paper-fixture"],
)
def test_request_cli_rejections_run_in_bubblewrap_read_only_network_namespace(
    tmp_path: Path, subcommand: str
) -> None:
    if subcommand == "validate-request":
        request_path, sidecar_path = _request_files(tmp_path, _request())
        sidecar_path.write_text("0" * 64, encoding="ascii")
    else:
        wrong_command = (
            "StartPaperEngine" if subcommand == "backtest-fixture" else "RunBacktest"
        )
        request_path, sidecar_path = _request_files(tmp_path, _request(wrong_command))

    _assert_rejected(
        _run_in_bubblewrap(
            subcommand,
            str(request_path),
            str(sidecar_path),
            timeout=5,
        )
    )


def test_bubblewrap_mount_allowlist_exposes_only_private_writable_mounts(
    tmp_path: Path,
) -> None:
    request_path, sidecar_path = _request_files(tmp_path, _request())
    command = _bubblewrap_command([str(Path(sys.executable)), "-c", "pass"])
    assert not any(
        command[index : index + 3] == ["--ro-bind", "/", "/"]
        for index in range(len(command) - 2)
    )
    probe = """
import hashlib
import json
import os
import sys
from pathlib import Path

records = []
for line in Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
    fields = line.split()
    separator = fields.index("-")
    records.append(
        {
            "mount_point": fields[4],
            "options": fields[5].split(","),
            "filesystem": fields[separator + 1],
            "source": fields[separator + 2],
        }
    )
leak_paths = (
    "/run/user",
    "/mnt/c",
    "/var/lib/docker",
    "/var/lib/containerd",
    "/usr/lib/wsl/lib",
)
print(
    json.dumps(
        {
            "mounts": records,
            "cli_readable": Path(sys.argv[1]).is_file(),
            "request_digest": hashlib.sha256(
                Path("/inputs/request.json").read_bytes()
            ).hexdigest(),
            "sidecar_digest": hashlib.sha256(
                Path("/inputs/request.sha256").read_bytes()
            ).hexdigest(),
            "leak_writable": {
                path: Path(path).exists() and os.access(path, os.W_OK)
                for path in leak_paths
            },
        },
        sort_keys=True,
    )
)
"""
    completed = _run_python_in_bubblewrap(
        probe, request_path, sidecar_path, str(CLI)
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    topology = json.loads(completed.stdout)
    mounts = topology["mounts"]
    assert isinstance(mounts, list)
    root_mounts = [
        mount
        for mount in mounts
        if isinstance(mount, dict) and mount["mount_point"] == "/"
    ]
    assert len(root_mounts) == 1
    assert root_mounts[0]["filesystem"] == "tmpfs"
    assert root_mounts[0]["source"] == "tmpfs"
    assert "rw" in root_mounts[0]["options"]
    for mount in mounts:
        assert isinstance(mount, dict)
        if "rw" not in mount["options"]:
            continue
        mount_point = mount["mount_point"]
        assert (
            mount_point == "/" and mount["filesystem"] == "tmpfs"
        ) or mount_point == "/tmp" or mount_point.startswith(("/proc", "/dev"))
    assert topology["cli_readable"] is True
    assert topology["request_digest"] == hashlib.sha256(request_path.read_bytes()).hexdigest()
    assert topology["sidecar_digest"] == hashlib.sha256(sidecar_path.read_bytes()).hexdigest()
    assert topology["leak_writable"] == {
        "/run/user": False,
        "/mnt/c": False,
        "/var/lib/docker": False,
        "/var/lib/containerd": False,
        "/usr/lib/wsl/lib": False,
    }


@pytest.mark.parametrize(
    "subcommand",
    ["validate-request", "backtest-fixture", "paper-fixture"],
)
def test_request_cli_rejections_run_under_audit_guard(
    tmp_path: Path, subcommand: str
) -> None:
    command_type = "RunBacktest"
    if subcommand == "validate-request":
        request_path, sidecar_path = _request_files(tmp_path, _request(command_type))
        sidecar_path.write_text("0" * 64, encoding="ascii")
    else:
        wrong_command = (
            "StartPaperEngine" if subcommand == "backtest-fixture" else "RunBacktest"
        )
        request_path, sidecar_path = _request_files(tmp_path, _request(wrong_command))

    _assert_rejected(
        _run(
            subcommand,
            str(request_path),
            str(sidecar_path),
            environment=_restricted_environment(tmp_path),
        )
    )


@pytest.mark.parametrize(
    "program",
    [
        "import socket; socket.socket()",
        "import _socket; _socket.socket()",
        "import socket; socket.getaddrinfo('localhost', 80)",
    ],
)
def test_audit_guard_blocks_network_operations(tmp_path: Path, program: str) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        check=False,
        text=True,
        env=_restricted_environment(tmp_path),
    )

    assert completed.returncode != 0
    assert "audit policy blocked socket." in completed.stderr


def test_audit_guard_blocks_socket_connect_after_construction(tmp_path: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "import socket; socket.socket().connect(('127.0.0.1', 9))",
        ],
        capture_output=True,
        check=False,
        text=True,
        env=_restricted_environment(tmp_path, allow_socket_construction=True),
    )

    assert completed.returncode != 0
    assert "audit policy blocked socket.connect" in completed.stderr


@pytest.mark.parametrize(
    ("api", "program", "output_name"),
    [
        ("forkpty", "import os; os.forkpty()", None),
        ("mkfifo", "import os; os.mkfifo('blocked-fifo')", "blocked-fifo"),
    ],
)
def test_python_guard_blocks_unaudited_process_and_filesystem_bypasses(
    tmp_path: Path, api: str, program: str, output_name: str | None
) -> None:
    if not hasattr(os, api):
        pytest.skip(f"os.{api} is unavailable on this host")

    completed = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        check=False,
        cwd=tmp_path,
        text=True,
        env=_restricted_environment(tmp_path),
    )

    assert completed.returncode != 0
    assert f"audit policy blocked os.{api}" in completed.stderr
    if output_name is not None:
        assert not (tmp_path / output_name).exists()


def test_audit_guard_blocks_absolute_path_write_attempt(tmp_path: Path) -> None:
    target = tmp_path / "outside-current-directory" / "blocked.txt"
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from pathlib import Path; import sys; "
                "Path(sys.argv[1]).write_bytes(b'x')"
            ),
            str(target),
        ],
        capture_output=True,
        check=False,
        cwd=tmp_path,
        text=True,
        env=_restricted_environment(tmp_path),
    )

    assert completed.returncode != 0
    assert "audit policy blocked write-capable open" in completed.stderr
    assert not target.exists()


@pytest.mark.parametrize(
    "program",
    [
        "open('blocked.txt', 'wb').write(b'x')",
        "import os; os.mkdir('blocked-directory')",
        "import os; os.remove('preexisting')",
        "import subprocess; subprocess.run(['true'], check=True)",
        "import os; os.system('true')",
    ],
)
def test_audit_guard_blocks_writes_and_process_spawning(
    tmp_path: Path, program: str
) -> None:
    existing_file = tmp_path / "preexisting"
    existing_file.write_bytes(b"unchanged")
    completed = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        check=False,
        cwd=tmp_path,
        text=True,
        env=_restricted_environment(tmp_path),
    )

    assert completed.returncode != 0
    assert "audit policy blocked" in completed.stderr
    assert not (tmp_path / "blocked.txt").exists()
    assert not (tmp_path / "blocked-directory").exists()
    assert existing_file.read_bytes() == b"unchanged"


@pytest.mark.parametrize("subcommand", ["live", "unknown-subcommand"])
def test_cli_rejects_live_and_unknown_subcommands(subcommand: str) -> None:
    _assert_rejected(_run(subcommand))
