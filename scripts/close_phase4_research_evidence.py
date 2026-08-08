#!/usr/bin/env python3
"""Close sealed Phase-4 research records and emit one canonical closure line."""

from __future__ import annotations

import argparse
import os
import stat
import sys
from collections.abc import Sequence
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.engine_contracts import canonical_json_bytes
from packages.research_validation import (
    CampaignEvidenceError,
    ResearchClosureError,
    close_ws04_research_campaign,
)


_CHECKOUT = Path(__file__).resolve().parents[1]
_RESULT_NAME = "ws04-campaign-closure-v2.json"
_IDENTITY_FIELDS = (
    "st_dev",
    "st_ino",
    "st_mode",
    "st_uid",
    "st_nlink",
    "st_size",
    "st_mtime_ns",
    "st_ctime_ns",
)


def _identity(observed: os.stat_result) -> tuple[int, ...]:
    return tuple(getattr(observed, field) for field in _IDENTITY_FIELDS)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--campaign-directory", required=True, type=Path)
    parser.add_argument("--campaign-sha256", required=True)
    parser.add_argument("--parity-record", required=True, type=Path)
    parser.add_argument("--parity-record-sha256", required=True)
    parser.add_argument("--paper-record", required=True, type=Path)
    parser.add_argument("--paper-record-sha256", required=True)
    parser.add_argument("--legacy-record-directory", required=True, type=Path)
    parser.add_argument("--legacy-records-sha256", required=True)
    parser.add_argument("--transport-root", required=True, type=Path)
    return parser


def _publish_closure(transport_root: Path, closure: object) -> None:
    parent_descriptor = -1
    root_descriptor = -1
    descriptor = -1
    created_identity: tuple[int, ...] | None = None
    published = False
    try:
        observed = transport_root.lstat()
        resolved = transport_root.resolve(strict=True)
        observed_parent = transport_root.parent.lstat()
        resolved_parent = transport_root.parent.resolve(strict=True)
    except OSError as exc:
        raise ResearchClosureError("research transport is unavailable") from exc
    if (
        not transport_root.is_absolute()
        or transport_root == Path("/")
        or ".." in transport_root.parts
        or resolved != transport_root
        or transport_root.is_relative_to(_CHECKOUT)
        or stat.S_ISLNK(observed.st_mode)
        or not stat.S_ISDIR(observed.st_mode)
        or observed.st_uid != os.geteuid()
        or stat.S_IMODE(observed.st_mode) != 0o700
        or resolved_parent != transport_root.parent
        or stat.S_ISLNK(observed_parent.st_mode)
        or not stat.S_ISDIR(observed_parent.st_mode)
        or observed_parent.st_uid != os.geteuid()
        or observed_parent.st_mode & 0o077
    ):
        raise ResearchClosureError("research transport is unsafe")
    value = canonical_json_bytes(closure) + b"\n"
    try:
        parent_descriptor = os.open(
            transport_root.parent,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        opened_parent = os.fstat(parent_descriptor)
        if _identity(opened_parent) != _identity(observed_parent):
            raise ResearchClosureError("research transport parent identity changed")
        parent_identity = _identity(opened_parent)
        root_descriptor = os.open(
            transport_root.name,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent_descriptor,
        )
        opened_root = os.fstat(root_descriptor)
        named_root = os.stat(
            transport_root.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if not (
            _identity(opened_root) == _identity(named_root) == _identity(observed)
        ) or os.listdir(root_descriptor):
            raise ResearchClosureError("research transport identity changed")
        root_identity = (opened_root.st_dev, opened_root.st_ino)
        descriptor = os.open(
            _RESULT_NAME,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o400,
            dir_fd=root_descriptor,
        )
        os.fchmod(descriptor, 0o400)
        created_identity = _identity(os.fstat(descriptor))
        remaining = memoryview(value)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("short closure write")
            remaining = remaining[written:]
        os.fsync(descriptor)
        current_parent = os.fstat(parent_descriptor)
        named_parent = transport_root.parent.lstat()
        current_root = os.fstat(root_descriptor)
        named_root = os.stat(
            transport_root.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        full_root = transport_root.lstat()
        opened = os.fstat(descriptor)
        named = os.stat(
            _RESULT_NAME,
            dir_fd=root_descriptor,
            follow_symlinks=False,
        )
        if not (
            _identity(current_parent) == _identity(named_parent) == parent_identity
            and (current_root.st_dev, current_root.st_ino)
            == (named_root.st_dev, named_root.st_ino)
            == (full_root.st_dev, full_root.st_ino)
            == root_identity
            and stat.S_IMODE(current_root.st_mode) == 0o700
            and _identity(opened) == _identity(named)
            and opened.st_uid == os.geteuid()
            and opened.st_nlink == 1
            and stat.S_IMODE(opened.st_mode) == 0o400
            and opened.st_size == len(value)
        ):
            raise ResearchClosureError("research closure identity changed")
        published = True
    except ResearchClosureError:
        raise
    except OSError as exc:
        raise ResearchClosureError("research closure cannot be sealed") from exc
    finally:
        try:
            if (
                descriptor >= 0
                and root_descriptor >= 0
                and created_identity is not None
                and not published
            ):
                try:
                    opened = os.fstat(descriptor)
                    named = os.stat(
                        _RESULT_NAME,
                        dir_fd=root_descriptor,
                        follow_symlinks=False,
                    )
                    if (
                        _identity(opened) == _identity(named)
                        and (opened.st_dev, opened.st_ino)
                        == (created_identity[0], created_identity[1])
                    ):
                        os.unlink(_RESULT_NAME, dir_fd=root_descriptor)
                except OSError:
                    pass
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if root_descriptor >= 0:
                os.close(root_descriptor)
            if parent_descriptor >= 0:
                os.close(parent_descriptor)


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        transport_root = arguments.transport_root
        closure = close_ws04_research_campaign(
            campaign_directory=arguments.campaign_directory,
            campaign_sha256=arguments.campaign_sha256,
            parity_record=arguments.parity_record,
            parity_record_sha256=arguments.parity_record_sha256,
            paper_record=arguments.paper_record,
            paper_record_sha256=arguments.paper_record_sha256,
            legacy_record_directory=arguments.legacy_record_directory,
            legacy_records_sha256=arguments.legacy_records_sha256,
        )
        _publish_closure(transport_root, closure)
    except (CampaignEvidenceError, ResearchClosureError, OSError, ValueError):
        print("error: Phase-4 research evidence did not close", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
