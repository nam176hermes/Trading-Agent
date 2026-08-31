from __future__ import annotations

import sys
import sysconfig
import stat
import warnings
import zipfile
from pathlib import Path

import pytest

import engines.nautilus.runtime_v1.dependency_scope as scope_module
from engines.nautilus.runtime_v1.dependency_scope import (
    RuntimeDependencyError,
    _expected_link_count,
    sealed_wheel_imports,
)


def _wheel(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, value in members.items():
            archive.writestr(name, value)
    path.chmod(0o400)


def test_fixed_sandbox_wheels_require_anonymous_sealed_mounts() -> None:
    assert _expected_link_count(Path("/engine/wheels")) == 0
    assert _expected_link_count(Path("/tmp/unit-wheels")) == 1


def test_sealed_wheel_imports_is_scoped_and_rejects_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheels = tmp_path / "wheels"
    temporary = tmp_path / "temporary"
    wheels.mkdir()
    temporary.mkdir()
    _wheel(wheels / "fixture-1.whl", {"fixture_package/__init__.py": b"VALUE = 7\n"})

    original = tuple(
        dict.fromkeys(
            (
                sysconfig.get_path("stdlib"),
                sysconfig.get_path("platstdlib"),
                sysconfig.get_config_var("DESTSHARED"),
            )
        )
    )
    original = tuple(value for value in original if value)
    monkeypatch.setattr(sys, "path", list(original))
    with sealed_wheel_imports(wheels, temporary) as roots:
        assert len(roots) == 1
        assert tuple(sys.path) == (*original, str(roots[0]))
        assert (roots[0] / "fixture_package/__init__.py").read_bytes() == b"VALUE = 7\n"
    assert tuple(sys.path) == original
    assert tuple(temporary.iterdir()) == ()

    (wheels / "fixture-1.whl").chmod(0o600)
    (wheels / "fixture-1.whl").unlink()
    _wheel(wheels / "unsafe-1.whl", {"../escape": b"no"})
    with pytest.raises(RuntimeDependencyError, match="unsafe member"):
        with sealed_wheel_imports(wheels, temporary):
            pass
    assert not (tmp_path / "escape").exists()


def test_sealed_wheel_imports_accepts_only_the_fixed_script_boot_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheels = tmp_path / "wheels"
    temporary = tmp_path / "temporary"
    wheels.mkdir()
    temporary.mkdir()
    _wheel(wheels / "fixture.whl", {"package/__init__.py": b""})
    _stdlib_path(monkeypatch)
    sys.path[:0] = ["/engine", "/engine/runtime_v1"]

    with sealed_wheel_imports(wheels, temporary):
        assert sys.path[:2] == ["/engine", "/engine/runtime_v1"]

    sys.path[1] = "/engine/foreign"
    with pytest.raises(RuntimeDependencyError, match="stdlib-only"):
        with sealed_wheel_imports(wheels, temporary):
            pass


def _stdlib_path(monkeypatch: pytest.MonkeyPatch) -> None:
    values = tuple(
        dict.fromkeys(
            (
                sysconfig.get_path("stdlib"),
                sysconfig.get_path("platstdlib"),
                sysconfig.get_config_var("DESTSHARED"),
            )
        )
    )
    monkeypatch.setattr(sys, "path", [value for value in values if value])


def test_sealed_wheel_imports_rejects_duplicate_special_cap_ambient_and_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheels = tmp_path / "wheels"
    temporary = tmp_path / "temporary"
    wheels.mkdir()
    temporary.mkdir()
    _stdlib_path(monkeypatch)

    duplicate = wheels / "duplicate.whl"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(duplicate, "w") as archive:
            archive.writestr("package/value.py", b"one")
            archive.writestr("package/value.py", b"two")
    duplicate.chmod(0o400)
    with pytest.raises(RuntimeDependencyError, match="duplicate member"):
        with sealed_wheel_imports(wheels, temporary):
            pass

    duplicate.chmod(0o600)
    duplicate.unlink()
    special = wheels / "special.whl"
    link = zipfile.ZipInfo("package/link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(special, "w") as archive:
        archive.writestr(link, b"target")
    special.chmod(0o400)
    with pytest.raises(RuntimeDependencyError, match="unsafe member"):
        with sealed_wheel_imports(wheels, temporary):
            pass

    special.chmod(0o600)
    special.unlink()
    _wheel(wheels / "large.whl", {"package/value": b"12"})
    monkeypatch.setattr(scope_module, "_MAX_UNCOMPRESSED_BYTES", 1)
    with pytest.raises(RuntimeDependencyError, match="fixed limit"):
        with sealed_wheel_imports(wheels, temporary):
            pass
    monkeypatch.setattr(scope_module, "_MAX_UNCOMPRESSED_BYTES", 2 * 1024**3)

    sys.path.append("/tmp/ambient-site-packages")
    with pytest.raises(RuntimeDependencyError, match="stdlib-only"):
        with sealed_wheel_imports(wheels, temporary):
            pass
    sys.path.pop()
    (wheels / "large.whl").chmod(0o600)
    with pytest.raises(RuntimeDependencyError, match="identity"):
        with sealed_wheel_imports(wheels, temporary):
            pass


def test_sealed_wheel_imports_detects_in_place_identity_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wheels = tmp_path / "wheels"
    temporary = tmp_path / "temporary"
    wheels.mkdir()
    temporary.mkdir()
    wheel = wheels / "fixture.whl"
    _wheel(wheel, {"package/value.py": b"value = 1\n"})
    _stdlib_path(monkeypatch)
    extractall = zipfile.ZipFile.extractall

    def mutate(archive: zipfile.ZipFile, path: Path) -> None:
        extractall(archive, path)
        wheel.chmod(0o600)

    monkeypatch.setattr(zipfile.ZipFile, "extractall", mutate)
    with pytest.raises(RuntimeDependencyError, match="identity changed"):
        with sealed_wheel_imports(wheels, temporary):
            pass
