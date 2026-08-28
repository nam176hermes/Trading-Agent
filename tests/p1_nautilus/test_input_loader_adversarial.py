from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]
sys.path.insert(0, str(ROOT / "engines/nautilus"))

from runtime_v1 import input_loader  # noqa: E402
from runtime_v1.input_loader import InputLoadError, load_inputs  # noqa: E402

from test_input_loader import canonical, sandbox  # noqa: E402


def test_rejects_missing_extra_symlink_fifo_and_hardlink_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact_root, _, _ = sandbox(tmp_path, monkeypatch)
    paths = tuple(artifact_root.iterdir())

    paths[0].unlink()
    with pytest.raises(InputLoadError):
        load_inputs()

    device_root = os.open("/dev", os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(InputLoadError):
            input_loader._read_artifact(device_root, "null", 1, "0" * 64)
    finally:
        os.close(device_root)

    artifact_root, _, _ = sandbox(tmp_path / "extra", monkeypatch)
    (artifact_root / "market_datа-confusable.json").write_bytes(b"{}\n")
    with pytest.raises(InputLoadError):
        load_inputs()

    artifact_root, _, _ = sandbox(tmp_path / "symlink", monkeypatch)
    target = next(artifact_root.iterdir())
    target.unlink()
    target.symlink_to("/dev/null")
    with pytest.raises(InputLoadError):
        load_inputs()

    artifact_root, _, _ = sandbox(tmp_path / "fifo", monkeypatch)
    target = next(artifact_root.iterdir())
    target.unlink()
    os.mkfifo(target, 0o400)
    with pytest.raises(InputLoadError):
        load_inputs()

    artifact_root, _, _ = sandbox(tmp_path / "hardlink", monkeypatch)
    target = next(artifact_root.iterdir())
    os.link(target, artifact_root.parent / "second-link")
    with pytest.raises(InputLoadError):
        load_inputs()


def test_rejects_request_sidecar_and_canonical_grammar_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox(tmp_path, monkeypatch)
    sidecar = Path(input_loader.SIDECAR)
    sidecar.chmod(0o600)
    sidecar.write_bytes(b"0" * 64 + b"\n")
    sidecar.chmod(0o400)
    with pytest.raises(InputLoadError, match="sidecar"):
        load_inputs()

    for index, bad in enumerate(
        (
            b'{"payload":{},"payload":{}}',
            b'\xef\xbb\xbf{}',
            b'{"stream_sequence":1.0}',
        )
    ):
        sandbox(tmp_path / f"request-{index}", monkeypatch)
        request = Path(input_loader.REQUEST)
        request.chmod(0o600)
        request.write_bytes(bad)
        request.chmod(0o400)
        sidecar = Path(input_loader.SIDECAR)
        sidecar.chmod(0o600)
        sidecar.write_bytes(hashlib.sha256(bad).hexdigest().encode() + b"\n")
        sidecar.chmod(0o400)
        with pytest.raises(InputLoadError):
            load_inputs()

def test_rejects_digest_drift_duplicate_json_bom_float_and_oversize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact_root, _, _ = sandbox(tmp_path, monkeypatch)
    target = next(artifact_root.iterdir())
    raw = target.read_bytes()
    target.chmod(0o600)
    target.write_bytes(raw[:-2] + b"0\n")
    target.chmod(0o400)
    with pytest.raises(InputLoadError):
        load_inputs()

    for index, bad in enumerate(
        (
            b'{"schema_version":"x","schema_version":"x"}\n',
            b'\xef\xbb\xbf{"schema_version":"x"}\n',
            b'{"schema_version":"x","value":1.0}\n',
            b"{" + b" " * 1_048_576 + b"}\n",
        )
    ):
        root = tmp_path / f"bad-{index}"
        artifact_root, _, request = sandbox(root, monkeypatch)
        target = next(
            path for path in artifact_root.iterdir() if path.name.startswith("engine_configuration-")
        )
        target.unlink()
        digest = hashlib.sha256(bad).hexdigest()
        replacement = artifact_root / f"engine_configuration-{digest}.json"
        replacement.write_bytes(bad)
        replacement.chmod(0o400)
        payload = dict(request["payload"])  # type: ignore[arg-type]
        reference = dict(payload["engine_configuration"])  # type: ignore[arg-type]
        reference["sha256"] = digest
        payload["engine_configuration"] = reference
        request["payload"] = payload
        request["payload_digest"] = hashlib.sha256(canonical(payload)).hexdigest()
        request["config_digest"] = hashlib.sha256(
            canonical(
                {
                    name: payload[name]
                    for name in (
                        "engine_configuration",
                        "instrument_catalog",
                        "strategy_configuration",
                    )
                }
            )
        ).hexdigest()
        request_raw = canonical(request)
        Path(input_loader.REQUEST).chmod(0o600)
        Path(input_loader.REQUEST).write_bytes(request_raw)
        Path(input_loader.REQUEST).chmod(0o400)
        Path(input_loader.SIDECAR).chmod(0o600)
        Path(input_loader.SIDECAR).write_bytes(
            hashlib.sha256(request_raw).hexdigest().encode() + b"\n"
        )
        Path(input_loader.SIDECAR).chmod(0o400)
        with pytest.raises(InputLoadError):
            load_inputs()


def test_rejects_inode_swap_and_truncation_during_descriptor_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact_root, _, _ = sandbox(tmp_path, monkeypatch)
    target = next(artifact_root.iterdir())
    real_stat = input_loader.os.stat
    swapped = False

    def swapping_stat(path: object, *args: object, **kwargs: object) -> os.stat_result:
        nonlocal swapped
        if not swapped and path == target.name and kwargs.get("dir_fd") is not None:
            swapped = True
            replacement = target.with_suffix(".replacement")
            replacement.write_bytes(target.read_bytes())
            replacement.chmod(0o400)
            replacement.replace(target)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(input_loader.os, "stat", swapping_stat)
    with pytest.raises(InputLoadError):
        load_inputs()

    artifact_root, _, _ = sandbox(tmp_path / "truncate", monkeypatch)
    target = next(artifact_root.iterdir())
    real_read = input_loader.os.read
    truncated = False

    def truncating_read(descriptor: int, size: int) -> bytes:
        nonlocal truncated
        if not truncated:
            truncated = True
            target.chmod(0o600)
            target.write_bytes(target.read_bytes()[:-1])
            target.chmod(0o400)
        return real_read(descriptor, size)

    monkeypatch.setattr(input_loader.os, "read", truncating_read)
    with pytest.raises(InputLoadError):
        load_inputs()
