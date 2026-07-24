from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from services.safety_state_exporter.exporter import (
    CANONICAL_SOURCE_ROOT,
    DEFAULT_SNAPSHOT_PATH,
    EXPORT_INTERVAL_SECONDS,
    MOUNTED_SOURCE_ROOT,
    SNAPSHOT_TTL_SECONDS,
    SafetyStateExporter,
    source_fingerprint,
)


COMMIT = "a" * 40
NOW = datetime(2026, 7, 12, 16, 0, tzinfo=UTC)


@pytest.fixture
def tmp_path():
    path = Path(tempfile.mkdtemp(prefix="phase4b-exporter-", dir="/home/thenam176/.cache"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _exporter(
    tmp_path: Path,
    *,
    mode: str | None = "paper",
    kill: str | None = None,
    gates: dict[str, str] | None = None,
) -> SafetyStateExporter:
    legacy_root = tmp_path / "legacy"
    legacy_root.mkdir(mode=0o700)
    mounted_root = tmp_path / "runtime" / "safety-sources"
    mounted_root.mkdir(mode=0o700, parents=True)
    if mode is not None:
        mode_path = mounted_root / ".mode"
        mode_path.write_text(mode, encoding="utf-8")
        mode_path.chmod(0o600)
    if kill is not None:
        kill_path = mounted_root / ".kill_switch"
        kill_path.write_text(kill, encoding="utf-8")
        kill_path.chmod(0o600)
    output_dir = tmp_path / "runtime" / "safety-output"
    output_dir.mkdir(mode=0o700)
    return SafetyStateExporter(
        canonical_source_root=legacy_root,
        mounted_source_root=mounted_root,
        output_path=output_dir / "safety-state.json",
        exporter_commit=COMMIT,
        gate_source=gates
        or {
            "LIVE_EXECUTION_ENABLED": "false",
            "LIVE_TRADING_APPROVED": "false",
        },
        clock=lambda: NOW,
    )


def _read(exporter: SafetyStateExporter) -> dict[str, object]:
    exporter.export_once()
    return json.loads(exporter.output_path.read_text(encoding="utf-8"))


def test_exporter_writes_exact_safe_paper_snapshot_with_bounded_timestamps(tmp_path: Path) -> None:
    exporter = _exporter(tmp_path)

    document = _read(exporter)

    assert document == {
        "schema_version": 1,
        "exporter_commit": COMMIT,
        "generated_at": "2026-07-12T16:00:00Z",
        "expires_at": "2026-07-12T16:00:06Z",
        "requested_mode": "PAPER",
        "effective_mode": "PAPER",
        "live_execution_enabled": False,
        "live_trading_approved": False,
        "kill_switch_state": "INACTIVE",
        "source_fingerprint": source_fingerprint(exporter.canonical_source_root),
    }
    assert SNAPSHOT_TTL_SECONDS == 6
    assert EXPORT_INTERVAL_SECONDS == 2
    assert exporter.output_path.stat().st_mode & 0o777 == 0o600


def test_exporter_atomically_replaces_the_snapshot(tmp_path: Path, monkeypatch) -> None:
    exporter = _exporter(tmp_path)
    exporter.export_once()
    first_inode = exporter.output_path.stat().st_ino
    replacements: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def observed_replace(source, destination, *args, **kwargs):
        replacements.append((Path(source), Path(destination)))
        source_path = Path(f"/proc/self/fd/{kwargs['src_dir_fd']}") / source
        assert source_path.stat().st_mode & 0o777 == 0o600
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr("services.safety_state_exporter.exporter.os.replace", observed_replace)
    exporter.export_once()

    assert exporter.output_path.stat().st_ino != first_inode
    assert replacements and replacements[-1][1] == Path(exporter.output_path.name)
    assert list(exporter.output_path.parent.glob("*.tmp")) == []


def test_exporter_opens_only_mounted_allowlist_and_never_canonical_legacy_root(
    tmp_path: Path, monkeypatch,
) -> None:
    exporter = _exporter(tmp_path)
    for root in (exporter.canonical_source_root, exporter.mounted_source_root):
        credential = root / ".env"
        credential.write_text("EXCHANGE_API_KEY=do-not-read", encoding="utf-8")
        credential.chmod(0o600)
    opened: list[tuple[str, int]] = []
    real_open = os.open

    def observed_open(path, flags, *args, **kwargs):
        opened.append((os.fspath(path), flags))
        return real_open(path, flags, *args, **kwargs)

    def forbidden_enumeration(*_args, **_kwargs):
        raise AssertionError("legacy root enumeration is forbidden")

    monkeypatch.setattr("services.safety_state_exporter.exporter.os.open", observed_open)
    monkeypatch.setattr("services.safety_state_exporter.exporter.os.listdir", forbidden_enumeration)
    monkeypatch.setattr("services.safety_state_exporter.exporter.os.scandir", forbidden_enumeration)
    monkeypatch.setattr("services.safety_state_exporter.exporter.os.walk", forbidden_enumeration)

    _read(exporter)

    opened_paths = [path for path, _flags in opened]
    assert ".env" not in opened_paths
    assert "exchange" not in " ".join(opened_paths).lower()
    relative_reads = {
        path for path, flags in opened
        if not os.path.isabs(path) and flags & os.O_ACCMODE == os.O_RDONLY
    }
    assert relative_reads <= {".mode", ".kill_switch"}
    assert os.fspath(exporter.canonical_source_root) not in opened_paths
    assert os.fspath(exporter.mounted_source_root) in opened_paths


def test_composition_discards_every_environment_value_except_explicit_gate_inputs() -> None:
    from services.safety_state_exporter import main

    exporter = main.build_exporter({
        "TRADING_SAFETY_EXPORTER_COMMIT": COMMIT,
        "LIVE_EXECUTION_ENABLED": "false",
        "LIVE_TRADING_APPROVED": "false",
        "EXCHANGE_API_KEY": "must-not-cross-boundary",
        "DATABASE_PASSWORD": "must-not-cross-boundary",
    })

    assert exporter.gate_source == {
        "LIVE_EXECUTION_ENABLED": "false",
        "LIVE_TRADING_APPROVED": "false",
    }
    assert exporter.canonical_source_root == CANONICAL_SOURCE_ROOT
    assert exporter.mounted_source_root == MOUNTED_SOURCE_ROOT
    assert exporter.output_path == DEFAULT_SNAPSHOT_PATH


def test_explicit_once_mode_exports_once_without_entering_long_running_loop(monkeypatch) -> None:
    from services.safety_state_exporter import main

    calls: list[str] = []
    exporter = type("Exporter", (), {"export_once": lambda self: calls.append("once")})()
    monkeypatch.setattr(main, "build_exporter", lambda: exporter)
    monkeypatch.setattr(main, "serve", lambda _exporter: (_ for _ in ()).throw(AssertionError("loop")))
    monkeypatch.setattr("sys.argv", ["safety-exporter", "--once"])

    assert main.main() == 0
    assert calls == ["once"]


def test_once_cli_rejects_path_and_gate_overrides(monkeypatch) -> None:
    from services.safety_state_exporter import main

    monkeypatch.setattr("sys.argv", ["safety-exporter", "--once", "--mode-path", "/tmp/.mode"])
    with pytest.raises(SystemExit):
        main.main()


def test_default_snapshot_path_is_the_exact_euid_runtime_directory() -> None:
    assert DEFAULT_SNAPSHOT_PATH == Path(
        f"/run/user/{os.geteuid()}/trading-agent/safety-state.json"
    )


def test_source_mount_is_a_fixed_private_sibling_beneath_the_runtime_root() -> None:
    runtime_root = Path(f"/run/user/{os.geteuid()}/trading-agent")
    assert MOUNTED_SOURCE_ROOT == runtime_root / "safety-sources"
    assert DEFAULT_SNAPSHOT_PATH == runtime_root / "safety-state.json"
    assert CANONICAL_SOURCE_ROOT == Path("/home/thenam176/.hermes/crypto-research")


def test_composition_rejects_explicit_snapshot_path_override() -> None:
    from services.safety_state_exporter import main

    with pytest.raises(ValueError, match="snapshot path override"):
        main.build_exporter({
            "TRADING_SAFETY_EXPORTER_COMMIT": COMMIT,
            "LIVE_EXECUTION_ENABLED": "false",
            "LIVE_TRADING_APPROVED": "false",
            "TRADING_SAFETY_STATE_PATH": "/tmp/redirected-safety.json",
        })


@pytest.mark.parametrize("key", [
    "TRADING_CANONICAL_SAFETY_ROOT",
    "TRADING_MOUNTED_SAFETY_ROOT",
    "TRADING_SAFETY_SOURCE_ROOT",
])
def test_composition_rejects_canonical_or_mounted_source_overrides(key: str) -> None:
    from services.safety_state_exporter import main

    with pytest.raises(ValueError, match="source root override"):
        main.build_exporter({
            "TRADING_SAFETY_EXPORTER_COMMIT": COMMIT,
            "LIVE_EXECUTION_ENABLED": "false",
            "LIVE_TRADING_APPROVED": "false",
            key: "/tmp/redirected-source",
        })


def test_xdg_runtime_environment_cannot_redirect_the_fixed_snapshot_path() -> None:
    from services.safety_state_exporter import main

    exporter = main.build_exporter({
        "TRADING_SAFETY_EXPORTER_COMMIT": COMMIT,
        "LIVE_EXECUTION_ENABLED": "false",
        "LIVE_TRADING_APPROVED": "false",
        "XDG_RUNTIME_DIR": "/tmp/attacker-runtime",
    })

    assert exporter.output_path == DEFAULT_SNAPSHOT_PATH


def test_exporter_rejects_wrong_runtime_directory_owner(
    tmp_path: Path, monkeypatch,
) -> None:
    exporter = _exporter(tmp_path)
    real_fstat = os.fstat

    def unsafe_runtime_owner(descriptor):
        metadata = real_fstat(descriptor)
        target = Path(os.readlink(f"/proc/self/fd/{descriptor}"))
        if target == exporter.output_path.parent:
            values = list(metadata)
            values[4] = metadata.st_uid + 1
            return os.stat_result(values)
        return metadata

    monkeypatch.setattr(
        "services.safety_state_exporter.exporter.os.fstat",
        unsafe_runtime_owner,
    )

    with pytest.raises(OSError, match="snapshot directory is unsafe"):
        exporter.export_once()

    assert not exporter.output_path.exists()


@pytest.mark.parametrize("unsafe", ["mode", "owner", "symlink"])
def test_exporter_blocks_unsafe_mounted_source_root(
    tmp_path: Path, monkeypatch, unsafe: str,
) -> None:
    exporter = _exporter(tmp_path)
    mounted = exporter.mounted_source_root
    if unsafe == "mode":
        mounted.chmod(0o750)
    elif unsafe == "symlink":
        real = mounted.with_name("real-safety-sources")
        mounted.rename(real)
        mounted.symlink_to(real, target_is_directory=True)
    else:
        real_fstat = os.fstat

        def wrong_owner(descriptor):
            metadata = real_fstat(descriptor)
            target = Path(os.readlink(f"/proc/self/fd/{descriptor}"))
            if target == mounted:
                values = list(metadata)
                values[4] = metadata.st_uid + 1
                return os.stat_result(values)
            return metadata

        monkeypatch.setattr(
            "services.safety_state_exporter.exporter.os.fstat", wrong_owner,
        )

    with pytest.raises(OSError):
        exporter.snapshot()


def test_missing_mode_is_unknown_while_missing_kill_sentinel_is_inactive(
    tmp_path: Path,
) -> None:
    document = _read(_exporter(tmp_path, mode=None, kill=None))

    assert document["requested_mode"] == "UNKNOWN"
    assert document["effective_mode"] == "UNKNOWN"
    assert document["kill_switch_state"] == "INACTIVE"


def test_source_fingerprint_uses_canonical_legacy_paths_not_mount_paths(
    tmp_path: Path,
) -> None:
    exporter = _exporter(tmp_path)
    document = _read(exporter)

    assert document["source_fingerprint"] == source_fingerprint(
        exporter.canonical_source_root
    )
    assert document["source_fingerprint"] != source_fingerprint(
        exporter.mounted_source_root
    )


@pytest.mark.parametrize(
    ("mode", "kill", "expected_mode", "expected_kill", "expected_effective"),
    [
        ("paper", "2026-07-12T15:00:00Z: operator drill", "PAPER", "ACTIVE", "PAPER"),
        ("paper", "invalid", "PAPER", "UNKNOWN", "PAPER"),
        ("live", None, "LIVE", "INACTIVE", "PAPER"),
        ("invalid", None, "UNKNOWN", "INACTIVE", "UNKNOWN"),
    ],
)
def test_exporter_resolves_active_unknown_and_effective_state(
    tmp_path: Path,
    mode: str,
    kill: str | None,
    expected_mode: str,
    expected_kill: str,
    expected_effective: str,
) -> None:
    document = _read(_exporter(tmp_path, mode=mode, kill=kill))

    assert document["requested_mode"] == expected_mode
    assert document["kill_switch_state"] == expected_kill
    assert document["effective_mode"] == expected_effective


def test_exporter_uses_one_clock_sample_and_exact_six_second_expiry(tmp_path: Path) -> None:
    samples = iter((NOW, NOW + timedelta(days=1)))
    exporter = _exporter(tmp_path)
    exporter.clock = lambda: next(samples)

    document = _read(exporter)

    generated = datetime.fromisoformat(str(document["generated_at"]).replace("Z", "+00:00"))
    expires = datetime.fromisoformat(str(document["expires_at"]).replace("Z", "+00:00"))
    assert expires - generated == timedelta(seconds=SNAPSHOT_TTL_SECONDS)
