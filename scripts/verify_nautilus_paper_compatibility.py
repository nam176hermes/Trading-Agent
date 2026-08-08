#!/usr/bin/env python3
"""Run one finite paper-compatibility process and seal its digest-only result."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.engine_contracts import (
    ArtifactReference,
    ValidatePaperCompatibility,
    canonical_json_bytes,
)
from packages.nautilus_backtest import (
    PaperCompatibilityResultV1,
    capture_prepared_engine_process,
)
from services.job_worker.engine_artifacts import (
    EngineArtifactBinding,
    HashBoundArtifactResolver,
)
from services.job_worker.engine_spawn import (
    EngineSpawnProvider,
    consume_prepared_engine_spawn,
)
from services.job_worker.nautilus_closure import (
    NautilusClosureConfig,
    attest_nautilus_backtest_closure,
)


_CHECKOUT = Path(__file__).resolve().parents[1]
_RESULT_NAME = "paper-compatibility-result.json"
_CAMPAIGN_MANIFEST = "campaign-manifest.json"
_ARTIFACT_FILES = (
    "engine-configuration.json",
    "instrument-catalog.json",
    "strategy-configuration.json",
)
_ARTIFACT_FIELDS = (
    "engine_configuration_sha256",
    "instrument_catalog_sha256",
    "strategy_configuration_sha256",
)
_CAMPAIGN_FIELDS = {
    *_ARTIFACT_FIELDS,
    "schema_version",
    "strategy_source_sha256",
}
_EVENT_FIELDS = {
    "compatible",
    "engine_configuration_sha256",
    "event_type",
    "instrument_catalog_sha256",
    "scenario_campaign_sha256",
    "strategy_configuration_sha256",
    "strategy_source_sha256",
}


class PaperCompatibilityVerificationError(RuntimeError):
    """The finite compatibility proof or its publication is unsafe."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--candidate-closure", required=True, type=Path)
    parser.add_argument("--artifact-directory", required=True, type=Path)
    parser.add_argument("--sandbox", required=True, type=Path)
    parser.add_argument("--campaign-directory", required=True, type=Path)
    parser.add_argument("--parity-record", required=True, type=Path)
    parser.add_argument("--transport-root", required=True, type=Path)
    return parser


def paper_record_path(transport_root: Path) -> Path:
    return transport_root / _RESULT_NAME


def _canonical_object(raw: bytes, *, label: str) -> dict[str, object]:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for name, value in pairs:
            if name in result:
                raise PaperCompatibilityVerificationError(
                    f"{label} contains a duplicate field"
                )
            result[name] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=reject_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PaperCompatibilityVerificationError(f"{label} JSON is invalid") from exc
    if not isinstance(value, dict) or canonical_json_bytes(value) != raw:
        raise PaperCompatibilityVerificationError(f"{label} is not canonical")
    return value


def _validate_launcher_result(
    raw: bytes, command: ValidatePaperCompatibility
) -> dict[str, object]:
    value = _canonical_object(raw, label="paper launcher result")
    expected = {
        "compatible": True,
        "engine_configuration_sha256": command.engine_configuration.sha256,
        "event_type": "PaperCompatibilityValidated",
        "instrument_catalog_sha256": command.instrument_catalog.sha256,
        "scenario_campaign_sha256": command.scenario_campaign_sha256,
        "strategy_configuration_sha256": command.strategy_configuration.sha256,
        "strategy_source_sha256": command.strategy_source_sha256,
    }
    if set(value) != _EVENT_FIELDS or value != expected:
        raise PaperCompatibilityVerificationError(
            "paper launcher result is not bound to the reviewed command"
        )
    return value


