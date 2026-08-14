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
VENV_ROOT = Path(sys.executable).parent.parent.resolve(strict=True)
CLI_SOURCE = CLI.resolve(strict=True)
RESOLVED_INTERPRETER = Path(sys.executable).resolve(strict=True)
VENV_SITE_PACKAGES = (
    VENV_ROOT / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages"
).resolve(strict=True)
FORBIDDEN_BWRAP_BIND_ROOTS = frozenset(
    {
        Path("/"),
        Path("/home"),
        Path("/etc"),
        Path("/tmp"),
        Path("/var"),
        Path("/run"),
        Path("/mnt"),
    }
)


def _resolved_cpython_install_root(executable: Path) -> Path:
    resolved = executable.resolve(strict=True)
    if not resolved.is_file() or resolved.parent.name != "bin":
        raise ValueError(f"resolved interpreter is not in a CPython bin directory: {resolved}")
    root = resolved.parent.parent
    if not (root / "lib").is_dir():
        raise ValueError(f"resolved interpreter has no CPython lib root: {resolved}")
    return root


def _validated_bwrap_bind(source: Path, target: Path) -> tuple[Path, Path]:
    resolved_source = source.resolve(strict=True)
    if resolved_source in FORBIDDEN_BWRAP_BIND_ROOTS:
        raise ValueError(f"forbidden Bubblewrap bind source: {resolved_source}")
    if target in FORBIDDEN_BWRAP_BIND_ROOTS:
        raise ValueError(f"forbidden Bubblewrap bind target: {target}")
    if not target.is_absolute() or ".." in target.parts:
        raise ValueError(f"invalid Bubblewrap bind target: {target}")
    return resolved_source, target


def _additional_cpython_bind_root(root: Path) -> Path | None:
    if root in FORBIDDEN_BWRAP_BIND_ROOTS:
        raise ValueError(f"forbidden CPython install root: {root}")
    if root.is_relative_to(Path("/usr")):
        return None
    return root


