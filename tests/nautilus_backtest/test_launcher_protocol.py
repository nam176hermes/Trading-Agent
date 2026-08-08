from __future__ import annotations

import ast
import hashlib
import importlib
import importlib.machinery
import importlib.util
import json
import subprocess
import sys
import sysconfig
import tempfile
from datetime import UTC, datetime
from decimal import Context, Decimal, localcontext
from pathlib import Path
from types import ModuleType, SimpleNamespace
from uuid import UUID

import pytest

from packages.engine_contracts import (
    ArtifactReference,
    CURRENT_SCHEMA_VERSION,
    EngineCommandEnvelope,
    EngineEventEnvelope,
    RunBacktest,
    RunBacktestSimulation,
    canonical_json_bytes,
    payload_digest,
)
from packages.nautilus_backtest.fixtures import (
    SCENARIO_IDS,
    build_canonical_simulation_fixture,
    build_simulation_envelope,
)


LAUNCHER = Path("engines/nautilus/launcher/nautilus_backtest.py")


@pytest.fixture(scope="module")
def launcher_module():
    spec = importlib.util.spec_from_file_location("nautilus_backtest_launcher", LAUNCHER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sealed_strategy_tmp_path() -> Path:
    with tempfile.TemporaryDirectory(
        prefix="nautilus-strategy-loader-", dir="/tmp"
    ) as directory:
        yield Path(directory)


def _strategy_manifest_record(strategy_path: Path) -> dict[str, object]:
    value = strategy_path.read_bytes()
    return {
        "mode": "0400",
        "path": "files/engine/launcher/target_portfolio_strategy.py",
        "sha256": hashlib.sha256(value).hexdigest(),
        "size": len(value),
        "target": "/engine/launcher/target_portfolio_strategy.py",
    }


def _write_strategy_loader_fixture(
    launcher_module,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    source: bytes = (
        b"class TargetPortfolioStrategy:\n    pass\n\n"
        b"class TargetPortfolioStrategyConfig:\n    pass\n"
    ),
) -> tuple[Path, Path, dict[str, object]]:
    strategy_path = tmp_path / "target_portfolio_strategy.py"
    strategy_path.write_bytes(source)
    strategy_path.chmod(0o400)
    manifest_path = tmp_path / "closure-manifest.json"
    record = _strategy_manifest_record(strategy_path)
    manifest_path.write_bytes(
        json.dumps(
            {"files": [record]},
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        + b"\n"
    )
    manifest_path.chmod(0o400)
    monkeypatch.setattr(
        launcher_module,
        "_CLOSURE_MANIFEST_PATH",
        manifest_path,
        raising=False,
    )
    monkeypatch.setattr(
        launcher_module,
        "_TARGET_PORTFOLIO_STRATEGY_PATH",
        strategy_path,
        raising=False,
    )
    return manifest_path, strategy_path, record


def test_manifest_bound_strategy_loader_returns_exact_module_symbols(
    launcher_module,
    sealed_strategy_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _manifest_path, strategy_path, _record = _write_strategy_loader_fixture(
        launcher_module, sealed_strategy_tmp_path, monkeypatch
    )

    strategy, configuration = launcher_module._load_target_portfolio_strategy()

    assert strategy.__name__ == "TargetPortfolioStrategy"
    assert configuration.__name__ == "TargetPortfolioStrategyConfig"
    assert strategy.__module__ == configuration.__module__
    loaded_module = sys.modules[strategy.__module__]
    assert Path(loaded_module.__file__) == strategy_path
    assert strategy is loaded_module.TargetPortfolioStrategy
    assert configuration is loaded_module.TargetPortfolioStrategyConfig


def test_strategy_loader_bounds_only_the_selected_strategy_record(
    launcher_module,
    sealed_strategy_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path, _strategy_path, record = _write_strategy_loader_fixture(
        launcher_module, sealed_strategy_tmp_path, monkeypatch
    )
    manifest_path.chmod(0o600)
    manifest_path.write_bytes(
        json.dumps(
            {
                "files": [
                    {
                        "mode": "0400",
                        "path": "files/engine/wheels/nautilus.whl",
                        "sha256": "f" * 64,
                        "size": 50_000_000,
                        "target": "/engine/wheels/nautilus.whl",
                    },
                    record,
                ]
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        + b"\n"
    )
    manifest_path.chmod(0o400)

    strategy, configuration = launcher_module._load_target_portfolio_strategy()

    assert strategy.__name__ == "TargetPortfolioStrategy"
    assert configuration.__name__ == "TargetPortfolioStrategyConfig"


def test_strategy_loader_accepts_producer_canonical_non_ascii_unrelated_record(
    launcher_module,
    sealed_strategy_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path, _strategy_path, record = _write_strategy_loader_fixture(
        launcher_module, sealed_strategy_tmp_path, monkeypatch
    )
    manifest_path.chmod(0o600)
    manifest_path.write_bytes(
        json.dumps(
            {
                "files": [
                    {
                        "mode": "0400",
                        "path": "files/engine/data/caf\u00e9.json",
                        "sha256": "e" * 64,
                        "size": 2,
                        "target": "/engine/data/caf\u00e9.json",
                    },
                    record,
                ]
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        + b"\n"
    )
    manifest_path.chmod(0o400)

    strategy, configuration = launcher_module._load_target_portfolio_strategy()

    assert strategy.__name__ == "TargetPortfolioStrategy"
    assert configuration.__name__ == "TargetPortfolioStrategyConfig"


@pytest.mark.parametrize(
    "mutation",
    ("malformed-files", "missing", "malformed-record", "duplicate", "wrong-target"),
)
def test_strategy_loader_rejects_invalid_manifest_records_before_module_execution(
    launcher_module,
    sealed_strategy_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    marker = sealed_strategy_tmp_path / "executed"
    manifest_path, _strategy_path, record = _write_strategy_loader_fixture(
        launcher_module,
        sealed_strategy_tmp_path,
        monkeypatch,
        source=(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n"
            "class TargetPortfolioStrategy:\n    pass\n"
            "class TargetPortfolioStrategyConfig:\n    pass\n"
        ).encode("utf-8"),
    )
    if mutation == "malformed-files":
        document: object = {"files": {}}
    elif mutation == "missing":
        document = {"files": []}
    elif mutation == "malformed-record":
        document = {"files": [{key: value for key, value in record.items() if key != "size"}]}
    elif mutation == "duplicate":
        document = {"files": [record, dict(record)]}
    else:
        document = {"files": [{**record, "target": "/engine/launcher/not-the-strategy.py"}]}
    manifest_path.chmod(0o600)
    manifest_path.write_bytes(
        json.dumps(
            document,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        + b"\n"
    )
    manifest_path.chmod(0o400)

    with pytest.raises(ValueError):
        launcher_module._load_target_portfolio_strategy()

    assert not marker.exists()


@pytest.mark.parametrize("mutation", ("sha256", "mode", "noncanonical"))
def test_strategy_loader_rejects_unbound_manifest_before_module_execution(
    launcher_module,
    sealed_strategy_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    marker = sealed_strategy_tmp_path / "executed"
    manifest_path, _strategy_path, record = _write_strategy_loader_fixture(
        launcher_module,
        sealed_strategy_tmp_path,
        monkeypatch,
        source=(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n"
            "class TargetPortfolioStrategy:\n    pass\n"
            "class TargetPortfolioStrategyConfig:\n    pass\n"
        ).encode("utf-8"),
    )
    if mutation == "sha256":
        record["sha256"] = "0" * 64
    elif mutation == "mode":
        record["mode"] = "0500"
    document = {"files": [record]}
    separators = None if mutation == "noncanonical" else (",", ":")
    manifest_path.chmod(0o600)
    manifest_path.write_bytes(
        json.dumps(
            document,
            ensure_ascii=True,
            separators=separators,
            sort_keys=True,
        ).encode("ascii")
        + (b"" if mutation == "noncanonical" else b"\n")
    )
    manifest_path.chmod(0o400)

    with pytest.raises(ValueError):
        launcher_module._load_target_portfolio_strategy()

    assert not marker.exists()


def test_strategy_loader_rejects_symlink_without_module_execution(
    launcher_module,
    sealed_strategy_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = sealed_strategy_tmp_path / "executed"
    manifest_path, strategy_path, record = _write_strategy_loader_fixture(
        launcher_module,
        sealed_strategy_tmp_path,
        monkeypatch,
        source=(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n"
            "class TargetPortfolioStrategy:\n    pass\n"
            "class TargetPortfolioStrategyConfig:\n    pass\n"
        ).encode("utf-8"),
    )
    real_strategy_path = (
        sealed_strategy_tmp_path / "real-target-portfolio-strategy.py"
    )
    strategy_path.rename(real_strategy_path)
    strategy_path.symlink_to(real_strategy_path)
    manifest_path.chmod(0o600)
    manifest_path.write_bytes(
        json.dumps(
            {"files": [record]},
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        + b"\n"
    )
    manifest_path.chmod(0o400)

    with pytest.raises(ValueError):
        launcher_module._load_target_portfolio_strategy()

    assert not marker.exists()


def test_strategy_loader_rejects_actual_writable_mode_before_module_execution(
    launcher_module,
    sealed_strategy_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marker = sealed_strategy_tmp_path / "executed"
    _manifest_path, strategy_path, _record = _write_strategy_loader_fixture(
        launcher_module,
        sealed_strategy_tmp_path,
        monkeypatch,
        source=(
            f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n"
            "class TargetPortfolioStrategy:\n    pass\n"
            "class TargetPortfolioStrategyConfig:\n    pass\n"
        ).encode("utf-8"),
    )
    strategy_path.chmod(0o600)

    with pytest.raises(ValueError, match="identity"):
        launcher_module._load_target_portfolio_strategy()

    assert not marker.exists()


@pytest.mark.parametrize(
    "missing_symbol",
    ("TargetPortfolioStrategy", "TargetPortfolioStrategyConfig"),
)
def test_strategy_loader_rejects_missing_symbols_before_module_execution(
    launcher_module,
    sealed_strategy_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_symbol: str,
) -> None:
    marker = sealed_strategy_tmp_path / "executed"
    definitions = {
        "TargetPortfolioStrategy": "class TargetPortfolioStrategy:\n    pass\n",
        "TargetPortfolioStrategyConfig": (
            "class TargetPortfolioStrategyConfig:\n    pass\n"
        ),
    }
    source = (
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n"
        + "".join(
            definition
            for name, definition in definitions.items()
            if name != missing_symbol
        )
    ).encode("utf-8")
    _write_strategy_loader_fixture(
        launcher_module, sealed_strategy_tmp_path, monkeypatch, source=source
    )

    with pytest.raises(ValueError, match="symbols"):
        launcher_module._load_target_portfolio_strategy()

    assert not marker.exists()


def test_strategy_loader_never_falls_back_to_an_ambient_bare_import(
    launcher_module,
    sealed_strategy_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ambient = sealed_strategy_tmp_path / "ambient"
    ambient.mkdir()
    marker = sealed_strategy_tmp_path / "ambient-executed"
    (ambient / "target_portfolio_strategy.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n"
        "class TargetPortfolioStrategy:\n    pass\n"
        "class TargetPortfolioStrategyConfig:\n    pass\n",
        encoding="utf-8",
    )
    manifest_path, strategy_path, _record = _write_strategy_loader_fixture(
        launcher_module, sealed_strategy_tmp_path, monkeypatch
    )
    strategy_path.chmod(0o600)
    strategy_path.unlink()
    monkeypatch.syspath_prepend(str(ambient))

    with pytest.raises(ValueError):
        launcher_module._load_target_portfolio_strategy()

    assert manifest_path.exists()
    assert not marker.exists()


def test_sealed_wheel_scope_resolves_cross_root_dependency_with_legacy_precedence(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    (first / "nautilus_trader").mkdir(parents=True)
    (second / "nautilus_trader").mkdir(parents=True)
    (first / "nautilus_trader/__init__.py").write_text(
        "ORIGIN = 'first'\n", encoding="utf-8"
    )
    (first / "sealed_cross_root_dependency.py").write_text(
        "VALUE = 'dependency-from-first'\n", encoding="utf-8"
    )
    (second / "nautilus_trader/__init__.py").write_text(
        "import sealed_cross_root_dependency\n"
        "ORIGIN = 'second'\n"
        "DEPENDENCY = sealed_cross_root_dependency.VALUE\n",
        encoding="utf-8",
    )
    script = """
import importlib
import importlib.util
import sys
from types import ModuleType

spec = importlib.util.spec_from_file_location("isolated_launcher", sys.argv[1])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
ambient_module = ModuleType("preloaded_ambient_dependency")
sys.modules[ambient_module.__name__] = ambient_module
before_path = list(sys.path)
before_meta_path = tuple(sys.meta_path)
before_modules = dict(sys.modules)
with module._sealed_wheel_import_scope((module.Path(sys.argv[2]), module.Path(sys.argv[3]))):
    assert ambient_module.__name__ not in sys.modules
    assert sys.modules[spec.name] is module
    resolved = importlib.import_module("nautilus_trader")
    assert resolved.ORIGIN == "second"
    assert resolved.DEPENDENCY == "dependency-from-first"
    assert list(sys.path) == before_path
assert list(sys.path) == before_path
assert tuple(sys.meta_path) == before_meta_path
assert sys.modules == before_modules
assert sys.modules[ambient_module.__name__] is ambient_module
print("isolated-cross-root-import-ok")
"""
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            script,
            str(LAUNCHER.resolve()),
            str(first),
            str(second),
        ],
        check=False,
        capture_output=True,
        env={},
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "isolated-cross-root-import-ok\n"


def _isolated_stdlib_search_path() -> list[str]:
    stdlib = Path(sysconfig.get_path("stdlib")).resolve()
    values = (
        stdlib.parent / f"python{sys.version_info.major}{sys.version_info.minor}.zip",
        stdlib,
        Path(sysconfig.get_path("platstdlib")).resolve(),
        Path(sysconfig.get_config_var("DESTSHARED")).resolve(),
    )
    return list(dict.fromkeys(str(value) for value in values))


def test_sealed_wheel_scope_delegates_unowned_import_to_registered_finder(
    launcher_module,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sealed = tmp_path / "sealed"
    sealed.mkdir()
    (sealed / "sealed_importer_owner.py").write_text(
        "import importlib.util\n"
        "import sys\n"
        "\n"
        "class _VirtualLoader:\n"
        "    def create_module(self, spec):\n"
        "        return None\n"
        "\n"
        "    def exec_module(self, module):\n"
        "        module.VALUE = 'registered-finder'\n"
        "\n"
        "class _VirtualFinder:\n"
        "    def find_spec(self, fullname, path=None, target=None):\n"
        "        if fullname == 'sealed_virtual_dependency':\n"
        "            return importlib.util.spec_from_loader(fullname, _VirtualLoader())\n"
        "        return None\n"
        "\n"
        "sys.meta_path.append(_VirtualFinder())\n"
        "import sealed_virtual_dependency\n"
        "VALUE = sealed_virtual_dependency.VALUE\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(sys, "path", _isolated_stdlib_search_path())
    monkeypatch.setattr(
        launcher_module, "_CLEAN_ISOLATED_ENGINE_ENTRY", True
    )
    before_path = list(sys.path)
    before_meta_path = tuple(sys.meta_path)
    before_modules = dict(sys.modules)

    with launcher_module._sealed_wheel_import_scope((sealed,)):
        resolved = importlib.import_module("sealed_importer_owner")
        assert resolved.VALUE == "registered-finder"
        assert list(sys.path) == before_path

    assert list(sys.path) == before_path
    assert tuple(sys.meta_path) == before_meta_path
    assert sys.modules == before_modules


def test_terminal_sealed_import_failure_preserves_requested_module_name(
    launcher_module,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sealed = tmp_path / "sealed"
    sealed.mkdir()
    (sealed / "sealed_terminal_owner.py").write_text(
        "import sealed_terminal_missing_dependency\n", encoding="utf-8"
    )
    monkeypatch.setattr(sys, "path", _isolated_stdlib_search_path())
    monkeypatch.setattr(
        launcher_module, "_CLEAN_ISOLATED_ENGINE_ENTRY", True
    )
    before_path = list(sys.path)
    before_meta_path = tuple(sys.meta_path)
    before_modules = dict(sys.modules)

    with pytest.raises(ModuleNotFoundError) as caught:
        with launcher_module._sealed_wheel_import_scope((sealed,)):
            importlib.import_module("sealed_terminal_owner")

    assert caught.value.name == "sealed_terminal_missing_dependency"
    assert list(sys.path) == before_path
    assert tuple(sys.meta_path) == before_meta_path
    assert sys.modules == before_modules


def test_launcher_source_requires_direct_isolated_no_site_execution() -> None:
    required_error = (
        "error: Nautilus engine entry requires direct CPython -I -S execution\n"
    )
    for flags in ((), ("-I",), ("-S",), ("-S", "-I")):
        result = subprocess.run(
            [sys.executable, *flags, str(LAUNCHER.resolve())],
            check=False,
            capture_output=True,
            env={},
            text=True,
            timeout=10,
        )

        assert result.returncode == 1
        assert result.stdout == ""
        assert result.stderr == required_error

    isolated = subprocess.run(
        [sys.executable, "-I", "-S", str(LAUNCHER.resolve())],
        check=False,
        capture_output=True,
        env={},
        text=True,
        timeout=10,
    )

    assert isolated.returncode == 1
    assert isolated.stdout == ""
    assert isolated.stderr == (
        "error: expected the attested launcher profile and request inputs\n"
    )


def test_launcher_source_rejects_forged_preload_before_startup_snapshot() -> None:
    script = r"""
import importlib.machinery
import importlib.util
import sys
import sysconfig
from pathlib import Path
from types import ModuleType

real_sys = sys
stdlib = Path(sysconfig.get_path("stdlib"))

def forged_source_module(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    forged = ModuleType(name)
    forged.__file__ = str(path)
    forged.__loader__ = loader
    forged.__package__ = name.rpartition(".")[0]
    forged.__spec__ = spec
    forged.AMBIENT_IMPERSONATOR = True
    return forged

forged_json = forged_source_module("json", stdlib / "json" / "__init__.py")
forged_child = forged_source_module(
    "xml.ambient_only_dependency", stdlib / "json" / "decoder.py"
)
forged_sys = ModuleType("sys")
forged_sys.__dict__.update(real_sys.__dict__)
forged_sys.AMBIENT_IMPERSONATOR = True

real_sys.modules["json"] = forged_json
real_sys.modules["sys"] = forged_sys
real_sys.modules["xml.ambient_only_dependency"] = forged_child

launcher_path = str(Path(real_sys.argv[1]).resolve())
claimed_argv = [launcher_path]
claimed_orig_argv = [real_sys.executable, "-I", "-S", launcher_path]
real_sys.argv = claimed_argv
real_sys.orig_argv = claimed_orig_argv
forged_sys.argv = claimed_argv
forged_sys.orig_argv = claimed_orig_argv

launcher = ModuleType("__main__")
launcher.__file__ = launcher_path
launcher.__package__ = None
launcher.__spec__ = None
real_sys.modules["__main__"] = launcher
source = Path(launcher_path).read_bytes()
exec(compile(source, launcher_path, "exec"), launcher.__dict__)
"""
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            script,
            str(LAUNCHER.resolve()),
        ],
        check=False,
        capture_output=True,
        env={},
        text=True,
        timeout=10,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == (
        "error: Nautilus engine entry requires direct CPython -I -S execution\n"
    )


def test_sealed_wheel_scope_refuses_ambient_fallback_and_restores_after_error(
    launcher_module,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sealed = tmp_path / "sealed"
    ambient = tmp_path / "ambient"
    (sealed / "nautilus_trader").mkdir(parents=True)
    ambient.mkdir()
    (sealed / "nautilus_trader/__init__.py").write_text(
        "import ambient_only_dependency\n", encoding="utf-8"
    )
    (ambient / "ambient_only_dependency.py").write_text(
        "VALUE = 'must-not-load'\n", encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(ambient))
    assert "ambient_only_dependency" not in sys.modules
    ambient_module = importlib.import_module("ambient_only_dependency")
    sys.modules.pop("ambient_only_dependency")
    monkeypatch.setitem(
        sys.modules, "ambient_only_dependency", ambient_module
    )
    monkeypatch.setattr(
        launcher_module, "_CLEAN_ISOLATED_ENGINE_ENTRY", True
    )
    before_path = list(sys.path)
    before_meta_path = tuple(sys.meta_path)
    before_modules = dict(sys.modules)

    with pytest.raises(ValueError, match="standard-library roots"):
        with launcher_module._sealed_wheel_import_scope((sealed,)):
            importlib.import_module("nautilus_trader")

    assert list(sys.path) == before_path
    assert tuple(sys.meta_path) == before_meta_path
    assert sys.modules == before_modules
    assert sys.modules["ambient_only_dependency"] is ambient_module
    assert "nautilus_trader" not in sys.modules


def test_sealed_wheel_scope_refuses_stdlib_key_impersonation_and_restores_after_success(
    launcher_module,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sealed = tmp_path / "sealed"
    (sealed / "nautilus_trader").mkdir(parents=True)
    (sealed / "nautilus_trader/__init__.py").write_text(
        "import json\n"
        "import sys\n"
        "USED_AMBIENT = getattr(json, 'AMBIENT_IMPERSONATOR', False)\n"
        "SERIALIZED = json.dumps(\n"
        "    {'trusted': True}, sort_keys=True, separators=(',', ':')\n"
        ")\n"
        "BUILTIN_SYS_OK = sys.version_info.major >= 3\n",
        encoding="utf-8",
    )
    real_json = json
    impersonator = ModuleType("json")
    impersonator.AMBIENT_IMPERSONATOR = True
    impersonator.dumps = lambda *_args, **_kwargs: "ambient"
    for attribute in ("__file__", "__loader__", "__package__", "__spec__"):
        setattr(impersonator, attribute, getattr(real_json, attribute))
    real_sys = sys
    builtin_impersonator = ModuleType("sys")
    builtin_impersonator.__loader__ = real_sys.__loader__
    builtin_impersonator.__spec__ = real_sys.__spec__
    monkeypatch.delitem(sys.modules, "nautilus_trader", raising=False)
    monkeypatch.setitem(sys.modules, "json", impersonator)
    monkeypatch.setitem(sys.modules, "sys", builtin_impersonator)
    before_path = list(sys.path)
    before_meta_path = tuple(sys.meta_path)
    before_modules = dict(sys.modules)

    with launcher_module._sealed_wheel_import_scope((sealed,)):
        resolved = importlib.import_module("nautilus_trader")
        assert resolved.USED_AMBIENT is False
        assert resolved.SERIALIZED == '{"trusted":true}'
        assert resolved.BUILTIN_SYS_OK is True
        assert sys.modules["json"] is real_json
        assert sys.modules["sys"] is real_sys
        assert list(sys.path) == before_path

    assert list(sys.path) == before_path
    assert tuple(sys.meta_path) == before_meta_path
    assert sys.modules == before_modules
    assert sys.modules["json"] is impersonator
    assert sys.modules["sys"] is builtin_impersonator
    assert "nautilus_trader" not in sys.modules


@pytest.mark.parametrize("mapping_state", ("spoofed", "missing"))
def test_sealed_wheel_scope_refuses_stdlib_child_key_impersonation_and_restores_after_error(
    launcher_module,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mapping_state: str,
) -> None:
    sealed = tmp_path / "sealed"
    (sealed / "nautilus_trader").mkdir(parents=True)
    (sealed / "nautilus_trader/__init__.py").write_text(
        "from xml import ambient_only_dependency\n", encoding="utf-8"
    )
    genuine_stdlib_child = importlib.import_module("json.decoder")
    child_name = "xml.ambient_only_dependency"
    assert genuine_stdlib_child.__file__ is not None
    loader = importlib.machinery.SourceFileLoader(
        child_name, genuine_stdlib_child.__file__
    )
    impersonator = ModuleType(child_name)
    impersonator.VALUE = "must-not-load"
    impersonator.__file__ = genuine_stdlib_child.__file__
    impersonator.__loader__ = loader
    impersonator.__package__ = "xml"
    impersonator.__spec__ = importlib.util.spec_from_loader(child_name, loader)
    xml = importlib.import_module("xml")
    monkeypatch.delitem(sys.modules, "nautilus_trader", raising=False)
    if mapping_state == "spoofed":
        monkeypatch.setitem(sys.modules, child_name, impersonator)
    else:
        impersonator.__name__ = "different.claim"
        monkeypatch.delitem(sys.modules, child_name, raising=False)
    monkeypatch.setattr(xml, "ambient_only_dependency", impersonator, raising=False)
    before_path = list(sys.path)
    before_meta_path = tuple(sys.meta_path)
    before_modules = dict(sys.modules)

    with pytest.raises(ImportError, match="ambient_only_dependency"):
        with launcher_module._sealed_wheel_import_scope((sealed,)):
            importlib.import_module("nautilus_trader")

    assert list(sys.path) == before_path
    assert tuple(sys.meta_path) == before_meta_path
    assert sys.modules == before_modules
    if mapping_state == "spoofed":
        assert sys.modules[child_name] is impersonator
    else:
        assert child_name not in sys.modules
    assert xml.ambient_only_dependency is impersonator
    assert "nautilus_trader" not in sys.modules


@pytest.mark.parametrize(
    "child_name",
    (
        "json.decoder",
        "os.path",
        "importlib._bootstrap",
        "importlib._bootstrap_external",
        "typing.io",
        "typing.re",
    ),
)
def test_sealed_wheel_scope_synchronizes_trusted_child_parent_attributes(
    launcher_module,
    child_name: str,
) -> None:
    parent_name, _, attribute = child_name.rpartition(".")
    parent = importlib.import_module(parent_name)
    trusted_child = launcher_module._TRUSTED_PRELOADED_INTERPRETER_MODULES[
        child_name
    ]
    original_child = sys.modules[child_name]
    original_parent_attribute = vars(parent)[attribute]
    impersonator = ModuleType(child_name)
    impersonator.AMBIENT_IMPERSONATOR = True
    try:
        sys.modules[child_name] = impersonator
        vars(parent)[attribute] = impersonator
        before_modules = dict(sys.modules)

        with launcher_module._sealed_wheel_import_scope(()):
            imported_child = importlib.import_module(child_name)
            namespace: dict[str, object] = {}
            exec(
                f"from {parent_name} import {attribute} as imported_from_parent",
                namespace,
            )
            assert imported_child is trusted_child
            assert namespace["imported_from_parent"] is trusted_child
            assert vars(parent)[attribute] is trusted_child

        assert sys.modules == before_modules
        assert sys.modules[child_name] is impersonator
        assert vars(parent)[attribute] is impersonator
    finally:
        sys.modules[child_name] = original_child
        vars(parent)[attribute] = original_parent_attribute


@pytest.mark.parametrize("parent_state", ("missing", "spoofed"))
def test_sealed_wheel_scope_reseeds_missing_child_mapping_and_parent_attribute(
    launcher_module,
    parent_state: str,
) -> None:
    child_name = "json.decoder"
    parent = json
    trusted_child = launcher_module._TRUSTED_PRELOADED_INTERPRETER_MODULES[
        child_name
    ]
    original_child = sys.modules[child_name]
    original_parent_attribute = vars(parent)["decoder"]
    impersonator = ModuleType(child_name)
    try:
        sys.modules.pop(child_name)
        if parent_state == "missing":
            vars(parent).pop("decoder")
        else:
            vars(parent)["decoder"] = impersonator
        before_modules = dict(sys.modules)

        with launcher_module._sealed_wheel_import_scope(()):
            namespace: dict[str, object] = {}
            exec("from json import decoder as imported_from_parent", namespace)
            exec("import json.decoder", namespace)
            assert namespace["imported_from_parent"] is trusted_child
            assert json.decoder is trusted_child
            assert sys.modules[child_name] is trusted_child

        assert sys.modules == before_modules
        assert child_name not in sys.modules
        if parent_state == "missing":
            assert "decoder" not in vars(parent)
        else:
            assert parent.decoder is impersonator
    finally:
        sys.modules[child_name] = original_child
        vars(parent)["decoder"] = original_parent_attribute


def test_sealed_wheel_scope_restores_repaired_child_parent_after_import_error(
    launcher_module,
    tmp_path: Path,
) -> None:
    sealed = tmp_path / "sealed"
    (sealed / "nautilus_trader").mkdir(parents=True)
    (sealed / "nautilus_trader/__init__.py").write_text(
        "from json import decoder\n"
        "assert not getattr(decoder, 'AMBIENT_IMPERSONATOR', False)\n"
        "import ambient_only_dependency\n",
        encoding="utf-8",
    )
    child_name = "json.decoder"
    original_child = sys.modules[child_name]
    original_parent_attribute = vars(json)["decoder"]
    impersonator = ModuleType(child_name)
    impersonator.AMBIENT_IMPERSONATOR = True
    missing = object()
    original_nautilus = sys.modules.pop("nautilus_trader", missing)
    try:
        sys.modules[child_name] = impersonator
        vars(json)["decoder"] = impersonator
        before_modules = dict(sys.modules)

        with pytest.raises(ModuleNotFoundError) as caught:
            with launcher_module._sealed_wheel_import_scope((sealed,)):
                importlib.import_module("nautilus_trader")

        assert caught.value.name == "ambient_only_dependency"
        assert sys.modules == before_modules
        assert sys.modules[child_name] is impersonator
        assert json.decoder is impersonator
        assert "nautilus_trader" not in sys.modules
    finally:
        sys.modules[child_name] = original_child
        vars(json)["decoder"] = original_parent_attribute
        if original_nautilus is not missing:
            sys.modules["nautilus_trader"] = original_nautilus


def test_sealed_wheel_scope_removes_new_stdlib_child_parent_attribute(
    launcher_module,
) -> None:
    child_name = "json.tool"
    missing = object()
    original_child = sys.modules.pop(child_name, missing)
    original_parent_attribute = vars(json).pop("tool", missing)
    try:
        before_modules = dict(sys.modules)

        with launcher_module._sealed_wheel_import_scope(()):
            imported_child = importlib.import_module(child_name)
            assert sys.modules[child_name] is imported_child
            assert json.tool is imported_child

        assert sys.modules == before_modules
        assert child_name not in sys.modules
        assert "tool" not in vars(json)
    finally:
        sys.modules.pop(child_name, None)
        vars(json).pop("tool", None)
        if original_child is not missing:
            sys.modules[child_name] = original_child
        if original_parent_attribute is not missing:
            vars(json)["tool"] = original_parent_attribute


def test_sealed_wheel_scope_restores_globals_after_nameless_child_error(
    launcher_module,
) -> None:
    attribute = "ambient_nameless_dependency"
    child = ModuleType(f"json.{attribute}")
    vars(child).pop("__name__")
    missing = object()
    original_attribute = vars(json).pop(attribute, missing)
    before_meta_path = tuple(sys.meta_path)
    before_modules = dict(sys.modules)
    try:
        with pytest.raises(RuntimeError, match="sealed failure"):
            with launcher_module._sealed_wheel_import_scope(()):
                vars(json)[attribute] = child
                raise RuntimeError("sealed failure")

        assert tuple(sys.meta_path) == before_meta_path
        assert sys.modules == before_modules
        assert attribute not in vars(json)
    finally:
        sys.meta_path[:] = before_meta_path
        for name in tuple(sys.modules):
            if name not in before_modules:
                sys.modules.pop(name, None)
        sys.modules.update(before_modules)
        vars(json).pop(attribute, None)
        if original_attribute is not missing:
            vars(json)[attribute] = original_attribute


def test_sealed_wheel_scope_removes_negative_cache_and_restores_after_success(
    launcher_module,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sealed = tmp_path / "sealed"
    sealed.mkdir()
    (sealed / "sealed_negative_cache_dependency.py").write_text(
        "VALUE = 'sealed'\n", encoding="utf-8"
    )
    module_name = "sealed_negative_cache_dependency"
    monkeypatch.setitem(sys.modules, module_name, None)
    before_modules = dict(sys.modules)

    with launcher_module._sealed_wheel_import_scope((sealed,)):
        resolved = importlib.import_module(module_name)
        assert resolved.VALUE == "sealed"

    assert sys.modules == before_modules
    assert sys.modules[module_name] is None


def test_sealed_wheel_scope_does_not_inspect_launcher_key_impersonator(
    launcher_module,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class LauncherKeyImpersonator:
        def __getattribute__(self, _name: str) -> object:
            raise AssertionError("ambient launcher impersonator was inspected")

    module_name = launcher_module.__name__
    impersonator = LauncherKeyImpersonator()
    monkeypatch.setitem(sys.modules, module_name, impersonator)
    before_modules = dict(sys.modules)

    with launcher_module._sealed_wheel_import_scope(()):
        assert module_name not in sys.modules

    assert sys.modules == before_modules
    assert sys.modules[module_name] is impersonator


def test_sealed_wheel_scope_preserves_cpython_interpreter_machinery(
    launcher_module,
) -> None:
    names = (
        "os.path",
        "importlib._bootstrap",
        "importlib._bootstrap_external",
        "typing.io",
        "typing.re",
        *(
            name
            for name in sys.modules
            if name.startswith("_sysconfigdata_")
        ),
    )
    assert any(name.startswith("_sysconfigdata_") for name in names)
    machinery = {name: sys.modules[name] for name in names}
    before_modules = dict(sys.modules)

    with launcher_module._sealed_wheel_import_scope(()):
        assert all(sys.modules[name] is module for name, module in machinery.items())

    assert sys.modules == before_modules


def test_sealed_wheel_scope_reseeds_missing_interpreter_alias_and_restores(
    launcher_module,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alias_name = "os.path"
    trusted_alias = sys.modules[alias_name]
    monkeypatch.delitem(sys.modules, alias_name)
    before_modules = dict(sys.modules)

    with launcher_module._sealed_wheel_import_scope(()):
        resolved = importlib.import_module(alias_name)
        assert resolved is trusted_alias

    assert sys.modules == before_modules
    assert alias_name not in sys.modules


def test_launcher_has_no_global_sys_path_mutation_or_bare_strategy_import() -> None:
    syntax = ast.parse(LAUNCHER.read_text(encoding="utf-8"))
    mutations: list[ast.AST] = []
    for node in ast.walk(syntax):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            if (
                isinstance(owner, ast.Attribute)
                and isinstance(owner.value, ast.Name)
                and owner.value.id == "sys"
                and owner.attr == "path"
            ):
                mutations.append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                base = target.value if isinstance(target, ast.Subscript) else target
                if (
                    isinstance(base, ast.Attribute)
                    and isinstance(base.value, ast.Name)
                    and base.value.id == "sys"
                    and base.attr == "path"
                ):
                    mutations.append(node)

    assert mutations == []
    assert "from target_portfolio_strategy import" not in LAUNCHER.read_text(
        encoding="utf-8"
    )


class _EngineAmount:
    def __init__(self, value: str) -> None:
        self._value = Decimal(value)

    def as_decimal(self) -> Decimal:
        return self._value


def _request() -> EngineCommandEnvelope:
    configuration = ArtifactReference(
        artifact_id=UUID("11111111-1111-4111-8111-111111111111"),
        sha256="1" * 64,
        media_type="application/json",
    )
    command = RunBacktest(
        command_type="RunBacktest",
        engine_configuration=configuration,
        instrument_catalog=ArtifactReference(
            artifact_id=UUID("22222222-2222-4222-8222-222222222222"),
            sha256="2" * 64,
            media_type="application/json",
        ),
        strategy_configuration=ArtifactReference(
            artifact_id=UUID("33333333-3333-4333-8333-333333333333"),
            sha256="3" * 64,
            media_type="application/json",
        ),
        market_data=ArtifactReference(
            artifact_id=UUID("44444444-4444-4444-8444-444444444444"),
            sha256="4" * 64,
            media_type="application/jsonl",
        ),
        start_time=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
        end_time=datetime(2026, 8, 5, 12, 30, tzinfo=UTC),
    )
    return EngineCommandEnvelope(
        message_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        correlation_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        causation_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        engine_run_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
        stream_sequence=1,
        event_time=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
        initialization_time=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
        schema_version=CURRENT_SCHEMA_VERSION,
        producer_identity="worker-authority-1",
        source_commit="0123456789abcdef0123456789abcdef01234567",
        config_digest=payload_digest(
            {
                "engine_configuration": command.engine_configuration,
                "instrument_catalog": command.instrument_catalog,
                "strategy_configuration": command.strategy_configuration,
            }
        ),
        payload_digest=payload_digest(command),
        payload=command,
    )


_SCENARIO_IDS = (
    "long-accounting",
    "short-accounting",
    "partial-fill",
    "same-bar-stop-take-profit",
    "stale-quote",
    "zero-liquidity",
    "session-boundary",
    "event-digest",
)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def _golden_simulation_fixture(
    scenario_id: str,
) -> tuple[bytes, bytes, bytes, bytes, bytes]:
    assert scenario_id in _SCENARIO_IDS
    target_quantity = "-2" if scenario_id == "short-accounting" else "1"
    if scenario_id == "long-accounting":
        target_quantity = "2"
    elif scenario_id == "partial-fill":
        target_quantity = "3"
    configuration = _canonical(
        {
            "execution_mode": "execution-simulation",
            "run_analysis": False,
            "schema_version": "nautilus-backtest-engine-config-v1",
        }
    )
    strategy = _canonical(
        {
            "effective_at": "2026-08-05T12:00:00Z",
            "positions": [
                {
                    "instrument": {
                        "product_type": "crypto_spot",
                        "symbol": "BTCUSDT",
                        "venue": "BINANCE",
                    },
                    "target_quantity": target_quantity,
                }
            ],
            "schema_version": "nautilus-execution-target-v1",
        }
    )
    events = [
        {
            "ask": "100",
            "bid": "99",
            "close": "101",
            "event_time": "2026-08-05T12:00:00Z",
            "high": "102",
            "low": "98",
            "open": "100",
            "quote_time": "2026-08-05T12:00:00Z",
            "sequence": 1,
            "session_open": True,
            "volume": "2",
        }
    ]
    liquidity_limit = "10"
    stop_price: str | None = None
    take_profit_price: str | None = None
    if scenario_id == "partial-fill":
        events[0]["volume"] = "1"
        liquidity_limit = "1"
    elif scenario_id == "same-bar-stop-take-profit":
        events[0]["high"] = "103"
        events[0]["low"] = "97"
        stop_price = "98"
        take_profit_price = "102"
    elif scenario_id == "stale-quote":
        events[0]["quote_time"] = "2026-08-05T11:58:00Z"
    elif scenario_id == "zero-liquidity":
        liquidity_limit = "0"
    elif scenario_id == "session-boundary":
        events[0]["session_open"] = False
        events.append(
            {
                "ask": "102",
                "bid": "101",
                "close": "102",
                "event_time": "2026-08-05T12:01:00Z",
                "high": "103",
                "low": "100",
                "open": "101",
                "quote_time": "2026-08-05T12:01:00Z",
                "sequence": 2,
                "session_open": True,
                "volume": "2",
            }
        )
    market_rows = [
        {
            "close": event["close"],
            "high": event["high"],
            "low": event["low"],
            "open": event["open"],
            "open_time": event["event_time"],
            "volume": event["volume"],
        }
        for event in events
    ]
    market = b"".join(_canonical(row) + b"\n" for row in market_rows)
    catalog = _canonical(
        {
            "canonical_rows_sha256": hashlib.sha256(_canonical(market_rows)).hexdigest(),
            "content_digest": "a" * 64,
            "continuity": {
                "duplicate_report": [],
                "gap_report": [],
                "timeframe": "1m",
            },
            "fetched_at": "2026-08-05T12:02:00Z",
            "first_event_at": events[0]["event_time"],
            "importer_version": "fixture-catalog-v1",
            "instrument": {
                "product_type": "crypto_spot",
                "symbol": "BTCUSDT",
                "venue": "BINANCE",
            },
            "known_at": "2026-08-05T12:02:00Z",
            "last_event_at": events[-1]["event_time"],
            "normalization_version": "market-normalization-v1",
            "observed_at": "2026-08-05T12:02:00Z",
            "parquet_sha256": "b" * 64,
            "provider": "deterministic-fixture-v1",
            "provenance_schema_version": "market-data-v1",
            "raw_evidence_sha256": "c" * 64,
            "row_count": len(events),
            "schema_version": "market-dataset-manifest-v1",
            "snapshot_schema_version": "market-snapshot-v1",
            "timeframe": "1m",
        }
    )
    scenario = _canonical(
        {
            "catalog_sha256": hashlib.sha256(catalog).hexdigest(),
            "events": events,
            "fee_rate": "0.001",
            "instrument": {
                "product_type": "crypto_spot",
                "symbol": "BTCUSDT",
                "venue": "BINANCE",
            },
            "liquidity_limit": liquidity_limit,
            "market_data_sha256": hashlib.sha256(market).hexdigest(),
            "scenario_id": scenario_id,
            "schema_version": "nautilus-execution-scenario-v1",
            "session_policy": "explicit-open-flag-v1",
            "slippage_bps": "0",
            "stale_quote_threshold_seconds": 30,
            "stop_price": stop_price,
            "stop_take_profit_precedence": "stop-first",
            "strategy_sha256": hashlib.sha256(strategy).hexdigest(),
            "take_profit_price": take_profit_price,
        }
    )
    return configuration, catalog, strategy, market, scenario


def _simulation_fixture(scenario_id: str) -> tuple[bytes, bytes, bytes, bytes, bytes]:
    return build_canonical_simulation_fixture(scenario_id).artifacts


def _simulation_request(
    artifacts: tuple[bytes, bytes, bytes, bytes, bytes],
) -> EngineCommandEnvelope:
    references = tuple(
        ArtifactReference(
            artifact_id=UUID(
                f"{index}{index}{index}{index}{index}{index}{index}{index}-1111-4111-8111-111111111111"
            ),
            sha256=hashlib.sha256(value).hexdigest(),
            media_type="application/jsonl" if index == 4 else "application/json",
        )
        for index, value in enumerate(artifacts, start=1)
    )
    command = RunBacktestSimulation(
        command_type="RunBacktestSimulation",
        engine_configuration=references[0],
        instrument_catalog=references[1],
        strategy_configuration=references[2],
        market_data=references[3],
        simulation_scenario=references[4],
        start_time=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
        end_time=datetime(2026, 8, 5, 12, 30, tzinfo=UTC),
    )
    return _request().model_copy(
        update={
            "config_digest": payload_digest(
                {
                    "engine_configuration": command.engine_configuration,
                    "instrument_catalog": command.instrument_catalog,
                    "strategy_configuration": command.strategy_configuration,
                }
            ),
            "payload_digest": payload_digest(command),
            "payload": command,
        }
    )


@pytest.mark.parametrize("scenario_id", _SCENARIO_IDS)
def test_package_fixture_api_preserves_every_canonical_byte_and_envelope_field(
    scenario_id: str,
) -> None:
    """Changing package-owned fixtures must not drift existing sealed goldens."""
    expected_artifacts = _golden_simulation_fixture(scenario_id)
    expected_envelope = _simulation_request(expected_artifacts)

    fixture = build_canonical_simulation_fixture(scenario_id)
    envelope = build_simulation_envelope(fixture)

    assert SCENARIO_IDS == _SCENARIO_IDS
    assert fixture.scenario_id == scenario_id
    assert fixture.artifacts == expected_artifacts
    assert envelope.model_dump(mode="json") == expected_envelope.model_dump(mode="json")


def _with_simulation_target(
    artifacts: tuple[bytes, bytes, bytes, bytes, bytes], target: str
) -> tuple[bytes, bytes, bytes, bytes, bytes]:
    changed = list(artifacts)
    strategy = json.loads(changed[2])
    strategy["positions"][0]["target_quantity"] = target
    changed[2] = _canonical(strategy)
    scenario = json.loads(changed[4])
    scenario["strategy_sha256"] = hashlib.sha256(changed[2]).hexdigest()
    changed[4] = _canonical(scenario)
    return tuple(changed)


def _with_complete_simulation_bound(
    artifacts: tuple[bytes, bytes, bytes, bytes, bytes],
    *,
    price: str | None = None,
    quantity: str | None = None,
) -> tuple[bytes, bytes, bytes, bytes, bytes]:
    """Rebind every affected canonical artifact after an audit-bound mutation."""

    assert (price is None) != (quantity is None)
    changed = list(artifacts)
    strategy = json.loads(changed[2])
    scenario = json.loads(changed[4])
    if price is not None:
        for field in ("ask", "bid", "close", "high", "low", "open"):
            scenario["events"][0][field] = price
    else:
        assert quantity is not None
        strategy["positions"][0]["target_quantity"] = quantity
        scenario["events"][0]["volume"] = quantity
        scenario["liquidity_limit"] = quantity
        changed[2] = _canonical(strategy)

    market_rows = [
        {
            "close": event["close"],
            "high": event["high"],
            "low": event["low"],
            "open": event["open"],
            "open_time": event["event_time"],
            "volume": event["volume"],
        }
        for event in scenario["events"]
    ]
    changed[3] = b"".join(_canonical(row) + b"\n" for row in market_rows)
    catalog = json.loads(changed[1])
    catalog["canonical_rows_sha256"] = hashlib.sha256(
        _canonical(market_rows)
    ).hexdigest()
    changed[1] = _canonical(catalog)
    scenario["catalog_sha256"] = hashlib.sha256(changed[1]).hexdigest()
    scenario["market_data_sha256"] = hashlib.sha256(changed[3]).hexdigest()
    scenario["strategy_sha256"] = hashlib.sha256(changed[2]).hexdigest()
    changed[4] = _canonical(scenario)
    return tuple(changed)


def _assert_complete_envelope_rejects_before_nautilus(
    launcher_module,
    monkeypatch: pytest.MonkeyPatch,
    artifacts: tuple[bytes, bytes, bytes, bytes, bytes],
) -> None:
    del artifacts
    entered_nautilus = False

    def no_nautilus_run(_fixture: dict[str, object]) -> dict[str, object]:
        nonlocal entered_nautilus
        entered_nautilus = True
        raise AssertionError("invalid simulation reached Nautilus")

    def no_request_validation(*_args, **_kwargs):
        raise AssertionError("imported host context reached request validation")

    def no_artifact_validation(*_args, **_kwargs):
        raise AssertionError("imported host context reached artifact validation")

    monkeypatch.setattr(launcher_module, "validated_request", no_request_validation)
    monkeypatch.setattr(
        launcher_module,
        "validated_input_artifacts",
        no_artifact_validation,
    )
    monkeypatch.setattr(
        launcher_module, "_run_nautilus_simulation_fixture", no_nautilus_run
    )

    with pytest.raises(SystemExit) as error:
        launcher_module.main(
            ["--profile", "execution-simulation", "request.json", "request.sha256"]
        )

    assert error.value.code == (
        "error: Nautilus engine entry requires direct CPython -I -S execution"
    )
    assert entered_nautilus is False


def test_launcher_accepts_only_hash_bound_canonical_run_backtest(
    launcher_module, tmp_path: Path
) -> None:
    request = canonical_json_bytes(_request())
    request_path = tmp_path / "request.json"
    sidecar_path = tmp_path / "request.sha256"
    request_path.write_bytes(request)
    sidecar_path.write_text(hashlib.sha256(request).hexdigest() + "\n", encoding="ascii")

    accepted = launcher_module.validated_request(request_path, sidecar_path)

    assert accepted["payload"]["command_type"] == "RunBacktest"


def test_launcher_rejects_request_digest_drift(launcher_module, tmp_path: Path) -> None:
    request = canonical_json_bytes(_request())
    request_path = tmp_path / "request.json"
    sidecar_path = tmp_path / "request.sha256"
    request_path.write_bytes(request)
    sidecar_path.write_text("0" * 64 + "\n", encoding="ascii")

    with pytest.raises(ValueError, match="digest"):
        launcher_module.validated_request(request_path, sidecar_path)


def test_launcher_reads_only_the_four_hash_bound_input_artifacts(
    launcher_module, tmp_path: Path
) -> None:
    artifact_values = (
        ("engine_configuration", b'{"mode":"zero-order"}\n', "application/json"),
        ("instrument_catalog", b'{"schema_version":"market-dataset-manifest-v1"}\n', "application/json"),
        ("strategy_configuration", b'{"positions":[]}\n', "application/json"),
        ("market_data", b'{"close":"1"}\n', "application/jsonl"),
    )
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    references: list[ArtifactReference] = []
    for index, (name, value, media_type) in enumerate(artifact_values, start=1):
        digest = hashlib.sha256(value).hexdigest()
        extension = ".jsonl" if media_type == "application/jsonl" else ".json"
        (artifact_root / f"{name}-{digest}{extension}").write_bytes(value)
        references.append(
            ArtifactReference(
                artifact_id=UUID(f"{index}{index}{index}{index}{index}{index}{index}{index}-1111-4111-8111-111111111111"),
                sha256=digest,
                media_type=media_type,
            )
        )
    command = RunBacktest(
        command_type="RunBacktest",
        engine_configuration=references[0],
        instrument_catalog=references[1],
        strategy_configuration=references[2],
        market_data=references[3],
        start_time=datetime(2026, 8, 5, 12, 0, tzinfo=UTC),
        end_time=datetime(2026, 8, 5, 12, 30, tzinfo=UTC),
    )
    envelope = _request().model_copy(
        update={
            "config_digest": payload_digest(
                {
                    "engine_configuration": command.engine_configuration,
                    "instrument_catalog": command.instrument_catalog,
                    "strategy_configuration": command.strategy_configuration,
                }
            ),
            "payload_digest": payload_digest(command),
            "payload": command,
        }
    )

    loaded = launcher_module.validated_input_artifacts(
        envelope.model_dump(mode="json"), artifact_root
    )

    assert loaded == tuple(value for _name, value, _media_type in artifact_values)


def test_launcher_simulation_profile_reads_five_inputs_and_binds_stdout_event(
    launcher_module, tmp_path: Path
) -> None:
    artifact_names = (
        "engine_configuration",
        "instrument_catalog",
        "strategy_configuration",
        "market_data",
        "simulation_scenario",
    )
    artifact_values = _simulation_fixture("event-digest")
    artifact_root = tmp_path / "simulation-artifacts"
    artifact_root.mkdir()
    envelope = _simulation_request(artifact_values)
    assert isinstance(envelope.payload, RunBacktestSimulation)
    for name, value, reference in zip(
        artifact_names,
        artifact_values,
        (
            envelope.payload.engine_configuration,
            envelope.payload.instrument_catalog,
            envelope.payload.strategy_configuration,
            envelope.payload.market_data,
            envelope.payload.simulation_scenario,
        ),
        strict=True,
    ):
        media_type = reference.media_type
        digest = hashlib.sha256(value).hexdigest()
        extension = ".jsonl" if media_type == "application/jsonl" else ".json"
        (artifact_root / f"{name}-{digest}{extension}").write_bytes(value)
    raw_request = canonical_json_bytes(envelope)
    request_path = tmp_path / "simulation-request.json"
    sidecar_path = tmp_path / "simulation-request.sha256"
    request_path.write_bytes(raw_request)
    sidecar_path.write_text(
        hashlib.sha256(raw_request).hexdigest() + "\n", encoding="ascii"
    )

    accepted = launcher_module.validated_request(
        request_path,
        sidecar_path,
        profile="execution-simulation",
    )
    artifacts = launcher_module.validated_input_artifacts(
        accepted,
        artifact_root,
        profile="execution-simulation",
    )
    fixture = launcher_module.validate_simulation_fixture_inputs(accepted, artifacts)
    result = launcher_module.run_execution_simulation(fixture)
    event = launcher_module._simulation_event(accepted, artifacts, result)
    parsed = EngineEventEnvelope.model_validate_json(canonical_json_bytes(event))

    assert len(artifacts) == 5
    assert parsed.payload.event_type == "NautilusBacktestSimulationCompleted"
    attributes = {item.name: item.value for item in parsed.payload.attributes}
    assert attributes == {
        "average_entry_price": "100",
        "event_digest": result["event_digest"],
        "fees": "0.1",
        "filled_quantity": "1",
        "input_artifacts_sha256": hashlib.sha256(
            canonical_json_bytes(
                {
                    name: hashlib.sha256(value).hexdigest()
                    for name, value in zip(artifact_names, artifacts, strict=True)
                }
            )
        ).hexdigest(),
        "iterations": 1,
        "position_quantity": "1",
        "realized_pnl": "0",
        "remaining_quantity": "0",
        "scenario_digest": envelope.payload.simulation_scenario.sha256,
        "scenario_id": "event-digest",
        "stop_take_profit_precedence": "stop-first",
        "total_events": 2,
        "total_fills": 1,
        "total_orders": 1,
        "total_positions": 1,
        "unrealized_pnl": "1",
    }
    with pytest.raises(ValueError, match="RunBacktest"):
        launcher_module.validated_request(request_path, sidecar_path)


def test_launcher_rejects_duplicate_simulation_artifact_references(
    launcher_module, tmp_path: Path
) -> None:
    envelope = _request().model_dump(mode="json")
    payload = dict(envelope["payload"])
    payload["command_type"] = "RunBacktestSimulation"
    payload["simulation_scenario"] = payload["engine_configuration"]
    envelope["payload"] = payload
    envelope["payload_digest"] = hashlib.sha256(
        canonical_json_bytes(payload)
    ).hexdigest()
    raw_request = canonical_json_bytes(envelope)
    request_path = tmp_path / "duplicate-simulation-request.json"
    sidecar_path = tmp_path / "duplicate-simulation-request.sha256"
    request_path.write_bytes(raw_request)
    sidecar_path.write_text(
        hashlib.sha256(raw_request).hexdigest() + "\n", encoding="ascii"
    )

    with pytest.raises(ValueError, match="duplicate artifact"):
        launcher_module.validated_request(
            request_path,
            sidecar_path,
            profile="execution-simulation",
        )


def test_commission_mapping_sums_only_numeric_values_fail_closed(
    launcher_module,
) -> None:
    assert launcher_module._normalize_commissions(
        {
            "BTC": _EngineAmount("0.000001"),
            "USDT": _EngineAmount("0.125"),
        }
    ) == Decimal("0.125001")

    for unsupported in (
        _EngineAmount("0.1"),
        [_EngineAmount("0.1")],
        {"USDT": object()},
        {"USDT": _EngineAmount("NaN")},
    ):
        with pytest.raises(ValueError, match="commission"):
            launcher_module._normalize_commissions(unsupported)


def test_account_balances_are_numeric_and_internally_consistent(
    launcher_module,
) -> None:
    account = SimpleNamespace(
        balances=lambda: {
            "USDT": SimpleNamespace(
                total=_EngineAmount("1000"),
                locked=_EngineAmount("25"),
                free=_EngineAmount("975"),
            )
        }
    )

    assert launcher_module._account_balance_count(account) == 1

    account.balances = lambda: {
        "USDT": SimpleNamespace(
            total=_EngineAmount("1000"),
            locked=_EngineAmount("25"),
            free=_EngineAmount("974"),
        )
    }
    with pytest.raises(ValueError, match="balance"):
        launcher_module._account_balance_count(account)


def test_short_cash_account_starts_with_exact_base_inventory(
    launcher_module,
) -> None:
    assert launcher_module._starting_balance_plan(Decimal("2")) == (
        ("USDT", Decimal("1000000")),
    )
    assert launcher_module._starting_balance_plan(Decimal("-2")) == (
        ("USDT", Decimal("1000000")),
        ("BTC", Decimal("2")),
    )


def test_canonical_nautilus_result_record_is_json_native_and_run_invariant(
    launcher_module,
) -> None:
    strategy_events = [
        {"event_type": "order-created", "quantity": "1", "sequence": 0},
        {
            "event_time": "2026-08-05T12:00:00Z",
            "event_type": "fill",
            "price": "100",
            "quantity": "1",
            "sequence": 1,
        },
    ]

    record = launcher_module._canonical_nautilus_result_record(
        strategy_events=strategy_events,
        iterations=1,
        order_count=1,
        fill_count=1,
        filled_quantity=Decimal("1.000000"),
        position_count=1,
        position_quantity=Decimal("1.000000"),
        average_entry_price=Decimal("100.000000"),
        realized_pnl=Decimal("0.00"),
        unrealized_pnl=Decimal("1.00"),
        account_balance_count=1,
        commissions=Decimal("0.1000"),
    )

    assert record == {
        "account": {"balance_count": 1, "commissions": "0.1"},
        "engine": {"iterations": 1},
        "orders": {
            "count": 1,
            "filled_count": 1,
            "filled_quantity": "1",
        },
        "positions": {
            "average_entry_price": "100",
            "count": 1,
            "quantity": "1",
            "realized_pnl": "0",
            "unrealized_pnl": "1",
        },
        "schema_version": "nautilus-simulation-result-v1",
        "strategy_events": strategy_events,
    }
    assert json.loads(launcher_module._canonical_json_bytes(record)) == record
    assert not ({"run_id", "event_time", "started_at", "finished_at"} & set(record))


@pytest.mark.parametrize(
    "strategy_events",
    (
        ({"event_type": "fill"},),
        [{"event_type": "fill", "native": object()}],
        [{1: "non-string-key"}],
    ),
)
def test_canonical_nautilus_result_record_rejects_non_json_strategy_events(
    launcher_module, strategy_events: object
) -> None:
    with pytest.raises(ValueError, match="strategy event"):
        launcher_module._canonical_nautilus_result_record(
            strategy_events=strategy_events,
            iterations=0,
            order_count=0,
            fill_count=0,
            filled_quantity=Decimal(0),
            position_count=0,
            position_quantity=Decimal(0),
            average_entry_price=Decimal(0),
            realized_pnl=Decimal(0),
            unrealized_pnl=Decimal(0),
            account_balance_count=1,
            commissions=Decimal(0),
        )


def test_gross_realized_pnl_uses_actual_fills_without_double_counting_fees(
    launcher_module,
) -> None:
    entry = [
        {"event_type": "order-created", "quantity": "1", "sequence": 0},
        {
            "event_time": "2026-08-05T12:00:00Z",
            "event_type": "fill",
            "price": "100",
            "quantity": "1",
            "sequence": 1,
        },
    ]
    closed = [
        *entry,
        {"event_type": "exit-order-created", "reason": "stop", "sequence": 2},
        {
            "event_time": "2026-08-05T12:00:00Z",
            "event_type": "fill",
            "price": "98",
            "quantity": "-1",
            "sequence": 3,
        },
        {"event_type": "position-closed", "sequence": 4},
    ]

    assert launcher_module._gross_realized_pnl(entry) == Decimal("0")
    assert launcher_module._gross_realized_pnl(closed) == Decimal("-2")
    assert launcher_module._normalize_commissions(
        {"USDT": _EngineAmount("0.198")}
    ) == Decimal("0.198")


def test_gross_realized_pnl_uses_fixed_context_not_ambient_precision(
    launcher_module,
) -> None:
    events = [
        {"event_type": "fill", "price": "100.123456789", "quantity": "1"},
        {"event_type": "fill", "price": "100.987654321", "quantity": "1"},
        {"event_type": "fill", "price": "101.5", "quantity": "-2"},
    ]

    with localcontext(Context(prec=5)):
        observed = launcher_module._gross_realized_pnl(events)

    assert observed == Decimal("1.888888890")


@pytest.mark.parametrize(
    ("scenario_id", "expected"),
    [
        (
            "long-accounting",
            {
                "filled_quantity": "2",
                "fees": "0.2",
                "position_quantity": "2",
                "remaining_quantity": "0",
                "unrealized_pnl": "2",
            },
        ),
        (
            "short-accounting",
            {
                "filled_quantity": "-2",
                "fees": "0.198",
                "position_quantity": "-2",
                "remaining_quantity": "0",
                "unrealized_pnl": "-4",
            },
        ),
        (
            "partial-fill",
            {
                "filled_quantity": "1",
                "position_quantity": "1",
                "remaining_quantity": "2",
                "total_fills": 1,
            },
        ),
        (
            "same-bar-stop-take-profit",
            {
                "position_quantity": "0",
                "realized_pnl": "-2",
                "stop_take_profit_precedence": "stop-first",
                "total_fills": 2,
                "total_orders": 2,
            },
        ),
        (
            "stale-quote",
            {
                "filled_quantity": "0",
                "position_quantity": "0",
                "remaining_quantity": "1",
                "total_fills": 0,
            },
        ),
        (
            "zero-liquidity",
            {
                "filled_quantity": "0",
                "position_quantity": "0",
                "remaining_quantity": "1",
                "total_fills": 0,
            },
        ),
        (
            "session-boundary",
            {
                "average_entry_price": "102",
                "filled_quantity": "1",
                "iterations": 2,
                "position_quantity": "1",
            },
        ),
        (
            "event-digest",
            {
                "event_digest": "31ca501f78a3ac250c0fc7d7d8d38d9fb4acbb51bae7c3b15d39c311082c6baa",
                "total_events": 2,
            },
        ),
    ],
)
def test_execution_simulation_covers_the_fixed_scenario_matrix(
    launcher_module, scenario_id: str, expected: dict[str, object]
) -> None:
    artifacts = _simulation_fixture(scenario_id)
    request = _simulation_request(artifacts).model_dump(mode="json")

    fixture = launcher_module.validate_simulation_fixture_inputs(request, artifacts)
    result = launcher_module.run_execution_simulation(fixture)

    assert result["scenario_id"] == scenario_id
    assert result.items() >= expected.items()
    assert all(not isinstance(value, float) for value in result.values())


@pytest.mark.parametrize("scenario_id", _SCENARIO_IDS)
def test_each_complete_simulation_envelope_is_accepted_and_emitted(
    launcher_module, scenario_id: str
) -> None:
    artifacts = _simulation_fixture(scenario_id)
    envelope = _simulation_request(artifacts)
    raw = canonical_json_bytes(envelope)
    accepted = launcher_module._validate_request(
        json.loads(raw), raw, profile="execution-simulation"
    )
    fixture = launcher_module.validate_simulation_fixture_inputs(accepted, artifacts)
    result = launcher_module.run_execution_simulation(fixture)
    event = launcher_module._simulation_event(accepted, artifacts, result)

    parsed = EngineEventEnvelope.model_validate_json(canonical_json_bytes(event))
    attributes = {item.name: item.value for item in parsed.payload.attributes}

    assert parsed.payload.event_type == "NautilusBacktestSimulationCompleted"
    assert attributes["scenario_id"] == scenario_id
    assert set(attributes) == {
        "average_entry_price",
        "event_digest",
        "fees",
        "filled_quantity",
        "input_artifacts_sha256",
        "iterations",
        "position_quantity",
        "realized_pnl",
        "remaining_quantity",
        "scenario_digest",
        "scenario_id",
        "stop_take_profit_precedence",
        "total_events",
        "total_fills",
        "total_orders",
        "total_positions",
        "unrealized_pnl",
    }


def test_execution_simulation_profile_rejects_zero_order_envelope(
    launcher_module,
) -> None:
    envelope = _request()
    raw = canonical_json_bytes(envelope)

    with pytest.raises(ValueError, match="RunBacktestSimulation"):
        launcher_module._validate_request(
            json.loads(raw), raw, profile="execution-simulation"
        )


@pytest.mark.parametrize(
    ("scenario_id", "mutation"),
    [
        ("long-accounting", "zero-liquidity"),
        ("short-accounting", "closed-session"),
        ("partial-fill", "zero-liquidity"),
        ("same-bar-stop-take-profit", "closed-session"),
        ("stale-quote", "closed-session"),
        ("zero-liquidity", "stale-quote"),
        ("session-boundary", "zero-liquidity"),
        ("event-digest", "zero-liquidity"),
    ],
)
def test_scenario_identifiers_reject_semantic_precondition_violations(
    launcher_module, scenario_id: str, mutation: str
) -> None:
    artifacts = list(_simulation_fixture(scenario_id))
    scenario = json.loads(artifacts[4])
    if mutation == "zero-liquidity":
        scenario["liquidity_limit"] = "0"
    elif mutation == "closed-session":
        scenario["events"][-1]["session_open"] = False
    else:
        scenario["events"][0]["quote_time"] = "2026-08-05T11:58:00Z"
    artifacts[4] = _canonical(scenario)
    bound = tuple(artifacts)

    with pytest.raises(ValueError, match="semantic precondition"):
        launcher_module.validate_simulation_fixture_inputs(
            _simulation_request(bound).model_dump(mode="json"), bound
        )


def test_same_bar_long_requires_stop_below_executable_entry_below_take(
    launcher_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts = list(_simulation_fixture("same-bar-stop-take-profit"))
    scenario = json.loads(artifacts[4])
    scenario["stop_price"] = "102"
    scenario["take_profit_price"] = "98"
    artifacts[4] = _canonical(scenario)
    bound = tuple(artifacts)

    with pytest.raises(ValueError, match="semantic precondition"):
        launcher_module.validate_simulation_fixture_inputs(
            _simulation_request(bound).model_dump(mode="json"), bound
        )

    _assert_complete_envelope_rejects_before_nautilus(
        launcher_module, monkeypatch, bound
    )


@pytest.mark.parametrize(
    ("value", "maximum", "label"),
    [
        ("17014118346047", "17014118346046", "price"),
        ("34028236692094", "34028236692093", "quantity"),
    ],
)
def test_nautilus_fixed_point_bound_is_fail_closed(
    launcher_module, value: str, maximum: str, label: str
) -> None:
    with pytest.raises(ValueError, match="Nautilus fixed-point"):
        launcher_module._require_nautilus_fixed_point_bound(
            Decimal(value), maximum=Decimal(maximum), label=label
        )


@pytest.mark.parametrize(
    ("price", "quantity"),
    [
        ("17014118346047", None),
        (None, "34028236692094"),
    ],
)
def test_complete_hash_rebound_over_limit_envelopes_reject_before_nautilus(
    launcher_module,
    monkeypatch: pytest.MonkeyPatch,
    price: str | None,
    quantity: str | None,
) -> None:
    bound = _with_complete_simulation_bound(
        _simulation_fixture("event-digest"), price=price, quantity=quantity
    )

    with pytest.raises(ValueError, match="Nautilus fixed-point"):
        launcher_module.validate_simulation_fixture_inputs(
            _simulation_request(bound).model_dump(mode="json"), bound
        )

    _assert_complete_envelope_rejects_before_nautilus(
        launcher_module, monkeypatch, bound
    )


@pytest.mark.parametrize(
    ("price", "quantity"),
    [
        ("17014118346046", None),
        (None, "34028236692093"),
    ],
)
def test_complete_hash_rebound_exact_fixed_point_limits_are_representable(
    launcher_module, price: str | None, quantity: str | None
) -> None:
    artifacts = _with_complete_simulation_bound(
        _simulation_fixture("event-digest"), price=price, quantity=quantity
    )

    fixture = launcher_module.validate_simulation_fixture_inputs(
        _simulation_request(artifacts).model_dump(mode="json"), artifacts
    )

    assert fixture["target_quantity"] != 0


def test_unrepresentable_slipped_entry_rejects_before_nautilus(
    launcher_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts = list(_simulation_fixture("event-digest"))
    scenario = json.loads(artifacts[4])
    scenario["events"][0]["ask"] = "17014118346046"
    scenario["events"][0]["bid"] = "17014118346046"
    scenario["slippage_bps"] = "1"
    artifacts[4] = _canonical(scenario)
    bound = tuple(artifacts)

    with pytest.raises(ValueError, match="Nautilus fixed-point"):
        launcher_module.validate_simulation_fixture_inputs(
            _simulation_request(bound).model_dump(mode="json"), bound
        )

    _assert_complete_envelope_rejects_before_nautilus(
        launcher_module, monkeypatch, bound
    )


def test_changed_strategy_digest_rejects_before_nautilus(
    launcher_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifacts = list(_simulation_fixture("event-digest"))
    scenario = json.loads(artifacts[4])
    scenario["strategy_sha256"] = "0" * 64
    artifacts[4] = _canonical(scenario)
    bound = tuple(artifacts)

    with pytest.raises(ValueError, match="strategy binding"):
        launcher_module.validate_simulation_fixture_inputs(
            _simulation_request(bound).model_dump(mode="json"), bound
        )

    _assert_complete_envelope_rejects_before_nautilus(
        launcher_module, monkeypatch, bound
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("target", "123456789012345678901234567890123456789"),
        ("fee", "0.000000000000000000000000000000000000001"),
    ],
)
def test_simulation_rejects_decimal_coefficient_or_exponent_beyond_bound(
    launcher_module, field: str, value: str
) -> None:
    artifacts = _simulation_fixture("partial-fill")
    if field == "target":
        bound = _with_simulation_target(artifacts, value)
    else:
        changed = list(artifacts)
        scenario = json.loads(changed[4])
        scenario["fee_rate"] = value
        changed[4] = _canonical(scenario)
        bound = tuple(changed)

    with pytest.raises(ValueError, match="decimal bound"):
        launcher_module.validate_simulation_fixture_inputs(
            _simulation_request(bound).model_dump(mode="json"), bound
        )


def test_simulation_decimal_arithmetic_is_isolated_from_ambient_context(
    launcher_module,
) -> None:
    target = "34028236692093"
    artifacts = _with_simulation_target(_simulation_fixture("partial-fill"), target)
    request = _simulation_request(artifacts).model_dump(mode="json")

    with localcontext(Context(prec=6)):
        fixture = launcher_module.validate_simulation_fixture_inputs(request, artifacts)
        result = launcher_module.run_execution_simulation(fixture)

    assert result["filled_quantity"] == "1"
    assert result["remaining_quantity"] == "34028236692092"


def test_execution_simulation_replay_is_byte_identical(launcher_module) -> None:
    artifacts = _simulation_fixture("event-digest")
    request = _simulation_request(artifacts).model_dump(mode="json")
    fixture = launcher_module.validate_simulation_fixture_inputs(request, artifacts)

    first = launcher_module._simulation_event(
        request, artifacts, launcher_module.run_execution_simulation(fixture)
    )
    second = launcher_module._simulation_event(
        request, artifacts, launcher_module.run_execution_simulation(fixture)
    )

    assert launcher_module._canonical_json_bytes(first) == launcher_module._canonical_json_bytes(
        second
    )


@pytest.mark.parametrize("scenario_id", _SCENARIO_IDS)
def test_each_changed_scenario_identifier_contract_is_rejected_before_execution(
    launcher_module, scenario_id: str
) -> None:
    artifacts = list(_simulation_fixture(scenario_id))
    scenario = json.loads(artifacts[4])
    scenario["schema_version"] = "changed-scenario-contract"
    artifacts[4] = _canonical(scenario)
    bound = tuple(artifacts)

    with pytest.raises(ValueError, match="scenario"):
        launcher_module.validate_simulation_fixture_inputs(
            _simulation_request(bound).model_dump(mode="json"), bound
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("float", "decimal"),
        ("unknown-key", "fields"),
        ("provider", "fields"),
        ("module", "fields"),
        ("writable-path", "fields"),
        ("catalog-drift", "catalog"),
        ("outside-window", "window"),
        ("instrument-precision", "precision"),
        ("unknown-precedence", "precedence"),
    ],
)
def test_simulation_scenario_forbidden_inputs_fail_closed(
    launcher_module, mutation: str, message: str
) -> None:
    artifacts = list(_simulation_fixture("event-digest"))
    scenario = json.loads(artifacts[4])
    if mutation == "float":
        scenario["fee_rate"] = 0.001
    elif mutation == "unknown-key":
        scenario["unexpected"] = True
    elif mutation == "provider":
        scenario["execution_provider"] = "exchange"
    elif mutation == "module":
        scenario["strategy_module"] = "arbitrary.module"
    elif mutation == "writable-path":
        scenario["output_path"] = "/tmp/result.json"
    elif mutation == "catalog-drift":
        scenario["catalog_sha256"] = "0" * 64
    elif mutation == "outside-window":
        scenario["events"][0]["event_time"] = "2026-08-05T13:00:00Z"
    elif mutation == "instrument-precision":
        scenario["events"][0]["ask"] = "100.001"
    else:
        scenario["stop_take_profit_precedence"] = "take-profit-first"
    artifacts[4] = _canonical(scenario)
    bound = tuple(artifacts)

    with pytest.raises(ValueError, match=message):
        launcher_module.validate_simulation_fixture_inputs(
            _simulation_request(bound).model_dump(mode="json"), bound
        )


def test_simulation_scenario_rejects_duplicate_json_keys(launcher_module) -> None:
    artifacts = list(_simulation_fixture("event-digest"))
    artifacts[4] = artifacts[4].replace(
        b'{"catalog_sha256":', b'{"scenario_id":"event-digest","catalog_sha256":'
    )
    bound = tuple(artifacts)

    with pytest.raises(ValueError, match="duplicate"):
        launcher_module.validate_simulation_fixture_inputs(
            _simulation_request(bound).model_dump(mode="json"), bound
        )


def test_launcher_accepts_only_a_zero_order_04a_catalog_and_04b_target(
    launcher_module,
) -> None:
    configuration = json.dumps(
        {
            "execution_mode": "zero-order",
            "run_analysis": False,
            "schema_version": "nautilus-backtest-engine-config-v1",
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    market_data = (
        b'{"close":"101.00","high":"102.00","low":"99.00","open":"100.00",'
        b'"open_time":"2026-08-05T12:00:00Z","volume":"12.500000"}\n'
    )
    catalog = json.dumps(
        {
            "canonical_rows_sha256": hashlib.sha256(
                b"[" + market_data[:-1] + b"]"
            ).hexdigest(),
            "content_digest": "a" * 64,
            "continuity": {"duplicate_report": [], "gap_report": [], "timeframe": "1m"},
            "fetched_at": "2026-08-05T12:01:00Z",
            "first_event_at": "2026-08-05T12:00:00Z",
            "importer_version": "fixture-catalog-v1",
            "instrument": {"product_type": "crypto_spot", "symbol": "BTCUSDT", "venue": "BINANCE"},
            "known_at": "2026-08-05T12:01:00Z",
            "last_event_at": "2026-08-05T12:00:00Z",
            "normalization_version": "market-normalization-v1",
            "observed_at": "2026-08-05T12:01:00Z",
            "parquet_sha256": "b" * 64,
            "provider": "deterministic-fixture-v1",
            "provenance_schema_version": "market-data-v1",
            "raw_evidence_sha256": "c" * 64,
            "row_count": 1,
            "schema_version": "market-dataset-manifest-v1",
            "snapshot_schema_version": "market-snapshot-v1",
            "timeframe": "1m",
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    target = json.dumps(
        {
            "effective_at": "2026-08-05T12:00:00Z",
            "positions": [],
            "schema_version": "1.0.0",
            "source_signal_ids": ["22222222-2222-4222-8222-222222222222"],
            "target_id": "11111111-1111-4111-8111-111111111111",
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    launcher_module.validate_zero_order_fixture_inputs(
        (configuration, catalog, target, market_data)
    )

    with pytest.raises(ValueError, match="zero target"):
        launcher_module.validate_zero_order_fixture_inputs(
            (configuration, catalog, target.replace(b"[]", b'[{}]'), market_data)
        )
    with pytest.raises(ValueError, match="strategy target"):
        launcher_module.validate_zero_order_fixture_inputs(
            (
                configuration,
                catalog,
                target.replace(b"2026-08-05T12:00:00Z", b"not-a-timestamp-Z"),
                market_data,
            )
        )
    with pytest.raises(ValueError, match="canonical rows"):
        launcher_module.validate_zero_order_fixture_inputs(
            (
                configuration,
                catalog,
                target,
                market_data.replace(b'"101.00"', b'"999.00"'),
            )
        )
