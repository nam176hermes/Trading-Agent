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
    SCENARIO_IDS,
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
from services.job_worker.engine_spawn_interface import EngineSpawnError
from services.job_worker.nautilus_closure import (
    NautilusClosureConfig,
    attest_nautilus_backtest_closure,
)


_CHECKOUT = Path(__file__).resolve().parents[1]
_RESULT_NAME = "paper-compatibility-result.json"
_CAMPAIGN_MANIFEST = "campaign-manifest.json"
_CAMPAIGN_ARTIFACTS = (
    ("engine-configuration.json", "engine_configuration_sha256"),
    ("instrument-catalog.json", "instrument_catalog_sha256"),
    ("strategy-configuration.json", "strategy_configuration_sha256"),
    ("market-data.json", "market_data_sha256"),
    ("simulation-scenario.json", "simulation_scenario_sha256"),
)
_PAPER_ARTIFACTS = _CAMPAIGN_ARTIFACTS[:3]
_CAMPAIGN_IDENTITY_FIELDS = {
    "scenario_id",
    *(field for _name, field in _CAMPAIGN_ARTIFACTS),
}
_CAMPAIGN_FIELDS = {
    "paper_scenario_id",
    "scenarios",
    "schema_version",
    "strategy_source_sha256",
}
_PARITY_FIELDS = {
    "candidate_closure_sha256",
    "candidate_manifest_schema_version",
    "candidate_manifest_sha256",
    "scenario_campaign_sha256",
    "scenarios",
    "schema_version",
    "status",
    "strategy_source_sha256",
}
_PARITY_RESULT_FIELDS = {
    "independent_reference_event_sha256",
    "independent_reference_result_sha256",
    "nautilus_event_sha256",
    "nautilus_result_sha256",
    "run_1_event_sha256",
    "run_2_event_sha256",
}
_PARITY_SCENARIO_FIELDS = _CAMPAIGN_IDENTITY_FIELDS | _PARITY_RESULT_FIELDS
_EVENT_FIELDS = {
    "compatible",
    "engine_configuration_sha256",
    "event_type",
    "instrument_catalog_sha256",
    "scenario_campaign_sha256",
    "strategy_configuration_sha256",
    "strategy_source_sha256",
}
_MAX_SEALED_BYTES = 8 * 1024 * 1024


class PaperCompatibilityVerificationError(RuntimeError):
    """The finite compatibility proof or its publication is unsafe."""


