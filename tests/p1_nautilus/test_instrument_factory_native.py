from __future__ import annotations

import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import stat
import subprocess
import tempfile
from typing import Iterator
from zipfile import ZipFile

import pytest


ROOT = Path(__file__).parents[2]
CATALOG = ROOT / "tests/fixtures/p1_nautilus/contracts/instrument-catalog.json"
G1_CLOSURE_SHA256 = "24f12b58cb0aba145e6d56146a71be874c5d9b214e7426eead9711131eaf1255"
G1_WHEEL_SHA256 = "ecc461d0f634c25db17e0fb79136c3bf0d513edd323d4f9adaaf84346e68b2fb"
G1_WHEEL_SIZE = 183_626_605
BWRAP = Path("/usr/bin/bwrap")
BWRAP_SHA256 = "52231e1caf55bcbc667b269f49c63599a6f7db4767ae6a039580d0ff853db712"


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


def _validate_closure(root: Path, manifest_path: Path, document: dict[str, object]) -> None:
    if (
        not stat.S_ISDIR(root.lstat().st_mode)
        or stat.S_IMODE(root.lstat().st_mode) != 0o500
        or not stat.S_ISREG(manifest_path.lstat().st_mode)
        or stat.S_IMODE(manifest_path.lstat().st_mode) != 0o400
        or manifest_path.lstat().st_nlink != 1
    ):
        raise AssertionError("G1 closure custody is mutable")
    records = document["files"]
    assert type(records) is list
    expected_files = {PurePosixPath("closure-manifest.json")}
    expected_directories = {PurePosixPath(".")}
    for record in records:
        assert type(record) is dict
        relative = PurePosixPath(record["path"])
        assert not relative.is_absolute() and ".." not in relative.parts
        path = root.joinpath(*relative.parts)
        status = path.lstat()
        assert stat.S_ISREG(status.st_mode) and status.st_nlink == 1
        assert stat.S_IMODE(status.st_mode) == int(record["mode"], 8)
        assert status.st_size == record["size"]
        assert _digest(path) == record["sha256"]
        expected_files.add(relative)
        parent = relative.parent
        while parent != PurePosixPath("."):
            expected_directories.add(parent)
            parent = parent.parent
    observed_files = {
        PurePosixPath(path.relative_to(root).as_posix())
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    observed_directories = {PurePosixPath(".")} | {
        PurePosixPath(path.relative_to(root).as_posix())
        for path in root.rglob("*")
        if path.is_dir()
    }
    observed_entries = {
        PurePosixPath(path.relative_to(root).as_posix()) for path in root.rglob("*")
    }
    assert observed_files == expected_files
    assert observed_directories == expected_directories
    assert observed_entries == (expected_files | expected_directories) - {
        PurePosixPath(".")
    }
    for relative in observed_directories:
        path = root if relative == PurePosixPath(".") else root.joinpath(*relative.parts)
        status = path.lstat()
        assert stat.S_ISDIR(status.st_mode) and stat.S_IMODE(status.st_mode) == 0o500


@contextmanager
def exact_g1_runtime() -> Iterator[tuple[Path, str] | None]:
    python = os.environ.get("P1_NAUTILUS_PYTHON")
    closure_manifest = os.environ.get("P1_NAUTILUS_CLOSURE_MANIFEST")
    legacy_site = os.environ.get("P1_NAUTILUS_SITE_PACKAGES")
    if python is None and closure_manifest is None and legacy_site is None:
        yield None
        return
    assert python is not None and closure_manifest is not None
    assert legacy_site is None
    assert _digest(BWRAP) == BWRAP_SHA256
    supplied_manifest = Path(closure_manifest)
    assert not supplied_manifest.is_symlink()
    manifest_path = supplied_manifest.resolve()
    assert _digest(manifest_path) == G1_CLOSURE_SHA256
    document = json.loads(manifest_path.read_bytes())
    root = manifest_path.parent
    _validate_closure(root, manifest_path, document)
    python_path = (root / "files/usr/bin/python3.12").resolve()
    assert Path(python).resolve() == python_path
    assert _digest(python_path) == document["python"]["executable_sha256"]
    assert document["engine"] == {
        "name": "nautilus_trader",
        "upstream_commit": "27a8e54e7ac3c57d6cbf8891f0283dfbaee97317",
        "upstream_tag": "v1.231.0",
        "version": "1.231.0",
    }
    records = document["runtime_wheels"]
    wheel_root = root / "files/engine/wheels"
    wheels = {path.name: path for path in wheel_root.glob("*.whl")}
    runtime_names = {record["filename"] for record in records}
    engine_names = {
        name for name in wheels if name.startswith("nautilus_trader-1.231.0-")
    }
    assert len(engine_names) == 1
    assert set(wheels) == runtime_names | engine_names
    for record in records:
        path = wheels[record["filename"]]
        assert path.stat().st_size == record["size"]
        assert _digest(path) == record["sha256"]
    engine_wheel = wheels[engine_names.pop()]
    assert engine_wheel.stat().st_size == G1_WHEEL_SIZE
    assert _digest(engine_wheel) == G1_WHEEL_SHA256
    with tempfile.TemporaryDirectory(prefix="p1-g1-site-", dir="/tmp") as directory:
        for path in wheels.values():
            with ZipFile(path) as archive:
                archive.extractall(directory)
        yield root, directory


def exact_g1_command(
    root: Path, site_packages: str, script: str, *inputs: tuple[Path, str]
) -> list[str]:
    command = [
        str(BWRAP),
        "--die-with-parent",
        "--unshare-all",
        "--new-session",
        "--tmpfs",
        "/",
        "--dev",
        "/dev",
        "--proc",
        "/proc",
        "--dir",
        "/engine",
        "--ro-bind",
        str(root / "files/engine/wheels"),
        "/engine/wheels",
        "--ro-bind",
        str(root / "files/usr"),
        "/usr",
        "--ro-bind",
        str(root / "files/lib"),
        "/lib",
        "--ro-bind",
        str(root / "files/lib64"),
        "/lib64",
        "--ro-bind",
        str(ROOT / "engines/nautilus"),
        "/engine/source",
        "--ro-bind",
        site_packages,
        "/engine/site",
        "--dir",
        "/inputs",
    ]
    for source, target in inputs:
        command.extend(("--ro-bind", str(source.resolve()), target))
    return [
        *command,
        "--tmpfs",
        "/tmp",
        "--clearenv",
        "--setenv",
        "PYTHONDONTWRITEBYTECODE",
        "1",
        "--",
        "/usr/bin/python3.12",
        "-I",
        "-S",
        "-c",
        script,
        "/engine/source",
        "/engine/site",
        *(target for _, target in inputs),
    ]


@pytest.mark.parametrize(
    "mutation", ("content", "missing", "extra", "special", "mode")
)
def test_closure_snapshot_rejects_nonwheel_drift(
    tmp_path: Path, mutation: str
) -> None:
    root = tmp_path / "closure"
    payload = root / "files/usr/lib/python312.zip"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"stdlib")
    manifest = root / "closure-manifest.json"
    manifest.write_bytes(b"{}")
    document: dict[str, object] = {
        "files": [
            {
                "mode": "0400",
                "path": "files/usr/lib/python312.zip",
                "sha256": hashlib.sha256(b"stdlib").hexdigest(),
                "size": 6,
            }
        ]
    }
    payload.chmod(0o400)
    manifest.chmod(0o400)
    for directory in (payload.parent, payload.parent.parent, payload.parent.parent.parent, root):
        directory.chmod(0o500)

    if mutation == "content":
        payload.chmod(0o600)
        payload.write_bytes(b"changed")
        payload.chmod(0o400)
    elif mutation == "missing":
        payload.parent.chmod(0o700)
        payload.unlink()
        payload.parent.chmod(0o500)
    elif mutation == "extra":
        payload.parent.chmod(0o700)
        extra = payload.parent / "ambient.py"
        extra.write_bytes(b"pass")
        extra.chmod(0o400)
        payload.parent.chmod(0o500)
    elif mutation == "special":
        payload.parent.chmod(0o700)
        os.mkfifo(payload.parent / "ambient.pipe", mode=0o400)
        payload.parent.chmod(0o500)
    else:
        payload.chmod(0o600)

    with pytest.raises((AssertionError, FileNotFoundError)):
        _validate_closure(root, manifest, document)