CPYTHON_INSTALL_ROOT = _resolved_cpython_install_root(RESOLVED_INTERPRETER)
ADDITIONAL_CPYTHON_BIND_ROOT = _additional_cpython_bind_root(CPYTHON_INSTALL_ROOT)


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
    targets = [
        CLI,
        Path(sys.executable),
        VENV_ROOT / "bin" / "python3",
        VENV_ROOT / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages",
    ]
    if ADDITIONAL_CPYTHON_BIND_ROOT is not None:
        targets.append(ADDITIONAL_CPYTHON_BIND_ROOT)
    directories = {Path("/inputs")}
    for target in targets:
        parent = target.parent
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
    bind_pairs = [
        _validated_bwrap_bind(Path("/usr"), Path("/usr")),
        _validated_bwrap_bind(CLI_SOURCE, CLI),
        _validated_bwrap_bind(RESOLVED_INTERPRETER, Path(sys.executable)),
        _validated_bwrap_bind(RESOLVED_INTERPRETER, VENV_ROOT / "bin" / "python3"),
        _validated_bwrap_bind(
            VENV_SITE_PACKAGES,
            VENV_ROOT
            / "lib"
            / f"python{sys.version_info.major}.{sys.version_info.minor}"
            / "site-packages",
        ),
    ]
    if ADDITIONAL_CPYTHON_BIND_ROOT is not None:
        bind_pairs.append(
            _validated_bwrap_bind(
                ADDITIONAL_CPYTHON_BIND_ROOT, ADDITIONAL_CPYTHON_BIND_ROOT
            )
        )
    arguments = [
        BWRAP,
        "--die-with-parent",
        "--unshare-user",
        "--unshare-net",
        "--new-session",
        *_bubblewrap_mount_parent_arguments(),
        "--ro-bind",
        str(bind_pairs[0][0]),
        str(bind_pairs[0][1]),
        "--symlink",
        "/usr/lib",
        "/lib",
        "--symlink",
        "/usr/lib64",
        "/lib64",
    ]
    for source, target in bind_pairs[1:]:
        arguments.extend(("--ro-bind", str(source), str(target)))
    if request_path is not None and sidecar_path is not None:
        request_source, request_target = _validated_bwrap_bind(
            request_path, Path("/inputs/request.json")
        )
        sidecar_source, sidecar_target = _validated_bwrap_bind(
            sidecar_path, Path("/inputs/request.sha256")
        )
        arguments.extend(
            (
                "--ro-bind",
                str(request_source),
                str(request_target),
                "--ro-bind",
                str(sidecar_source),
                str(sidecar_target),
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
            "--setenv",
            "PYTHONHOME",
            str(CPYTHON_INSTALL_ROOT),
            "--setenv",
            "PYTHONPATH",
            str(
                VENV_ROOT
                / "lib"
                / f"python{sys.version_info.major}.{sys.version_info.minor}"
                / "site-packages"
            ),
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
            [str(VENV_ROOT / "bin" / "python3"), "-c", program, *program_arguments],
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
    sitecustomize_path = tmp_path / "sitecustomize.py"
    sitecustomize_path.write_text("\n".join(source) + "\n", encoding="utf-8")
    for cache_file in (tmp_path / "__pycache__").glob("sitecustomize.*.pyc"):
        cache_file.unlink()
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = str(tmp_path)
    return environment


def _filesystem_snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _is_mount_path_or_descendant(path: str, parent: str) -> bool:
    return path == parent or path.startswith(f"{parent}/")


def _assert_only_private_writable_mounts(mounts: list[dict[str, object]]) -> None:
    expected_private_mounts = {
        "/": ("tmpfs", "tmpfs"),
        "/tmp": ("tmpfs", "tmpfs"),
        "/proc": ("proc", "proc"),
        "/dev": ("tmpfs", "tmpfs"),
    }
    for mount_point, (filesystem, source) in expected_private_mounts.items():
        matching = [
            mount for mount in mounts if mount["mount_point"] == mount_point
        ]
        assert len(matching) == 1
        mount = matching[0]
        assert mount["filesystem"] == filesystem
        assert mount["source"] == source
        assert "rw" in mount["options"]

    allowed_descendant_filesystems = {
        "/proc": {("binfmt_misc", "binfmt_misc")},
        "/dev": {("devtmpfs", "none"), ("devpts", "devpts")},
    }
    for mount in mounts:
        if "rw" not in mount["options"]:
            continue
        mount_point = mount["mount_point"]
        assert isinstance(mount_point, str)
        if mount_point in expected_private_mounts:
            continue
        for parent, allowed_filesystems in allowed_descendant_filesystems.items():
            if _is_mount_path_or_descendant(mount_point, parent):
                assert (
                    mount["filesystem"],
                    mount["source"],
                ) in allowed_filesystems
                break
        else:
            raise AssertionError(f"unexpected writable mount: {mount_point}")


def test_resolved_cpython_root_uses_relative_alias_target_not_raw_link_text(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    runtime_bin = runtime / "bin"
    runtime_bin.mkdir(parents=True)
    (runtime / "lib").mkdir()
    copied_interpreter = runtime_bin / "python3.11"
    shutil.copyfile(RESOLVED_INTERPRETER, copied_interpreter)
    relative_alias = tmp_path / "python"
    relative_alias.symlink_to(Path("runtime") / "bin" / "python3.11")

    root = _resolved_cpython_install_root(
        Path(os.path.relpath(relative_alias, Path.cwd()))
    )

    assert root == runtime.resolve(strict=True)
    assert _additional_cpython_bind_root(root) == runtime.resolve(strict=True)


def test_resolved_bin_interpreter_uses_existing_usr_mount() -> None:
    assert _additional_cpython_bind_root(Path("/usr")) is None


@pytest.mark.parametrize(
    ("interpreter", "resolved_root"),
    [
        (Path("/bin/python3.11"), Path("/usr")),
        (Path("/home/bin/python3.11"), Path("/home")),
        (Path("/etc/bin/python3.11"), Path("/etc")),
    ],
)
def test_interpreter_bind_selection_uses_only_resolved_install_roots(
    interpreter: Path, resolved_root: Path
) -> None:
    if resolved_root == Path("/usr"):
        assert _additional_cpython_bind_root(resolved_root) is None
    else:
        with pytest.raises(ValueError, match="forbidden"):
            _additional_cpython_bind_root(resolved_root)
    assert interpreter.name == "python3.11"


@pytest.mark.parametrize(
    "root",
    [Path("/"), Path("/home"), Path("/etc"), Path("/tmp"), Path("/var"), Path("/run")],
)
def test_bubblewrap_rejects_forbidden_broad_bind_roots(root: Path) -> None:
    with pytest.raises(ValueError, match="forbidden"):
        _additional_cpython_bind_root(root)
    with pytest.raises(ValueError, match="forbidden"):
        _validated_bwrap_bind(Path("/usr"), root)


def test_bubblewrap_builder_emits_only_validated_narrow_bind_pairs() -> None:
    command = _bubblewrap_command([str(Path(sys.executable)), "-c", "pass"])
    pairs = [
        (Path(command[index + 1]), Path(command[index + 2]))
        for index, argument in enumerate(command)
        if argument == "--ro-bind"
    ]

    assert pairs
    assert all(
        source not in FORBIDDEN_BWRAP_BIND_ROOTS
        and target not in FORBIDDEN_BWRAP_BIND_ROOTS
        for source, target in pairs
    )


@pytest.mark.parametrize(
    "mount",
    [
        {
            "mount_point": "/tmp",
            "options": ["rw"],
            "filesystem": "ext4",
            "source": "/dev/sda",
        },
        {
            "mount_point": "/development",
            "options": ["rw"],
            "filesystem": "tmpfs",
            "source": "tmpfs",
        },
        {
            "mount_point": "/device",
            "options": ["rw"],
            "filesystem": "tmpfs",
            "source": "tmpfs",
        },
    ],
)
def test_mountinfo_proof_rejects_host_tmp_and_prefix_collision_mounts(
    mount: dict[str, object],
) -> None:
    private_mounts: list[dict[str, object]] = [
        {
            "mount_point": "/",
            "options": ["rw"],
            "filesystem": "tmpfs",
            "source": "tmpfs",
        },
        {
            "mount_point": "/tmp",
            "options": ["rw"],
            "filesystem": "tmpfs",
            "source": "tmpfs",
        },
        {
            "mount_point": "/proc",
            "options": ["rw"],
            "filesystem": "proc",
            "source": "proc",
        },
        {
            "mount_point": "/dev",
            "options": ["rw"],
            "filesystem": "tmpfs",
            "source": "tmpfs",
        },
    ]
    if mount["mount_point"] == "/tmp":
        private_mounts[1] = mount
    else:
        private_mounts.append(mount)

    with pytest.raises(AssertionError):
        _assert_only_private_writable_mounts(private_mounts)


@pytest.mark.parametrize(
    "mount",
    [
        {
            "mount_point": "/proc/sys/fs/binfmt_misc",
            "options": ["rw"],
            "filesystem": "binfmt_misc",
            "source": "binfmt_misc",
        },
        {
            "mount_point": "/dev/null",
            "options": ["rw"],
            "filesystem": "devtmpfs",
            "source": "none",
        },
        {
            "mount_point": "/dev/pts",
            "options": ["rw"],
            "filesystem": "devpts",
            "source": "devpts",
        },
    ],
)
def test_mountinfo_proof_accepts_observed_private_proc_and_dev_descendants(
    mount: dict[str, object],
) -> None:
    private_mounts: list[dict[str, object]] = [
        {
            "mount_point": "/",
            "options": ["rw"],
            "filesystem": "tmpfs",
            "source": "tmpfs",
        },
        {
            "mount_point": "/tmp",
            "options": ["rw"],
            "filesystem": "tmpfs",
            "source": "tmpfs",
        },
        {
            "mount_point": "/proc",
            "options": ["rw"],
            "filesystem": "proc",
            "source": "proc",
        },
        {
            "mount_point": "/dev",
            "options": ["rw"],
            "filesystem": "tmpfs",
            "source": "tmpfs",
        },
        mount,
    ]

    _assert_only_private_writable_mounts(private_mounts)


@pytest.mark.parametrize("mount_point", ["/dev/host-data", "/proc/host-data"])
def test_mountinfo_proof_rejects_host_backed_proc_and_dev_descendants(
    mount_point: str,
) -> None:
    private_mounts: list[dict[str, object]] = [
        {
            "mount_point": "/",
            "options": ["rw"],
            "filesystem": "tmpfs",
            "source": "tmpfs",
        },
        {
            "mount_point": "/tmp",
            "options": ["rw"],
            "filesystem": "tmpfs",
            "source": "tmpfs",
        },
        {
            "mount_point": "/proc",
            "options": ["rw"],
            "filesystem": "proc",
            "source": "proc",
        },
        {
            "mount_point": "/dev",
            "options": ["rw"],
            "filesystem": "tmpfs",
            "source": "tmpfs",
        },
        {
            "mount_point": mount_point,
            "options": ["rw"],
            "filesystem": "ext4",
            "source": "/dev/sda1",
        },
    ]

    with pytest.raises(AssertionError):
        _assert_only_private_writable_mounts(private_mounts)


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
    assert all(isinstance(mount, dict) for mount in mounts)
    _assert_only_private_writable_mounts(mounts)
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
    assert not list((tmp_path / "__pycache__").glob("sitecustomize.*.pyc"))


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
