from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from uuid import UUID

import pytest

from packages.engine_contracts import ArtifactReference, RunBacktest
from services.job_worker.engine_artifacts import (
    EngineArtifactBinding,
    HashBoundArtifactResolver,
)
from services.job_worker.engine_spawn import EngineSpawnError


@pytest.fixture
def sealed_artifacts() -> tuple[Path, tuple[ArtifactReference, ...]]:
    root = Path(tempfile.mkdtemp(prefix="engine-artifacts-", dir="/home/thenam176/.cache"))
    try:
        references = []
        for number, media_type in enumerate(
            ("application/json", "application/json", "application/json", "application/jsonl"),
            start=1,
        ):
            value = f'{{"artifact":{number}}}\n'.encode("ascii")
            path = root / f"artifact-{number}"
            path.write_bytes(value)
            path.chmod(0o400)
            references.append(
                ArtifactReference(
                    artifact_id=UUID(f"{number}{number}{number}{number}{number}{number}{number}{number}-1111-4111-8111-111111111111"),
                    sha256=hashlib.sha256(value).hexdigest(),
                    media_type=media_type,
                )
            )
        root.chmod(0o500)
        yield root, tuple(references)
    finally:
        root.chmod(0o700)
        for path in root.iterdir():
            if not path.is_symlink():
                path.chmod(0o600)
        shutil.rmtree(root)


def _request(references: tuple[ArtifactReference, ...]) -> RunBacktest:
    return RunBacktest(
        command_type="RunBacktest",
        engine_configuration=references[0],
        instrument_catalog=references[1],
        strategy_configuration=references[2],
        market_data=references[3],
        start_time="2026-08-05T12:00:00Z",
        end_time="2026-08-05T12:30:00Z",
    )


def test_resolver_returns_only_request_bound_sealed_artifacts(sealed_artifacts) -> None:
    root, references = sealed_artifacts
    resolver = HashBoundArtifactResolver(
        tuple(
            EngineArtifactBinding(reference, root / f"artifact-{index}")
            for index, reference in enumerate(references, start=1)
        )
    )

    inputs = resolver(_request(references))

    assert tuple(item.name for item in inputs) == (
        "engine_configuration",
        "instrument_catalog",
        "strategy_configuration",
        "market_data",
    )
    assert tuple(item.sha256 for item in inputs) == tuple(reference.sha256 for reference in references)


def test_resolver_rejects_digest_drift(sealed_artifacts) -> None:
    root, references = sealed_artifacts
    resolver = HashBoundArtifactResolver(
        tuple(
            EngineArtifactBinding(reference, root / f"artifact-{index}")
            for index, reference in enumerate(references, start=1)
        )
    )
    root.chmod(0o700)
    changed = root / "artifact-1"
    changed.chmod(0o600)
    changed.write_text("changed", encoding="ascii")
    changed.chmod(0o400)
    root.chmod(0o500)

    with pytest.raises(EngineSpawnError, match="digest"):
        resolver(_request(references))


def test_resolver_rejects_an_external_path_with_a_checkout_symlink_ancestor(
    sealed_artifacts,
) -> None:
    root, references = sealed_artifacts
    root.chmod(0o700)
    checkout_link = root / "checkout-link"
    checkout_link.symlink_to(Path(__file__).resolve().parents[2])
    root.chmod(0o500)
    forged = checkout_link / "pyproject.toml"
    resolver = HashBoundArtifactResolver(
        (
            EngineArtifactBinding(references[0], forged),
            *(
                EngineArtifactBinding(reference, root / f"artifact-{index}")
                for index, reference in enumerate(references[1:], start=2)
            ),
        )
    )

    with pytest.raises(EngineSpawnError, match="artifact path"):
        resolver(_request(references))
