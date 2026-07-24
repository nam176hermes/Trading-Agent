"""Select six structured sources and atomically refresh semantic authority."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Callable

from packages.runtime_release.semantic import SEMANTIC_INPUT_ROOT
from scripts.build_phase4_semantic_manifest import (
    SemanticManifestBuildResult,
    build_semantic_manifest,
)


REPORTS_SOURCE_ROOT = Path("/home/thenam176/.hermes/crypto-research/reports")
MACRO_SOURCE_ROOT = Path("/home/thenam176/.hermes/crypto-research/memory/macro")
DESTINATION_ROOT = SEMANTIC_INPUT_ROOT
MANIFEST_PATH = Path("/etc/trading-agent/research-input-manifests/phase4-v1.json")
RUNTIME_UID = 1000
RUNTIME_GID = 1000
VALIDITY_MINUTES = 30
MAX_SOURCE_AGE = timedelta(hours=2)
MAX_FUTURE_SKEW = timedelta(seconds=30)
MAX_SOURCE_BYTES = 4 * 1024 * 1024
_REPORTS = ("macro", "sentiment", "onchain")
_CACHE_NAMES = {
    "fred_cache": "fred_cache.json",
    "cross_asset_cache": "yf_macro_cache.json",
    "crypto_global_cache": "coingecko_global_cache.json",
}
_REPORT_PATTERN = re.compile(
    r"(?P<kind>macro|sentiment|onchain)_report_(?P<stamp>[0-9]{8}_[0-9]{6})\.json\Z"
)


class SemanticRefreshError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("semantic input refresh rejected")


def _open_source_directory(path: Path) -> int:
    descriptor = None
    try:
        path = Path(path)
        if not path.is_absolute() or ".." in path.parts or str(path) != os.path.normpath(path):
            raise ValueError
        flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path.anchor, flags)
        for part in path.parts[1:]:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        raise SemanticRefreshError() from None


def _valid_payload(logical_name: str, payload: object) -> bool:
    if not isinstance(payload, dict) or not payload:
        return False
    if logical_name == "macro_report":
        confidence = payload.get("regime_confidence")
        return (
            isinstance(payload.get("regime"), str)
            and isinstance(confidence, (int, float)) and not isinstance(confidence, bool)
            and 0 <= confidence <= 1
        )
    if logical_name in {"sentiment_report", "onchain_report"}:
        return isinstance(payload.get("assets"), dict)
    return True


def _regular_at(
    directory_fd: int, directory: Path, name: str, *, logical_name: str,
    now: datetime, source_time: datetime | None = None,
) -> tuple[Path, tuple[int, int, int, str]]:
    descriptor = None
    try:
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if (
            stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_size > MAX_SOURCE_BYTES
        ):
            raise ValueError
        observed_time = source_time or datetime.fromtimestamp(metadata.st_mtime, tz=UTC)
        if observed_time > now + MAX_FUTURE_SKEW or now - observed_time > MAX_SOURCE_AGE:
            raise ValueError
        descriptor = os.open(
            name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory_fd,
        )
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise ValueError
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(descriptor, 65536):
            total += len(chunk)
            if total > MAX_SOURCE_BYTES:
                raise ValueError
            chunks.append(chunk)
        content = b"".join(chunks)
        if not _valid_payload(logical_name, json.loads(content)):
            raise ValueError
        return directory / name, (
            metadata.st_dev, metadata.st_ino, len(content), hashlib.sha256(content).hexdigest(),
        )
    except Exception:
        raise SemanticRefreshError() from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _select_sources(
    now: datetime,
) -> tuple[dict[str, Path], dict[str, tuple[int, int, int, str]]]:
    selected: dict[str, tuple[datetime, str]] = {}
    reports_fd = macro_fd = None
    try:
        reports_fd = _open_source_directory(REPORTS_SOURCE_ROOT)
        macro_fd = _open_source_directory(MACRO_SOURCE_ROOT)
        with os.scandir(reports_fd) as entries:
            for entry in entries:
                match = _REPORT_PATTERN.fullmatch(entry.name)
                if match is None:
                    continue
                stamp = datetime.strptime(match.group("stamp"), "%Y%m%d_%H%M%S").replace(tzinfo=UTC)
                kind = match.group("kind")
                current = selected.get(kind)
                if current is None or stamp > current[0]:
                    selected[kind] = (stamp, entry.name)
        if set(selected) != set(_REPORTS):
            raise ValueError
        sources: dict[str, Path] = {}
        attestations: dict[str, tuple[int, int, int, str]] = {}
        for kind in _REPORTS:
            stamp, name = selected[kind]
            logical = f"{kind}_report"
            path, attestation = _regular_at(
                reports_fd, REPORTS_SOURCE_ROOT, name,
                logical_name=logical, now=now, source_time=stamp,
            )
            sources[logical] = path
            attestations[logical] = attestation
        for logical, name in _CACHE_NAMES.items():
            path, attestation = _regular_at(
                macro_fd, MACRO_SOURCE_ROOT, name,
                logical_name=logical, now=now,
            )
            sources[logical] = path
            attestations[logical] = attestation
        return sources, attestations
    except SemanticRefreshError:
        raise
    except Exception:
        raise SemanticRefreshError() from None
    finally:
        for descriptor in (macro_fd, reports_fd):
            if descriptor is not None:
                os.close(descriptor)


def refresh(
    *, clock: Callable[[], datetime] = lambda: datetime.now(UTC), apply: bool = False,
) -> SemanticManifestBuildResult:
    if apply and os.geteuid() != 0:
        raise SemanticRefreshError()
    generated_at = clock()
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise SemanticRefreshError()
    generated_at = generated_at.astimezone(UTC)
    sources, attestations = _select_sources(generated_at)
    from services.job_worker.command_registry import APPROVED_BACKEND_REVISION

    manifest_version = generated_at.strftime("%Y%m%dT%H%M%SZ")
    arguments = {
        "sources": sources,
        "destination_root": DESTINATION_ROOT,
        "manifest_path": MANIFEST_PATH,
        "manifest_version": manifest_version,
        "backend_commit": APPROVED_BACKEND_REVISION,
        "runtime_uid": RUNTIME_UID,
        "runtime_gid": RUNTIME_GID,
        "generated_at": generated_at,
        "validity_minutes": VALIDITY_MINUTES,
        "expected_source_attestations": attestations,
    }
    planned = build_semantic_manifest(**arguments, apply=False)
    if not apply:
        return planned
    return build_semantic_manifest(
        **arguments, apply=True, approved_plan_digest=planned.plan_digest,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        refresh(apply=args.apply)
        return 0
    except Exception:
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = ["SemanticRefreshError", "main", "refresh"]
