"""Explicit authority for hash-bound engine input artifacts."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from packages.engine_contracts import (
    ArtifactReference,
    RunBacktest,
    RunBacktestSimulation,
)

from .engine_spawn import HashBoundEngineInput
from .engine_spawn_interface import EngineSpawnError


_ROOT = Path(__file__).resolve().parents[2]
_ZERO_ORDER_NAMES = (
    "engine_configuration",
    "instrument_catalog",
    "strategy_configuration",
    "market_data",
)
_SIMULATION_NAMES = (*_ZERO_ORDER_NAMES, "simulation_scenario")


@dataclass(frozen=True, slots=True)
class EngineArtifactBinding:
    """One pre-provisioned external file for a specific artifact reference."""

    reference: ArtifactReference
    source: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        while block := os.read(descriptor, 1024 * 1024):
            digest.update(block)
        return digest.hexdigest()
    except OSError as exc:
        raise EngineSpawnError("ENGINE_INPUT_AUTHORITY_UNAVAILABLE", "artifact cannot be read") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


class HashBoundArtifactResolver:
    """Resolve only exact, sealed files that match request references."""

    def __init__(self, bindings: tuple[EngineArtifactBinding, ...]) -> None:
        if type(bindings) is not tuple or not bindings:
            raise TypeError("non-empty artifact bindings tuple is required")
        index: dict[tuple[str, str, str], EngineArtifactBinding] = {}
        for binding in bindings:
            if type(binding) is not EngineArtifactBinding:
                raise TypeError("exact EngineArtifactBinding entries are required")
            reference = binding.reference
            if type(reference) is not ArtifactReference or not isinstance(binding.source, Path):
                raise TypeError("artifact binding shape is invalid")
            key = (str(reference.artifact_id), reference.sha256, reference.media_type)
            if key in index:
                raise ValueError("artifact bindings contain a duplicate reference")
            index[key] = binding
        self._index = index

    @staticmethod
    def _attest(name: str, binding: EngineArtifactBinding) -> HashBoundEngineInput:
        source = binding.source
        if not source.is_absolute() or source == Path("/") or ".." in source.parts:
            raise EngineSpawnError("ENGINE_INPUT_AUTHORITY_INVALID", "artifact path is unsafe")
        try:
            if source.resolve(strict=True) != source:
                raise EngineSpawnError(
                    "ENGINE_INPUT_AUTHORITY_INVALID",
                    "artifact path contains a symlink ancestor",
                )
        except EngineSpawnError:
            raise
        except OSError as exc:
            raise EngineSpawnError(
                "ENGINE_INPUT_AUTHORITY_UNAVAILABLE", "artifact path is unavailable"
            ) from exc
        try:
            source.relative_to(_ROOT)
        except ValueError:
            pass
        else:
            raise EngineSpawnError("ENGINE_INPUT_AUTHORITY_INVALID", "artifact must remain external")
        try:
            observed = source.lstat()
        except OSError as exc:
            raise EngineSpawnError("ENGINE_INPUT_AUTHORITY_UNAVAILABLE", "artifact is unavailable") from exc
        if (
            stat.S_ISLNK(observed.st_mode)
            or not stat.S_ISREG(observed.st_mode)
            or observed.st_uid != os.geteuid()
            or observed.st_nlink != 1
            or stat.S_IMODE(observed.st_mode) != 0o400
            or observed.st_size <= 0
            or observed.st_size > 8 * 1024 * 1024
            or _sha256(source) != binding.reference.sha256
        ):
            raise EngineSpawnError("ENGINE_INPUT_AUTHORITY_INVALID", "artifact identity or digest drifted")
        return HashBoundEngineInput(
            name=name,
            reference=binding.reference,
            source=source,
            identity=(observed.st_dev, observed.st_ino),
            size=observed.st_size,
            mode=0o400,
            sha256=binding.reference.sha256,
        )

    def __call__(
        self, request: RunBacktest | RunBacktestSimulation
    ) -> tuple[HashBoundEngineInput, ...]:
        if type(request) is RunBacktest:
            names = _ZERO_ORDER_NAMES
        elif type(request) is RunBacktestSimulation:
            names = _SIMULATION_NAMES
        else:
            raise EngineSpawnError(
                "ENGINE_INPUT_AUTHORITY_INVALID",
                "exact backtest command is required",
            )
        values: list[HashBoundEngineInput] = []
        for name in names:
            reference = getattr(request, name)
            key = (str(reference.artifact_id), reference.sha256, reference.media_type)
            try:
                binding = self._index[key]
            except KeyError as exc:
                raise EngineSpawnError("ENGINE_INPUT_AUTHORITY_UNAVAILABLE", "artifact binding is missing") from exc
            values.append(self._attest(name, binding))
        return tuple(values)


__all__ = ["EngineArtifactBinding", "HashBoundArtifactResolver"]
