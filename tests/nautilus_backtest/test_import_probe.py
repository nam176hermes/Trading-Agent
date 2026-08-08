from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "engines/nautilus/launcher/import_probe.py"


def _module():
    if not PROBE.is_file():
        pytest.fail("sealed import probe is missing")
    specification = importlib.util.spec_from_file_location("sealed_import_probe", PROBE)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("ascii")


def _wheel(path: Path, package: str, version: str) -> str:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"{package}/__init__.py",
            f"__version__ = {version!r}\n".encode("ascii"),
        )
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def secure_tmp_path() -> Path:
    root = Path(tempfile.mkdtemp(prefix="nautilus-import-probe-", dir="/tmp"))
    root.chmod(0o700)
    try:
        yield root
    finally:
        for directory, child_directories, files in os.walk(root, topdown=False):
            current = Path(directory)
            for name in files:
                (current / name).chmod(0o600)
            for name in child_directories:
                (current / name).chmod(0o700)
            current.chmod(0o700)
        shutil.rmtree(root, ignore_errors=True)


def test_direct_entry_validator_accepts_only_the_fixed_clean_kernel_command() -> None:
    module = _module()
    command = (
        "/usr/bin/python3.12",
        "-I",
        "-S",
        "/qualification/import_probe.py",
        "--entry-launcher",
        "/qualification/entry-launcher.py",
        "--wheel-directory",
        "/engine/wheels",
    )
    facts = module._DirectEntryFacts(
        module_name="__main__",
        module_spec=None,
        module_file="/qualification/import_probe.py",
        implementation_name="cpython",
        version=(3, 12),
        isolated=1,
        no_site=1,
        ignore_environment=1,
        no_user_site=1,
        safe_path=True,
        orig_argv=command,
        argv=command[3:],
        kernel_argv=tuple(value.encode("ascii") for value in command),
    )

    module._validate_direct_entry(facts)

    with pytest.raises(module.ImportProbeError, match="direct isolated"):
        module._validate_direct_entry(
            module._DirectEntryFacts(
                **{**facts.__dict__, "kernel_argv": (*facts.kernel_argv[:-1], b"/ambient")}
            )
        )


def test_probe_rejects_the_current_nonisolated_test_process() -> None:
    module = _module()

    with pytest.raises(module.ImportProbeError, match="direct isolated"):
        module._require_direct_entry()


def test_probe_accepts_only_stdlib_search_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    stdlib = Path(module.sysconfig.get_path("stdlib")).resolve(strict=True)
    platstdlib = Path(module.sysconfig.get_path("platstdlib")).resolve(strict=True)
    allowed = list(dict.fromkeys((str(stdlib), str(platstdlib))))
    if shared := module.sysconfig.get_config_var("DESTSHARED"):
        allowed.append(str(Path(shared).resolve(strict=True)))
    monkeypatch.setattr(module.sys, "path", allowed)

    module._require_stdlib_only_path()


@pytest.mark.parametrize("mutation", ("ambient", "duplicate", "non-string"))
def test_probe_rejects_non_stdlib_search_path_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutation: str
) -> None:
    module = _module()
    stdlib = str(Path(module.sysconfig.get_path("stdlib")).resolve(strict=True))
    values: list[object] = [stdlib]
    if mutation == "ambient":
        ambient = tmp_path / "site-packages"
        ambient.mkdir()
        values.append(str(ambient))
    elif mutation == "duplicate":
        values.append(stdlib)
    else:
        values.append(7)
    monkeypatch.setattr(module.sys, "path", values)

    with pytest.raises(module.ImportProbeError, match="stdlib-only"):
        module._require_stdlib_only_path()


def test_probe_stdout_write_retries_short_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _module()
    document = {
        "modules": [],
        "schema_version": "nautilus-sealed-import-probe-v1",
        "status": "passed",
        "strategy_source_sha256": "a" * 64,
    }
    expected = _canonical(document) + b"\n"
    emitted = bytearray()

    monkeypatch.setattr(module, "_require_direct_entry", lambda: None)
    monkeypatch.setattr(module, "_require_stdlib_only_path", lambda: None)
    monkeypatch.setattr(module, "_probe_import_graph", lambda *_args: document)
    monkeypatch.setattr(
        module.sys,
        "argv",
        [
            "/qualification/import_probe.py",
            "--entry-launcher",
            "/qualification/entry-launcher.py",
            "--wheel-directory",
            "/engine/wheels",
        ],
    )

    def short_write(_descriptor: int, value: bytes) -> int:
        block = bytes(value[:3])
        emitted.extend(block)
        return len(block)

    monkeypatch.setattr(module.os, "write", short_write)

    assert module.main() == 0
    assert bytes(emitted) == expected


