"""Private, temporary import scope for the sealed P1 wheel inventory."""

from __future__ import annotations

import os
import stat
import sys
import sysconfig
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


_MAX_MEMBERS = 20_000
_MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024


class RuntimeDependencyError(ValueError):
    """The sealed wheel inventory cannot be imported safely."""


def _require_stdlib_only_path() -> None:
    stdlib = Path(sysconfig.get_path("stdlib")).resolve(strict=True)
    platstdlib = Path(sysconfig.get_path("platstdlib")).resolve(strict=True)
    allowed = {
        Path("/engine"),
        stdlib,
        platstdlib,
        stdlib.parent / f"python{sys.version_info.major}{sys.version_info.minor}.zip",
    }
    if shared := sysconfig.get_config_var("DESTSHARED"):
        allowed.add(Path(shared).resolve(strict=True))
    try:
        observed = tuple(Path(value).resolve() for value in sys.path)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise RuntimeDependencyError("runtime sys.path is not stdlib-only") from exc
    if (
        not observed
        or len(observed) != len(set(observed))
        or any(path not in allowed for path in observed)
    ):
        raise RuntimeDependencyError("runtime sys.path is not stdlib-only")


def _validate_archive(archive: zipfile.ZipFile) -> None:
    members = archive.infolist()
    if not members or len(members) > _MAX_MEMBERS:
        raise RuntimeDependencyError("sealed wheel member inventory is invalid")
    seen: set[tuple[str, ...]] = set()
    total = 0
    for member in members:
        relative = Path(member.filename)
        mode = member.external_attr >> 16
        kind = stat.S_IFMT(mode)
        parts = relative.parts
        if (
            not member.filename
            or relative.is_absolute()
            or ".." in parts
            or (kind and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)))
        ):
            raise RuntimeDependencyError("sealed wheel contains an unsafe member")
        if not member.is_dir():
            key = tuple(parts)
            if key in seen:
                raise RuntimeDependencyError("sealed wheel contains a duplicate member")
            seen.add(key)
            total += member.file_size
    if total > _MAX_UNCOMPRESSED_BYTES:
        raise RuntimeDependencyError("sealed wheel extraction exceeds the fixed limit")


@contextmanager
def sealed_wheel_imports(
    wheels_root: Path = Path("/engine/wheels"),
    temporary_root: Path = Path("/tmp"),
) -> Iterator[tuple[Path, ...]]:
    """Extract attested wheels to one private tmpfs scope, then remove it."""

    _require_stdlib_only_path()
    wheels = tuple(sorted(wheels_root.glob("*.whl"), key=lambda path: path.name))
    if not wheels:
        raise RuntimeDependencyError("sealed wheel inventory is empty")
    original = tuple(sys.path)
    with tempfile.TemporaryDirectory(prefix="p1-wheel-scope-", dir=temporary_root) as raw:
        scope = Path(raw)
        scope.chmod(0o700)
        roots: list[Path] = []
        try:
            for wheel in wheels:
                descriptor = os.open(wheel, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
                try:
                    opened = os.fstat(descriptor)
                    if (
                        not stat.S_ISREG(opened.st_mode)
                        or stat.S_IMODE(opened.st_mode) != 0o400
                        or opened.st_nlink != 1
                    ):
                        raise RuntimeDependencyError("sealed wheel identity is invalid")
                    destination = scope / f"{len(roots):04d}"
                    destination.mkdir(mode=0o700)
                    with os.fdopen(os.dup(descriptor), "rb") as source, zipfile.ZipFile(source) as archive:
                        _validate_archive(archive)
                        archive.extractall(destination)
                    named = wheel.stat(follow_symlinks=False)
                    after = os.fstat(descriptor)
                    expected = (
                        opened.st_dev,
                        opened.st_ino,
                        opened.st_size,
                        opened.st_mode,
                        opened.st_nlink,
                        opened.st_mtime_ns,
                        opened.st_ctime_ns,
                    )
                    if (
                        (
                            named.st_dev,
                            named.st_ino,
                            named.st_size,
                            named.st_mode,
                            named.st_nlink,
                            named.st_mtime_ns,
                            named.st_ctime_ns,
                        )
                        != expected
                        or (
                            after.st_dev,
                            after.st_ino,
                            after.st_size,
                            after.st_mode,
                            after.st_nlink,
                            after.st_mtime_ns,
                            after.st_ctime_ns,
                        )
                        != expected
                    ):
                        raise RuntimeDependencyError("sealed wheel identity changed")
                except (OSError, zipfile.BadZipFile) as exc:
                    raise RuntimeDependencyError("sealed wheel is unreadable") from exc
                finally:
                    os.close(descriptor)
                roots.append(destination.resolve(strict=True))
            if len(roots) != len(set(roots)):
                raise RuntimeDependencyError("sealed wheel extraction roots are duplicated")
            sys.path[:] = [*original, *(str(root) for root in roots)]
            yield tuple(roots)
        finally:
            sys.path[:] = original