_EXPECTED_FAILURES = (
    EngineSpawnError,
    OSError,
    subprocess.SubprocessError,
    TypeError,
    ValueError,
)
_ALL_EXPECTED_FAILURES = (PaperCompatibilityVerificationError, *_EXPECTED_FAILURES)


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
    try:
        canonical = canonical_json_bytes(value)
    except ValueError as exc:
        raise PaperCompatibilityVerificationError(
            f"{label} contains a non-canonical value"
        ) from exc
    if not isinstance(value, dict) or canonical != raw:
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
    cleanup: Callable[[], None] = lambda: None,
) -> PaperCompatibilityResultV1:
    """Own exactly one prepare, consume, and bounded captured-process call."""

    try:
        prepared = provider.prepare(command)
    except _EXPECTED_FAILURES as exc:
        raise PaperCompatibilityVerificationError(
            f"paper compatibility prepare failed: {exc}"
        ) from exc
    primary: PaperCompatibilityVerificationError | None = None
    try:
        built = consume(prepared)
        completed = capture(built, popen_factory=popen_factory)
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
    except PaperCompatibilityVerificationError as exc:
        primary = exc
        raise
    except _EXPECTED_FAILURES as exc:
        primary = PaperCompatibilityVerificationError(
            f"paper compatibility process failed: {exc}"
        )
        raise primary from exc
    finally:
        try:
            cleanup()
        except _ALL_EXPECTED_FAILURES as cleanup_error:
            if primary is not None:
                primary.add_note(
                    "paper compatibility transport cleanup failed: "
                    f"{type(cleanup_error).__name__}"
                )
            else:
                raise PaperCompatibilityVerificationError(
                    "paper compatibility transport cleanup failed"
                ) from cleanup_error


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
    created = False
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
        created = True
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
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError as close_error:
                exc.add_note(
                    "paper result descriptor cleanup failed: "
                    f"{type(close_error).__name__}"
                )
            descriptor = -1
        if created and parent_fd >= 0:
            try:
                os.unlink(path.name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            except OSError as cleanup_error:
                exc.add_note(
                    "partial paper result cleanup failed: "
                    f"{type(cleanup_error).__name__}"
                )
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
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != 0o400
            or opened.st_size <= 0
            or opened.st_size > _MAX_SEALED_BYTES
        ):
            raise PaperCompatibilityVerificationError(f"{label} is not sealed")
        remaining = opened.st_size
        chunks: list[bytes] = []
        while remaining:
            block = os.read(descriptor, min(65_536, remaining))
            if not block:
                raise PaperCompatibilityVerificationError(
                    f"{label} read was incomplete"
                )
            chunks.append(block)
            remaining -= len(block)
        if os.read(descriptor, 1) != b"":
            raise PaperCompatibilityVerificationError(
                f"{label} changed while being read"
            )
        named = path.stat(follow_symlinks=False)
        identity = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_uid",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
        if any(getattr(named, field) != getattr(opened, field) for field in identity):
            raise PaperCompatibilityVerificationError(
                f"{label} identity changed while being read"
            )
        return b"".join(chunks)
    except PaperCompatibilityVerificationError:
        raise
    except OSError as exc:
        raise PaperCompatibilityVerificationError(f"{label} is unavailable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _canonical_line_object(raw: bytes, *, label: str) -> dict[str, object]:
    if not raw.endswith(b"\n") or raw.count(b"\n") != 1:
        raise PaperCompatibilityVerificationError(
            f"{label} must contain one canonical JSON line"
        )
    return _canonical_object(raw[:-1], label=label)


def _sha256(value: object, *, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise PaperCompatibilityVerificationError(f"{label} is not SHA-256")
    return value


def _scenario_records(
    value: object,
    *,
    fields: set[str],
    label: str,
) -> tuple[dict[str, object], ...]:
    if not isinstance(value, list) or len(value) != len(SCENARIO_IDS):
        raise PaperCompatibilityVerificationError(
            f"{label} is not the exact eight-scenario campaign"
        )
    records: list[dict[str, object]] = []
    for expected_id, record in zip(SCENARIO_IDS, value, strict=True):
        if (
            not isinstance(record, dict)
            or set(record) != fields
            or record.get("scenario_id") != expected_id
        ):
            raise PaperCompatibilityVerificationError(
                f"{label} is missing, duplicated, or out of order"
            )
        for field in fields - {"scenario_id"}:
            _sha256(record[field], label=f"{label} {field}")
        records.append(record)
    return tuple(records)


def _campaign_authority(
    campaign_directory: Path,
    parity_record: Path,
    *,
    candidate_closure_sha256: str,
    candidate_manifest_sha256: str,
) -> tuple[ValidatePaperCompatibility, tuple[EngineArtifactBinding, ...], str]:
    _sha256(candidate_closure_sha256, label="paper candidate closure")
    _sha256(candidate_manifest_sha256, label="paper candidate manifest")
    _private_directory(campaign_directory, mode=0o500, label="campaign directory")
    manifest_raw = _sealed_bytes(
        campaign_directory / _CAMPAIGN_MANIFEST, label="campaign manifest"
    )
    manifest = _canonical_line_object(manifest_raw, label="campaign manifest")
    if (
        set(manifest) != _CAMPAIGN_FIELDS
        or manifest.get("schema_version") != "nautilus-phase4-campaign-v1"
        or manifest.get("paper_scenario_id") != "long-accounting"
    ):
        raise PaperCompatibilityVerificationError("campaign manifest is invalid")
    strategy_source_sha256 = _sha256(
        manifest["strategy_source_sha256"], label="campaign strategy source"
    )
    campaign_records = _scenario_records(
        manifest["scenarios"],
        fields=_CAMPAIGN_IDENTITY_FIELDS,
        label="campaign scenarios",
    )
    campaign_sha256 = hashlib.sha256(manifest_raw).hexdigest()
    references: list[ArtifactReference] = []
    bindings: list[EngineArtifactBinding] = []
    for record in campaign_records:
        scenario_id = str(record["scenario_id"])
        scenario_directory = campaign_directory / scenario_id
        _private_directory(
            scenario_directory,
            mode=0o500,
            label=f"campaign scenario {scenario_id}",
        )
        for filename, field in _CAMPAIGN_ARTIFACTS:
            path = scenario_directory / filename
            raw = _sealed_bytes(path, label=f"{scenario_id} {filename}")
            digest = hashlib.sha256(raw).hexdigest()
            if record[field] != digest:
                raise PaperCompatibilityVerificationError(
                    "campaign artifact digest does not match its manifest"
                )
            if (
                scenario_id == "long-accounting"
                and (filename, field) in _PAPER_ARTIFACTS
            ):
                number = len(references) + 1
                reference = ArtifactReference(
                    artifact_id=UUID(
                        f"{number}{number}{number}{number}{number}{number}{number}{number}"
                        "-1111-4111-8111-111111111111"
                    ),
                    sha256=digest,
                    media_type="application/json",
                )
                references.append(reference)
                bindings.append(
                    EngineArtifactBinding(reference=reference, source=path)
                )
    if len(references) != 3:
        raise PaperCompatibilityVerificationError(
            "paper scenario artifacts are incomplete"
        )

    parity_raw = _sealed_bytes(parity_record, label="parity record")
    parity = _canonical_line_object(parity_raw, label="parity record")
    if (
        set(parity) != _PARITY_FIELDS
        or parity.get("schema_version") != "nautilus-phase4-parity-evidence-v2"
        or parity.get("status") != "passed"
        or type(parity.get("candidate_manifest_schema_version")) is not int
        or parity.get("candidate_manifest_schema_version") != 6
        or parity.get("scenario_campaign_sha256") != campaign_sha256
        or parity.get("strategy_source_sha256") != strategy_source_sha256
    ):
        raise PaperCompatibilityVerificationError(
            "parity record is not bound to the exact candidate campaign"
        )
    _sha256(
        parity.get("candidate_closure_sha256"),
        label="simulation candidate closure",
    )
    _sha256(
        parity.get("candidate_manifest_sha256"),
        label="simulation candidate manifest",
    )
    parity_records = _scenario_records(
        parity["scenarios"],
        fields=_PARITY_SCENARIO_FIELDS,
        label="parity scenarios",
    )
    for campaign_record, parity_scenario in zip(
        campaign_records, parity_records, strict=True
    ):
        if any(
            parity_scenario[field] != campaign_record[field]
            for field in _CAMPAIGN_IDENTITY_FIELDS
        ):
            raise PaperCompatibilityVerificationError(
                "parity scenario identity differs from the campaign"
            )
        event_digests = {
            parity_scenario[field]
            for field in (
                "independent_reference_event_sha256",
                "nautilus_event_sha256",
                "run_1_event_sha256",
                "run_2_event_sha256",
            )
        }
        if len(event_digests) != 1:
            raise PaperCompatibilityVerificationError(
                "parity scenario event digests differ"
            )
        if (
            parity_scenario["independent_reference_result_sha256"]
            != parity_scenario["nautilus_result_sha256"]
        ):
            raise PaperCompatibilityVerificationError(
                "parity scenario result digests differ"
            )
    command = ValidatePaperCompatibility(
        command_type="ValidatePaperCompatibility",
        engine_configuration=references[0],
        instrument_catalog=references[1],
        strategy_configuration=references[2],
        strategy_source_sha256=strategy_source_sha256,
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
    try:
        _private_directory(transport_root, mode=0o700, label="paper transport root")
        if next(transport_root.iterdir(), None) is not None:
            raise PaperCompatibilityVerificationError(
                "paper transport root must be empty"
            )
        config = NautilusClosureConfig(
            runtime_root=candidate_closure,
            artifact_directory=artifact_directory,
            sandbox_executable=sandbox,
        )
        closure = attest_nautilus_backtest_closure(
            config, expected_profile="paper-compatibility"
        )
        manifest_path = candidate_closure / "closure-manifest.json"
        candidate_manifest_sha256 = hashlib.sha256(
            _sealed_bytes(manifest_path, label="candidate closure manifest")
        ).hexdigest()
        command, bindings, parity_digest = _campaign_authority(
            campaign_directory,
            parity_record,
            candidate_closure_sha256=closure.closure_sha256,
            candidate_manifest_sha256=candidate_manifest_sha256,
        )
        result = capture_paper_compatibility(
            provider=EngineSpawnProvider(
                transport_root=transport_root,
                attest_closure=lambda: closure,
                attest_inputs=HashBoundArtifactResolver(bindings),
                expected_manifest_schema_version=6,
                monotonic_ns=time.monotonic_ns,
            ),
            command=command,
            candidate_closure_sha256=closure.closure_sha256,
            candidate_manifest_sha256=candidate_manifest_sha256,
            parity_record_sha256=parity_digest,
            cleanup=lambda: _cleanup_provider_transport(transport_root, command),
        )
        return publish_paper_compatibility_result(transport_root, result)
    except PaperCompatibilityVerificationError:
        raise
    except _EXPECTED_FAILURES as exc:
        raise PaperCompatibilityVerificationError(
            f"paper compatibility verification failed: {exc}"
        ) from exc


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
    except _ALL_EXPECTED_FAILURES as exc:
        print(f"paper compatibility failed: {exc}", file=sys.stderr)
        return 1
    print(f"paper compatibility result sealed: {result.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