def test_exact_g1_native_instrument_when_host_authority_is_supplied() -> None:
    with exact_g1_runtime() as runtime:
        if runtime is None:
            # Portable source qualification cannot claim native authority.
            assert "nautilus_trader" not in subprocess.run(
                ["uv", "run", "python", "-c", "import sys;print(' '.join(sys.path))"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            return
        root, site_packages = runtime
        script = r'''
import json
from pathlib import Path
import sys
sys.path[:0] = [sys.argv[1], sys.argv[2]]
import nautilus_trader
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.common.config import LoggingConfig
from nautilus_trader.config import BacktestEngineConfig
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import Venue
from runtime_v1.instrument_factory import InstrumentFactoryError, build_instrument
assert nautilus_trader.__version__ == "1.231.0"
assert Path(nautilus_trader.__file__).resolve().is_relative_to(Path(sys.argv[2]).resolve())
values = json.loads(open(sys.argv[3], encoding="utf-8").read())
catalog = tuple(sorted(values.items()))
instrument = build_instrument(catalog)
rebuilt = build_instrument(catalog)
engine = BacktestEngine(BacktestEngineConfig(load_state=False, logging=LoggingConfig(bypass_logging=True), run_analysis=False, save_state=False))
try:
    engine.add_venue(Venue("BINANCE"), OmsType.NETTING, AccountType.CASH, [instrument.min_notional])
    engine.add_instrument(instrument)
    engine.reset()
    engine.add_instrument(rebuilt)
    registration_ids = [str(item.id) for item in engine.cache.instruments()]
finally:
    engine.dispose()
rejections = []
for label, updates in (
    ("wrong_price_precision", {"price_precision": 3}),
    ("zero_tick", {"tick_size": "0"}),
    ("negative_step", {"step_size": "-0.000001"}),
    ("unsupported_product", {"product_type": "equity"}),
    ("unknown_currency", {"base_currency": "ETH"}),
    ("over_precision", {"price_precision": 17}),
    ("outside_price_range", {"tick_size": "17014118346047"}),
    ("outside_quantity_range", {"step_size": "34028236692094"}),
    ("generated_identity", {"instrument_id": "BTCUSDT-1.BINANCE"}),
    ("unexpected_maximum", {"max_quantity": "1"}),
):
    mutated = dict(values)
    mutated.update(updates)
    try:
        build_instrument(tuple(sorted(mutated.items())))
    except InstrumentFactoryError:
        rejections.append(label)
    else:
        raise AssertionError(f"accepted {label}")
try:
    build_instrument(catalog + (("symbol", "BTCUSDT"),))
except InstrumentFactoryError:
    rejections.append("duplicate_catalog_key")
else:
    raise AssertionError("accepted duplicate catalog key")
print(json.dumps({
    "asset_class": instrument.asset_class.name,
    "base_currency": str(instrument.base_currency),
    "base_precision": instrument.base_currency.precision,
    "id": str(instrument.id),
    "instrument_class": instrument.instrument_class.name,
    "min_notional": str(instrument.min_notional.as_decimal()),
    "min_quantity": str(instrument.min_quantity),
    "price_increment": str(instrument.price_increment),
    "price_precision": instrument.price_precision,
    "quote_currency": str(instrument.quote_currency),
    "quote_precision": instrument.quote_currency.precision,
    "raw_symbol": str(instrument.raw_symbol),
    "registration_ids": registration_ids,
    "rejections": rejections,
    "settlement_currency": str(instrument.get_settlement_currency()),
    "size_increment": str(instrument.size_increment),
    "size_precision": instrument.size_precision,
    "venue": str(instrument.id.venue),
}, separators=(",", ":"), sort_keys=True))
'''
        completed = subprocess.run(
            exact_g1_command(
                root,
                site_packages,
                script,
                (CATALOG, "/inputs/instrument-catalog.json"),
            ),
            cwd="/",
            env={},
            check=False,
            capture_output=True,
            text=True,
        )

    assert completed.returncode == 0, completed.stderr
    assert completed.stderr == ""
    assert json.loads(completed.stdout) == {
        "asset_class": "CRYPTOCURRENCY",
        "base_currency": "BTC",
        "base_precision": 8,
        "id": "BTCUSDT.BINANCE",
        "instrument_class": "SPOT",
        "min_notional": "10",
        "min_quantity": "0.000001",
        "price_increment": "0.01",
        "price_precision": 2,
        "quote_currency": "USDT",
        "quote_precision": 6,
        "raw_symbol": "BTCUSDT",
        "registration_ids": ["BTCUSDT.BINANCE"],
        "rejections": [
            "wrong_price_precision",
            "zero_tick",
            "negative_step",
            "unsupported_product",
            "unknown_currency",
            "over_precision",
            "outside_price_range",
            "outside_quantity_range",
            "generated_identity",
            "unexpected_maximum",
            "duplicate_catalog_key",
        ],
        "settlement_currency": "USDT",
        "size_increment": "0.000001",
        "size_precision": 6,
        "venue": "BINANCE",
    }
