"""Canonical, bounded research-evidence artifact verification."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from packages.engine_contracts import canonical_json_bytes

from .models import ResearchGateEvidenceV1


class ResearchEvidenceArtifactError(ValueError):
    """An offline research-evidence artifact is not canonical or hash-bound."""


_MAX_EVIDENCE_ARTIFACT_BYTES = 2 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_FILENAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}\.json$", re.ASCII)
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class ResearchEvidenceArtifactReference:
    """One sealed, external evidence source selected by its exact SHA-256."""

    root: Path
    filename: str
    sha256: str


def _sealed_external_root(root: Path) -> None:
    if not isinstance(root, Path) or not root.is_absolute() or root == Path("/") or ".." in root.parts:
        raise ResearchEvidenceArtifactError("research evidence root is unsafe")
    try:
        root.relative_to(_REPOSITORY_ROOT)
    except ValueError:
        pass
    else:
        raise ResearchEvidenceArtifactError("research evidence root must remain external")
    try:
        if root.resolve(strict=True) != root:
            raise ResearchEvidenceArtifactError("research evidence root contains a symlink")
        observed = root.lstat()
    except ResearchEvidenceArtifactError:
        raise
    except OSError as exc:
        raise ResearchEvidenceArtifactError("research evidence root is unavailable") from exc
    if (
        stat.S_ISLNK(observed.st_mode)
        or not stat.S_ISDIR(observed.st_mode)
        or observed.st_uid != os.geteuid()
        or stat.S_IMODE(observed.st_mode) != 0o500
    ):
        raise ResearchEvidenceArtifactError("research evidence root is not sealed")


def _read_sealed_artifact(reference: ResearchEvidenceArtifactReference) -> bytes:
    if type(reference) is not ResearchEvidenceArtifactReference:
        raise TypeError("ResearchEvidenceArtifactReference is required")
    if not isinstance(reference.filename, str) or _FILENAME.fullmatch(reference.filename) is None:
        raise ResearchEvidenceArtifactError("research evidence filename is unsafe")
    if not isinstance(reference.sha256, str) or _SHA256.fullmatch(reference.sha256) is None:
        raise ResearchEvidenceArtifactError("research evidence digest is invalid")
    _sealed_external_root(reference.root)
    source = reference.root / reference.filename
    descriptor = -1
    try:
        descriptor = os.open(
            source, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) != 0o400
            or before.st_size <= 0
            or before.st_size > _MAX_EVIDENCE_ARTIFACT_BYTES
        ):
            raise ResearchEvidenceArtifactError("research evidence artifact is not sealed")
        blocks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            block = os.read(descriptor, min(65_536, remaining))
            if not block:
                raise ResearchEvidenceArtifactError("research evidence artifact changed while reading")
            blocks.append(block)
            remaining -= len(block)
        if os.read(descriptor, 1):
            raise ResearchEvidenceArtifactError("research evidence artifact changed while reading")
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_size) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
        ):
            raise ResearchEvidenceArtifactError("research evidence artifact changed while reading")
        value = b"".join(blocks)
    except ResearchEvidenceArtifactError:
        raise
    except OSError as exc:
        raise ResearchEvidenceArtifactError("research evidence artifact is unavailable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not hmac.compare_digest(hashlib.sha256(value).hexdigest(), reference.sha256):
        raise ResearchEvidenceArtifactError("research evidence artifact digest drifted")
    return value


def canonical_evidence_artifact_bytes(evidence: ResearchGateEvidenceV1) -> bytes:
    """Encode one typed evidence object into its only accepted artifact form."""

    if type(evidence) is not ResearchGateEvidenceV1:
        raise TypeError("ResearchGateEvidenceV1 is required")
    return canonical_json_bytes(evidence)


def load_verified_evidence(
    reference: ResearchEvidenceArtifactReference,
) -> tuple[ResearchGateEvidenceV1, bytes]:
    """Load the sole accepted external research evidence authority artifact."""

    artifact = _read_sealed_artifact(reference)
    try:
        document = json.loads(artifact)
        parsed = ResearchGateEvidenceV1.model_validate_json(artifact)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ResearchEvidenceArtifactError("research evidence artifact is invalid") from exc
    if canonical_json_bytes(document) != artifact:
        raise ResearchEvidenceArtifactError("research evidence artifact is not canonical")
    return parsed, artifact