def test_probe_uses_the_paper_entrys_shared_sealed_import_helper_contract() -> None:
    module = _module()
    helpers = (
        lambda *_args: (),
        lambda *_args: None,
        lambda: (object, object),
        Path("/engine/launcher/strategy.py"),
    )
    launcher = SimpleNamespace(
        _sealed_import_qualification_helpers=lambda: helpers,
    )

    assert module._qualification_helpers(launcher) == helpers


def test_probe_imports_exact_sealed_graph_and_reports_no_paths(
    secure_tmp_path: Path,
) -> None:
    module = _module()
    tmp_path = secure_tmp_path
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    digests = {
        "nautilus_trader": _wheel(
            wheels / "nautilus_trader-1.227.0-py3-none-any.whl",
            "nautilus_trader",
            "1.227.0",
        ),
        "numpy": _wheel(wheels / "numpy-2.2.6-py3-none-any.whl", "numpy", "2.2.6"),
        "pandas": _wheel(
            wheels / "pandas-2.3.0-py3-none-any.whl", "pandas", "2.3.0"
        ),
    }
    for wheel in wheels.iterdir():
        wheel.chmod(0o400)
    marker = tmp_path / "calls"
    strategy = tmp_path / "target_portfolio_strategy.py"
    strategy.write_bytes(b"class TargetPortfolioStrategy:\n    pass\n")
    strategy.chmod(0o400)
    launcher = tmp_path / "entry-launcher.py"
    launcher.write_text(
        "from contextlib import contextmanager\n"
        "from pathlib import Path\n"
        "import hashlib, sys, zipfile\n"
        f"_TARGET_PORTFOLIO_STRATEGY_PATH = Path({str(strategy)!r})\n"
        "_CLEAN_ISOLATED_ENGINE_ENTRY = False\n"
        f"_MARKER = Path({str(marker)!r})\n"
        "def _mark(value):\n"
        "    with _MARKER.open('a', encoding='ascii') as stream: stream.write(value + '\\n')\n"
        "def _extract_sealed_wheels(wheels_root, extraction_root):\n"
        "    _mark('extract')\n"
        "    extraction_root.mkdir(mode=0o700)\n"
        "    roots = []\n"
        "    for wheel in sorted(wheels_root.glob('*.whl')):\n"
        "        destination = extraction_root / hashlib.sha256(wheel.name.encode('ascii')).hexdigest()\n"
        "        destination.mkdir(mode=0o700)\n"
        "        with zipfile.ZipFile(wheel) as archive: archive.extractall(destination)\n"
        "        roots.append(destination)\n"
        "    return tuple(roots)\n"
        "@contextmanager\n"
        "def _sealed_dependency_path_scope(roots):\n"
        "    _mark('scope-enter')\n"
        "    original = tuple(sys.path)\n"
        "    sys.path[:] = [*original, *(str(root) for root in roots)]\n"
        "    try: yield\n"
        "    finally:\n"
        "        sys.path[:] = original\n"
        "        _mark('scope-exit')\n"
        "def _load_target_portfolio_strategy():\n"
        "    assert _CLEAN_ISOLATED_ENGINE_ENTRY is False\n"
        "    assert all(name in sys.modules for name in ('numpy', 'pandas', 'nautilus_trader'))\n"
        "    _mark('strategy')\n"
        "    class Strategy: pass\n"
        "    class Configuration: pass\n"
        "    return Strategy, Configuration\n",
        encoding="ascii",
    )
    launcher.chmod(0o400)
    previous = {name: sys.modules.pop(name, None) for name in digests}
    try:
        document = module._probe_import_graph(launcher, wheels)
    finally:
        for name in digests:
            sys.modules.pop(name, None)
            if previous[name] is not None:
                sys.modules[name] = previous[name]

    assert marker.read_text("ascii").splitlines() == [
        "extract",
        "scope-enter",
        "strategy",
        "scope-exit",
    ]
    assert document == {
        "modules": [
            {
                "name": "nautilus_trader",
                "source_wheel_sha256": digests["nautilus_trader"],
                "version": "1.227.0",
            },
            {
                "name": "numpy",
                "source_wheel_sha256": digests["numpy"],
                "version": "2.2.6",
            },
            {
                "name": "pandas",
                "source_wheel_sha256": digests["pandas"],
                "version": "2.3.0",
            },
        ],
        "schema_version": "nautilus-sealed-import-probe-v1",
        "status": "passed",
        "strategy_source_sha256": hashlib.sha256(strategy.read_bytes()).hexdigest(),
    }
    assert b"/" not in _canonical(document)


def test_probe_rejects_a_module_outside_the_extracted_wheel_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _module()
    outside = tmp_path / "ambient.py"
    outside.write_text("__version__ = '1'\n", encoding="ascii")
    fake = type("Ambient", (), {"__file__": str(outside), "__version__": "1"})()

    with pytest.raises(module.ImportProbeError, match="sealed wheel"):
        module._module_record("numpy", fake, (), {})
