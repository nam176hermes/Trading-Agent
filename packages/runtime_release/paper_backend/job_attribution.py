"""Strict paper-worker attribution for production and Package 6 staging paths."""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


APPROVED_RESEARCH_OUTPUT_ROOT = Path(
    "/home/thenam176/.local/share/trading-agent/research-output"
)
APPROVED_WORKER_SCRATCHPAD_ROOT = Path(
    "/home/thenam176/.local/run/trading-agent/research-home/scratchpad"
)
_JOB_ID = re.compile(r"job_[0-9a-f]{32}\Z")
_ATTEMPT_ID = re.compile(r"attempt_[0-9a-f]{32}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_SAFE_FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,191}\Z")
_WORKER_KEYS = frozenset(
    {
        "TRADING_JOB_ID",
        "TRADING_JOB_ATTEMPT_ID",
        "TRADING_ATTEMPT_ID",
        "TRADING_REPORTS_DIR",
        "TRADING_SIGNAL_OUTPUT_DIR",
        "TRADING_RESEARCH_BACKEND_COMMIT",
        "TRADING_RESEARCH_SCRATCHPAD_ROOT",
    }
)


class ResearchInvocationError(RuntimeError):
    """Raised before research or result writes when attribution is unsafe."""


@dataclass(frozen=True, slots=True)
class ResearchInvocation:
    job_id: str
    attempt_id: str
    research_only: bool
    backend_commit: str
    reports_dir: Path
    signal_output_dir: Path
    replay_scratchpad_root: Path


def _canonical_tmp(path: Path) -> bool:
    return (
        path.is_absolute()
        and ".." not in path.parts
        and str(path) == os.path.normpath(path)
        and path.parts[:2] == ("/", "tmp")
    )


def _validate_directory(path: Path, label: str) -> None:
    if not path.is_absolute() or ".." in path.parts:
        raise ResearchInvocationError(f"{label} is not canonical")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open(path.anchor, flags)
    try:
        current_path = Path(path.anchor)
        for part in path.parts[1:]:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            current_path /= part
            info = os.fstat(descriptor)
            mode = stat.S_IMODE(info.st_mode)
            is_tmp = current_path == Path("/tmp")
            if not stat.S_ISDIR(info.st_mode):
                raise ResearchInvocationError(f"{label} is not a directory")
            if is_tmp:
                if mode != 0o1777:
                    raise ResearchInvocationError("/tmp policy is unsafe")
            elif info.st_uid not in {0, os.geteuid(), Path("/").stat().st_uid}:
                raise ResearchInvocationError(f"{label} owner is unsafe")
            elif current_path == path and mode != 0o700:
                raise ResearchInvocationError(f"{label} must be mode 0700")
            elif current_path != path and mode & 0o022:
                raise ResearchInvocationError(f"{label} ancestor is unsafe")
    except OSError as exc:
        raise ResearchInvocationError(f"{label} cannot be opened safely") from exc
    finally:
        os.close(descriptor)


def _resolve_paths(values: Mapping[str, str]) -> tuple[Path, Path, Path]:
    reports = Path(values["TRADING_REPORTS_DIR"])
    signals = Path(values["TRADING_SIGNAL_OUTPUT_DIR"])
    scratchpad = Path(values["TRADING_RESEARCH_SCRATCHPAD_ROOT"])
    production = (
        APPROVED_RESEARCH_OUTPUT_ROOT / "reports",
        APPROVED_RESEARCH_OUTPUT_ROOT / "signals",
        APPROVED_WORKER_SCRATCHPAD_ROOT,
    )
    if (reports, signals, scratchpad) != production:
        semantic_root_value = values.get("TRADING_DATA_ROOT")
        semantic_authority_value = values.get("TRADING_SEMANTIC_AUTHORITY_PATH")
        if not semantic_root_value or not semantic_authority_value:
            raise ResearchInvocationError(
                "disposable paths require complete semantic authority"
            )
        semantic_root = Path(semantic_root_value)
        semantic_authority = Path(semantic_authority_value)
        runtime_root = reports.parent
        if (
            not all(
                _canonical_tmp(path)
                for path in (
                    reports,
                    signals,
                    scratchpad,
                    semantic_root,
                    semantic_authority,
                )
            )
            or reports != runtime_root / "reports"
            or signals != runtime_root / "signals"
            or scratchpad != runtime_root / "scratch" / "scratchpad"
            or semantic_root.name != "input"
            or semantic_authority.name != "active.json"
            or semantic_root.parent != semantic_authority.parent
            or runtime_root.parent != semantic_root.parent.parent
        ):
            raise ResearchInvocationError(
                "disposable paths are not one exact Package 6 profile"
            )
    for path, label in (
        (reports, "reports directory"),
        (signals, "signals directory"),
        (scratchpad, "scratchpad directory"),
    ):
        _validate_directory(path, label)
    return reports, signals, scratchpad


def bootstrap_strict_worker_invocation(
    source: Mapping[str, str] | None = None,
) -> ResearchInvocation | None:
    values = os.environ if source is None else source
    if not any(key in values for key in _WORKER_KEYS):
        return None
    required = (
        "TRADING_JOB_ID",
        "TRADING_JOB_ATTEMPT_ID",
        "TRADING_REPORTS_DIR",
        "TRADING_SIGNAL_OUTPUT_DIR",
        "TRADING_RESEARCH_BACKEND_COMMIT",
        "TRADING_RESEARCH_SCRATCHPAD_ROOT",
    )
    if any(not values.get(key) for key in required) or "TRADING_ATTEMPT_ID" in values:
        raise ResearchInvocationError("strict worker attribution is incomplete")
    job_id = values["TRADING_JOB_ID"]
    attempt_id = values["TRADING_JOB_ATTEMPT_ID"]
    backend_commit = values["TRADING_RESEARCH_BACKEND_COMMIT"]
    if (
        _JOB_ID.fullmatch(job_id) is None
        or _ATTEMPT_ID.fullmatch(attempt_id) is None
        or _COMMIT.fullmatch(backend_commit) is None
    ):
        raise ResearchInvocationError("strict worker attribution is invalid")
    reports, signals, scratchpad = _resolve_paths(values)
    return ResearchInvocation(
        job_id,
        attempt_id,
        True,
        backend_commit,
        reports,
        signals,
        scratchpad,
    )


def with_lineage(
    document: Mapping[str, object], invocation: ResearchInvocation
) -> dict[str, object]:
    attributed = dict(document)
    attributed.update(
        {
            "job_id": invocation.job_id,
            "attempt_id": invocation.attempt_id,
            "research_only": True,
            "backend_commit": invocation.backend_commit,
        }
    )
    return attributed


def write_json_exclusive(
    directory: Path, filename: str, document: Mapping[str, object]
) -> Path:
    if _SAFE_FILENAME.fullmatch(filename) is None:
        raise ResearchInvocationError("result filename is unsafe")
    _validate_directory(directory, "result directory")
    raw = (
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        + b"\n"
    )
    directory_fd = os.open(
        directory,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
    )
    descriptor = -1
    try:
        descriptor = os.open(
            filename,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=directory_fd,
        )
        os.write(descriptor, raw)
        os.fsync(descriptor)
    except OSError as exc:
        raise ResearchInvocationError("result cannot be written exclusively") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(directory_fd)
    return directory / filename


__all__ = [
    "ResearchInvocationError",
    "bootstrap_strict_worker_invocation",
    "with_lineage",
    "write_json_exclusive",
]