def capture_paper_compatibility(
    *,
    provider: object,
    command: ValidatePaperCompatibility,
    candidate_closure_sha256: str,
    candidate_manifest_sha256: str,
    parity_record_sha256: str,
    popen_factory: object = subprocess.Popen,
    consume: Callable[[object], object] = consume_prepared_engine_spawn,
    capture: Callable[..., object] = capture_prepared_engine_process,
) -> PaperCompatibilityResultV1:
    """Own exactly one prepare, consume, and bounded captured-process call."""

    prepared = provider.prepare(command)
    built = consume(prepared)
    try:
        completed = capture(built, popen_factory=popen_factory)
    except subprocess.TimeoutExpired as exc:
        raise PaperCompatibilityVerificationError(
            "paper compatibility process exceeded its attested timeout"
        ) from exc
    if completed.returncode != 0:
        raise PaperCompatibilityVerificationError(
            "paper compatibility process exited unsuccessfully"
        )
    if completed.stderr != b"":
        raise PaperCompatibilityVerificationError(
            "paper compatibility process emitted stderr"
        )
    stdout = completed.stdout
    if (
        not isinstance(stdout, bytes)
        or not stdout.endswith(b"\n")
        or stdout.count(b"\n") != 1
        or stdout == b"\n"
    ):
        raise PaperCompatibilityVerificationError(
            "paper compatibility stdout must contain exactly one canonical line"
        )
    launcher_raw = stdout[:-1]
    _validate_launcher_result(launcher_raw, command)
    return PaperCompatibilityResultV1.create(
        candidate_closure_sha256=candidate_closure_sha256,
        candidate_manifest_sha256=candidate_manifest_sha256,
        engine_configuration_sha256=command.engine_configuration.sha256,
        instrument_catalog_sha256=command.instrument_catalog.sha256,
        strategy_configuration_sha256=command.strategy_configuration.sha256,
        strategy_source_sha256=command.strategy_source_sha256,
        scenario_campaign_sha256=command.scenario_campaign_sha256,
        parity_record_sha256=parity_record_sha256,
        launcher_result_sha256=hashlib.sha256(launcher_raw).hexdigest(),
    )


def _private_directory(path: Path, *, mode: int, label: str) -> None:
    if not path.is_absolute() or path == Path("/") or ".." in path.parts:
        raise PaperCompatibilityVerificationError(f"{label} path is unsafe")
    try:
        observed = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise PaperCompatibilityVerificationError(f"{label} is unavailable") from exc
    if (
        resolved != path
        or stat.S_ISLNK(observed.st_mode)
        or not stat.S_ISDIR(observed.st_mode)
        or observed.st_uid != os.geteuid()
        or stat.S_IMODE(observed.st_mode) != mode
    ):
        raise PaperCompatibilityVerificationError(f"{label} is unsafe")


