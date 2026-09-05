"""Pinned command identity for the separately qualified native P3 adapter."""

from pathlib import Path


PINNED_ENGINE_VERSION = "1.231.0"


def native_parity_command(python: Path, release: Path) -> tuple[str, ...]:
    if not python.is_absolute() or not release.is_absolute():
        raise ValueError("native adapter paths must be absolute protected paths")
    return (str(python), "-I", "-B", str(release / "scripts/run_p3_nautilus_parity.py"))


__all__ = ["PINNED_ENGINE_VERSION", "native_parity_command"]