def publish_paper_compatibility_result(
    transport_root: Path, result: PaperCompatibilityResultV1
) -> Path:
    """Publish the one fixed no-clobber result at mode 0400."""

    _private_directory(
        transport_root, mode=0o700, label="paper compatibility transport root"
    )
    path = paper_record_path(transport_root)
    raw = canonical_json_bytes(result) + b"\n"
    parent_fd = -1
    descriptor = -1
    try:
        parent_fd = os.open(
            transport_root,
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        descriptor = os.open(
            path.name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o400,
            dir_fd=parent_fd,
        )
        os.fchmod(descriptor, 0o400)
        remaining = memoryview(raw)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError("short result write")
            remaining = remaining[written:]
        os.fsync(descriptor)
    except FileExistsError as exc:
        raise PaperCompatibilityVerificationError(
            "paper compatibility result already exists"
        ) from exc
    except OSError as exc:
        raise PaperCompatibilityVerificationError(
            "paper compatibility result cannot be published"
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent_fd >= 0:
            os.close(parent_fd)
    return path


def _sealed_bytes(path: Path, *, label: str) -> bytes:
    try:
        observed = path.lstat()
        raw = path.read_bytes()
    except OSError as exc:
        raise PaperCompatibilityVerificationError(f"{label} is unavailable") from exc
    if (
        stat.S_ISLNK(observed.st_mode)
        or not stat.S_ISREG(observed.st_mode)
        or observed.st_uid != os.geteuid()
        or observed.st_nlink != 1
        or stat.S_IMODE(observed.st_mode) != 0o400
        or not raw
    ):
        raise PaperCompatibilityVerificationError(f"{label} is not sealed")
    return raw


def _campaign_authority(
    campaign_directory: Path, parity_record: Path
) -> tuple[ValidatePaperCompatibility, tuple[EngineArtifactBinding, ...], str]:
    _private_directory(campaign_directory, mode=0o500, label="campaign directory")
    manifest_raw = _sealed_bytes(
        campaign_directory / _CAMPAIGN_MANIFEST, label="campaign manifest"
    )
    manifest = _canonical_object(manifest_raw[:-1], label="campaign manifest")
    if set(manifest) != _CAMPAIGN_FIELDS or manifest.get("schema_version") != "nautilus-phase4-campaign-v1":
        raise PaperCompatibilityVerificationError("campaign manifest is invalid")
    campaign_sha256 = hashlib.sha256(manifest_raw).hexdigest()
    parity_raw = _sealed_bytes(parity_record, label="parity record")
    parity = _canonical_object(parity_raw[:-1], label="parity record")
    if (
        parity.get("scenario_campaign_sha256") != campaign_sha256
        or any(parity.get(field) != manifest.get(field) for field in _ARTIFACT_FIELDS)
        or parity.get("strategy_source_sha256")
        != manifest.get("strategy_source_sha256")
    ):
        raise PaperCompatibilityVerificationError(
            "parity record is not bound to the exact campaign"
        )
    references: list[ArtifactReference] = []
    bindings: list[EngineArtifactBinding] = []
    for number, (filename, field) in enumerate(
        zip(_ARTIFACT_FILES, _ARTIFACT_FIELDS, strict=True), start=1
    ):
        path = campaign_directory / filename
        raw = _sealed_bytes(path, label=filename)
        digest = hashlib.sha256(raw).hexdigest()
        if manifest.get(field) != digest:
            raise PaperCompatibilityVerificationError(
                "campaign artifact digest does not match its manifest"
            )
        reference = ArtifactReference(
            artifact_id=UUID(
                f"{number}{number}{number}{number}{number}{number}{number}{number}"
                "-1111-4111-8111-111111111111"
            ),
            sha256=digest,
            media_type="application/json",
        )
        references.append(reference)
        bindings.append(EngineArtifactBinding(reference=reference, source=path))
    command = ValidatePaperCompatibility(
        command_type="ValidatePaperCompatibility",
        engine_configuration=references[0],
        instrument_catalog=references[1],
        strategy_configuration=references[2],
        strategy_source_sha256=manifest["strategy_source_sha256"],
        scenario_campaign_sha256=campaign_sha256,
    )
    return command, tuple(bindings), hashlib.sha256(parity_raw).hexdigest()


def _cleanup_provider_transport(transport_root: Path, command: ValidatePaperCompatibility) -> None:
    request_sha256 = hashlib.sha256(canonical_json_bytes(command)).hexdigest()
    run_directory = transport_root / f"paper-{request_sha256[:32]}"
    for name in ("request.json", "request.sha256"):
        path = run_directory / name
        try:
            path.unlink()
        except OSError as exc:
            raise PaperCompatibilityVerificationError(
                "paper transport cannot be cleaned"
            ) from exc
    try:
        run_directory.rmdir()
    except OSError as exc:
        raise PaperCompatibilityVerificationError(
            "paper transport cannot be cleaned"
        ) from exc


def verify_paper_compatibility(
    *,
    candidate_closure: Path,
    artifact_directory: Path,
    sandbox: Path,
    campaign_directory: Path,
    parity_record: Path,
    transport_root: Path,
) -> Path:
    _private_directory(transport_root, mode=0o700, label="paper transport root")
    if next(transport_root.iterdir(), None) is not None:
        raise PaperCompatibilityVerificationError("paper transport root must be empty")
    command, bindings, parity_digest = _campaign_authority(
        campaign_directory, parity_record
    )
    config = NautilusClosureConfig(
        runtime_root=candidate_closure,
        artifact_directory=artifact_directory,
        sandbox_executable=sandbox,
    )

    def attest():
        return attest_nautilus_backtest_closure(
            config, expected_profile="paper-compatibility"
        )

    closure = attest()
    manifest_path = candidate_closure / "closure-manifest.json"
    result = capture_paper_compatibility(
        provider=EngineSpawnProvider(
            transport_root=transport_root,
            attest_closure=attest,
            attest_inputs=HashBoundArtifactResolver(bindings),
            expected_manifest_schema_version=6,
            monotonic_ns=time.monotonic_ns,
        ),
        command=command,
        candidate_closure_sha256=closure.closure_sha256,
        candidate_manifest_sha256=hashlib.sha256(
            _sealed_bytes(manifest_path, label="candidate closure manifest")
        ).hexdigest(),
        parity_record_sha256=parity_digest,
    )
    _cleanup_provider_transport(transport_root, command)
    return publish_paper_compatibility_result(transport_root, result)


def main() -> int:
    arguments = _parser().parse_args()
    try:
        result = verify_paper_compatibility(
            candidate_closure=arguments.candidate_closure,
            artifact_directory=arguments.artifact_directory,
            sandbox=arguments.sandbox,
            campaign_directory=arguments.campaign_directory,
            parity_record=arguments.parity_record,
            transport_root=arguments.transport_root,
        )
    except PaperCompatibilityVerificationError as exc:
        print(f"paper compatibility failed: {exc}", file=sys.stderr)
        return 1
    print(f"paper compatibility result sealed: {result.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
