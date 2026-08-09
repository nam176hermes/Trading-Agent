from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid5

import pytest

import packages.research_validation as research
from packages.research_validation import producers
from packages.engine_contracts import (
    EngineEvent,
    EngineEventEnvelope,
    EventAttribute,
    EventFamily,
    canonical_json_bytes,
    payload_digest,
)
from packages.nautilus_backtest import (
    SCENARIO_IDS,
    BacktestScenarioV1,
    build_canonical_simulation_fixture,
    build_simulation_envelope,
    calculate_reference_outcome,
)
from scripts import verify_nautilus_paper_compatibility as paper_verifier
from scripts import verify_nautilus_v12_r3_parity as parity_verifier
from scripts import close_phase4_research_evidence as research_closer
from services.job_worker.engine_artifacts import HashBoundArtifactResolver
from tests.nautilus_backtest.test_runtime_parity_verifier import (
    _Harness as ParityHarness,
    _normalized_decimal_text,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MATERIALIZER = REPOSITORY_ROOT / "scripts" / "materialize_phase4_campaign_inputs.py"
RESEARCH_CLOSER = REPOSITORY_ROOT / "scripts" / "close_phase4_research_evidence.py"
LEGACY_ROOT = REPOSITORY_ROOT / "legacy" / "research-backend"
LEGACY_ADAPTER_COMMAND = (
    "legacy/research-backend/.venv/bin/python",
    "legacy/research-backend/nautilus_parity_adapter.py",
)
LEGACY_UV_PATH = Path("/home/thenam176/.local/bin/uv")
LEGACY_UV_SHA256 = (
    "cd952ca51e2c730e848a45c4e0dfb58926d79d90550b6a5feb5543b43d3248b4"
)
LEGACY_UV_VERSION = "uv 0.11.7 (x86_64-unknown-linux-gnu)"
LEGACY_ENV_COMMAND = (
    "/usr/bin/env",
    "-i",
    "PATH=/usr/bin:/bin",
    "PYTHONDONTWRITEBYTECODE=1",
    "PYTHONHASHSEED=0",
    "PYTHONNOUSERSITE=1",
    "UV_OFFLINE=1",
)
LEGACY_SYNC_ARGUMENTS = ("sync", "--frozen", "--extra", "test")
ARTIFACT_NAMES = (
    "engine-configuration.json",
    "instrument-catalog.json",
    "strategy-configuration.json",
    "market-data.json",
    "simulation-scenario.json",
)
DIGEST_FIELDS = (
    "engine_configuration_sha256",
    "instrument_catalog_sha256",
    "strategy_configuration_sha256",
    "market_data_sha256",
    "simulation_scenario_sha256",
)


@dataclass(frozen=True, slots=True)
class _RetainedUvAuthority:
    descriptor: int
    path: Path
    identity: tuple[int, ...]

    @property
    def exec_path(self) -> str:
        return f"/proc/self/fd/{self.descriptor}"


def _uv_identity(observed: os.stat_result) -> tuple[int, ...]:
    return (
        observed.st_dev,
        observed.st_ino,
        observed.st_mode,
        observed.st_uid,
        observed.st_gid,
        observed.st_size,
        observed.st_mtime_ns,
        observed.st_ctime_ns,
    )


def _digest_retained_descriptor(descriptor: int) -> str:
    os.lseek(descriptor, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
    os.lseek(descriptor, 0, os.SEEK_SET)
    return digest.hexdigest()


def _open_retained_uv_authority(
    path: Path,
    *,
    expected_sha256: str,
) -> _RetainedUvAuthority:
    resolved = path.resolve(strict=True)
    named_before = path.lstat()
    if (
        resolved != path
        or not stat.S_ISREG(named_before.st_mode)
        or stat.S_IMODE(named_before.st_mode) != 0o755
        or named_before.st_uid != os.geteuid()
        or named_before.st_gid != os.getegid()
    ):
        raise AssertionError("legacy uv authority is unsafe")
    descriptor = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        opened_before = os.fstat(descriptor)
        digest = _digest_retained_descriptor(descriptor)
        opened_after = os.fstat(descriptor)
        named_after = path.lstat()
        if (
            _uv_identity(named_before) != _uv_identity(opened_before)
            or _uv_identity(opened_before) != _uv_identity(opened_after)
            or _uv_identity(opened_after) != _uv_identity(named_after)
            or digest != expected_sha256
        ):
            raise AssertionError("legacy uv authority identity changed")
        return _RetainedUvAuthority(
            descriptor=descriptor,
            path=path,
            identity=_uv_identity(opened_after),
        )
    except BaseException:
        os.close(descriptor)
        raise


def _run_retained_uv(
    authority: _RetainedUvAuthority,
    arguments: tuple[str, ...],
    *,
    clean_env: bool,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[bytes]:
    command = (
        (*LEGACY_ENV_COMMAND, authority.exec_path, *arguments)
        if clean_env
        else (authority.exec_path, *arguments)
    )
    return subprocess.run(
        command,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        pass_fds=(authority.descriptor,),
    )


def _recheck_retained_uv_authority(
    authority: _RetainedUvAuthority,
    path: Path,
    *,
    expected_sha256: str,
) -> None:
    opened_before = os.fstat(authority.descriptor)
    digest = _digest_retained_descriptor(authority.descriptor)
    opened_after = os.fstat(authority.descriptor)
    named = path.lstat()
    if (
        _uv_identity(opened_before) != authority.identity
        or _uv_identity(opened_after) != authority.identity
        or _uv_identity(named) != authority.identity
        or digest != expected_sha256
    ):
        raise AssertionError("legacy uv authority identity changed")


@pytest.fixture
def private_root() -> Path:
    root = Path(tempfile.mkdtemp(prefix="phase4-campaign-test-", dir="/tmp"))
    root.chmod(0o700)
    try:
        yield root
    finally:
        for directory, _children, files in os.walk(root):
            Path(directory).chmod(0o700)
            for filename in files:
                path = Path(directory) / filename
                if not path.is_symlink():
                    path.chmod(0o600)
        shutil.rmtree(root)


def _run_materializer(destination: Path) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, str(MATERIALIZER), "--destination", str(destination)],
        cwd=REPOSITORY_ROOT,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def test_materializer_writes_one_no_clobber_exact_eight_scenario_campaign(
    private_root: Path,
) -> None:
    """A producer that omits, aliases, or rewrites a scenario breaks authority."""
    destination = private_root / "campaign"

    first = _run_materializer(destination)
    second = _run_materializer(destination)

    assert first.returncode == 0, first.stderr.decode("utf-8", errors="replace")
    assert first.stdout == b""
    assert second.returncode == 1
    assert stat.S_IMODE(destination.stat().st_mode) == 0o500
    assert sorted(path.name for path in destination.iterdir()) == sorted(
        ("campaign-manifest.json", *SCENARIO_IDS)
    )
    manifest_raw = (destination / "campaign-manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    assert manifest_raw == canonical_json_bytes(manifest) + b"\n"
    assert manifest["schema_version"] == "nautilus-phase4-campaign-v1"
    assert manifest["paper_scenario_id"] == "long-accounting"
    assert [item["scenario_id"] for item in manifest["scenarios"]] == list(
        SCENARIO_IDS
    )
    for scenario_id, record in zip(SCENARIO_IDS, manifest["scenarios"], strict=True):
        scenario = destination / scenario_id
        assert stat.S_IMODE(scenario.stat().st_mode) == 0o500
        assert tuple(sorted(path.name for path in scenario.iterdir())) == tuple(
            sorted(ARTIFACT_NAMES)
        )
        fixture = build_canonical_simulation_fixture(scenario_id)
        for filename, field, expected in zip(
            ARTIFACT_NAMES, DIGEST_FIELDS, fixture.artifacts, strict=True
        ):
            artifact = scenario / filename
            assert stat.S_IMODE(artifact.stat().st_mode) == 0o400
            assert artifact.read_bytes() == expected
            assert record[field] == hashlib.sha256(expected).hexdigest()


def test_materializer_retains_sealed_partial_destination_after_nested_collision(
    private_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = private_root / "campaign"

    def collide(_path: Path, _value: bytes, **_kwargs: object) -> None:
        raise FileExistsError("simulated nested collision")

    monkeypatch.setattr(producers, "_write_sealed_file", collide)

    with pytest.raises(ValueError, match="already exists"):
        research.materialize_phase4_campaign(destination)

    assert destination.exists()
    assert stat.S_IMODE(destination.stat().st_mode) == 0o500
    retained = destination / "long-accounting"
    assert retained.is_dir()
    assert stat.S_IMODE(retained.stat().st_mode) == 0o500
    assert list(retained.iterdir()) == []


def test_materializer_failure_never_calls_production_unlink_or_rmdir(
    private_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = private_root / "campaign"
    deletion_calls: list[tuple[str, object]] = []

    def collide(_path: Path, _value: bytes, **_kwargs: object) -> None:
        raise FileExistsError("simulated nested collision")

    monkeypatch.setattr(producers, "_write_sealed_file", collide)
    monkeypatch.setattr(
        producers.os,
        "unlink",
        lambda path, **_kwargs: deletion_calls.append(("unlink", path)),
    )
    monkeypatch.setattr(
        producers.os,
        "rmdir",
        lambda path, **_kwargs: deletion_calls.append(("rmdir", path)),
    )

    with pytest.raises(ValueError, match="already exists"):
        research.materialize_phase4_campaign(destination)

    assert deletion_calls == []
    assert destination.exists()


@pytest.mark.parametrize(
    ("failure_name", "expected_directories"),
    (
        ("campaign", {"campaign"}),
        ("long-accounting", {"campaign", "long-accounting"}),
    ),
)
def test_materializer_open_gap_retains_only_mode_0500_directories_without_delete(
    private_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_name: str,
    expected_directories: set[str],
) -> None:
    destination = private_root / "campaign"
    deletion_calls: list[tuple[str, object]] = []
    real_open = producers.os.open

    def fail_selected_directory_open(path, flags, *args, **kwargs):
        if os.fspath(path) == failure_name and kwargs.get("dir_fd") is not None:
            raise OSError(f"inert {failure_name} open gap")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(producers.os, "open", fail_selected_directory_open)
    monkeypatch.setattr(
        producers.os,
        "unlink",
        lambda path, **_kwargs: deletion_calls.append(("unlink", path)),
    )
    monkeypatch.setattr(
        producers.os,
        "rmdir",
        lambda path, **_kwargs: deletion_calls.append(("rmdir", path)),
    )

    with pytest.raises(OSError, match=f"inert {failure_name} open gap"):
        research.materialize_phase4_campaign(destination)

    retained = {destination.name: destination}
    retained.update(
        {
            path.name: path
            for path in destination.rglob("*")
            if path.is_dir() and not path.is_symlink()
        }
    )
    assert set(retained) == expected_directories
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o500 for path in retained.values())
    assert deletion_calls == []


def test_materializer_closes_scenario_descriptor_when_initial_identity_read_fails(
    private_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = private_root / "campaign"
    real_open = producers.os.open
    real_fstat = producers.os.fstat
    real_close = producers.os.close
    scenario_descriptor = -1
    injected = False
    production_close_calls = 0

    def tracked_open(path, flags, *args, **kwargs):
        nonlocal scenario_descriptor
        descriptor = real_open(path, flags, *args, **kwargs)
        if os.fspath(path) == "long-accounting" and kwargs.get("dir_fd") is not None:
            scenario_descriptor = descriptor
        return descriptor

    def fail_initial_scenario_fstat(descriptor: int):
        nonlocal injected
        if descriptor == scenario_descriptor and not injected:
            injected = True
            raise OSError("inert scenario identity failure")
        return real_fstat(descriptor)

    def tracked_close(descriptor: int) -> None:
        nonlocal production_close_calls
        if descriptor == scenario_descriptor:
            production_close_calls += 1
        real_close(descriptor)

    monkeypatch.setattr(producers.os, "open", tracked_open)
    monkeypatch.setattr(producers.os, "fstat", fail_initial_scenario_fstat)
    monkeypatch.setattr(producers.os, "close", tracked_close)

    try:
        with pytest.raises(OSError, match="scenario identity failure"):
            research.materialize_phase4_campaign(destination)
    finally:
        if scenario_descriptor >= 0 and production_close_calls == 0:
            real_close(scenario_descriptor)

    assert injected is True
    assert production_close_calls == 1
    assert stat.S_IMODE(destination.stat().st_mode) == 0o500
    assert stat.S_IMODE((destination / "long-accounting").stat().st_mode) == 0o500


def test_verified_campaign_rejects_a_self_consistent_noncanonical_artifact(
    private_root: Path,
) -> None:
    """A manifest digest alone must not turn caller-chosen bytes into fixtures."""
    destination = private_root / "campaign"
    assert _run_materializer(destination).returncode == 0
    manifest_path = destination / "campaign-manifest.json"
    artifact = destination / "long-accounting" / "market-data.json"
    destination.chmod(0o700)
    (destination / "long-accounting").chmod(0o700)
    artifact.chmod(0o600)
    artifact.write_bytes(b'{"close":"999"}\n')
    artifact.chmod(0o400)
    manifest_path.chmod(0o600)
    manifest = json.loads(manifest_path.read_bytes())
    manifest["scenarios"][0]["market_data_sha256"] = hashlib.sha256(
        artifact.read_bytes()
    ).hexdigest()
    manifest_path.write_bytes(canonical_json_bytes(manifest) + b"\n")
    manifest_path.chmod(0o400)
    (destination / "long-accounting").chmod(0o500)
    destination.chmod(0o500)

    with pytest.raises(ValueError, match="canonical campaign fixture"):
        research.load_verified_campaign(destination)


def test_verified_campaign_exposes_only_fixed_hash_bound_five_artifact_members(
    private_root: Path,
) -> None:
    destination = private_root / "campaign"
    assert _run_materializer(destination).returncode == 0

    campaign = research.load_verified_campaign(destination)

    assert campaign.sha256 == hashlib.sha256(
        (destination / "campaign-manifest.json").read_bytes()
    ).hexdigest()
    assert tuple(item.scenario_id for item in campaign.scenarios) == SCENARIO_IDS
    assert all(len(item.bindings) == 5 for item in campaign.scenarios)
    assert tuple(binding.source.name for binding in campaign.scenarios[0].bindings) == ARTIFACT_NAMES


def test_campaign_loader_rejects_root_substitution_during_snapshot(
    private_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign = private_root / "campaign"
    replacement = private_root / "replacement"
    displaced = private_root / "displaced"
    research.materialize_phase4_campaign(campaign)
    research.materialize_phase4_campaign(replacement)
    real_open = os.open
    swapped = False

    def swap_root(path: object, *args: object, **kwargs: object) -> int:
        nonlocal swapped
        if not swapped and os.fspath(path).endswith("engine-configuration.json"):
            campaign.rename(displaced)
            replacement.rename(campaign)
            swapped = True
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(producers.os, "open", swap_root)

    with pytest.raises(ValueError, match="identity|changed"):
        research.load_verified_campaign(campaign)

    assert swapped is True


def test_campaign_loader_rejects_scenario_substitution_during_snapshot(
    private_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign = private_root / "campaign"
    replacement_campaign = private_root / "replacement"
    displaced = campaign / "displaced-long-accounting"
    research.materialize_phase4_campaign(campaign)
    research.materialize_phase4_campaign(replacement_campaign)
    real_open = os.open
    swapped = False

    def swap_scenario(path: object, *args: object, **kwargs: object) -> int:
        nonlocal swapped
        if not swapped and os.fspath(path).endswith("engine-configuration.json"):
            swapped = True
            campaign.chmod(0o700)
            staged = campaign / "staged-long-accounting"
            shutil.copytree(replacement_campaign / "long-accounting", staged)
            for artifact in staged.iterdir():
                artifact.chmod(0o400)
            staged.chmod(0o500)
            (campaign / "long-accounting").rename(displaced)
            staged.rename(campaign / "long-accounting")
            campaign.chmod(0o500)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(producers.os, "open", swap_scenario)

    with pytest.raises(ValueError, match="identity|changed"):
        research.load_verified_campaign(campaign)

    assert swapped is True


def test_campaign_loader_rejects_manifest_mutation_during_snapshot(
    private_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign = private_root / "campaign"
    research.materialize_phase4_campaign(campaign)
    manifest = campaign / "campaign-manifest.json"
    original = manifest.read_bytes()
    real_open = os.open
    mutated = False

    def mutate_manifest(path: object, *args: object, **kwargs: object) -> int:
        nonlocal mutated
        if not mutated and os.fspath(path).endswith("engine-configuration.json"):
            manifest.chmod(0o600)
            manifest.write_bytes(original)
            manifest.chmod(0o400)
            mutated = True
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(producers.os, "open", mutate_manifest)

    with pytest.raises(ValueError, match="manifest.*identity|identity.*changed"):
        research.load_verified_campaign(campaign)

    assert mutated is True


def test_materializer_failure_cleanup_preserves_a_replacement_campaign(
    private_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = private_root / "campaign"
    replacement = private_root / "replacement"
    displaced = private_root / "displaced-partial"
    research.materialize_phase4_campaign(replacement)
    replacement_identity = (replacement.stat().st_dev, replacement.stat().st_ino)
    real_write = os.write
    swapped = False

    def replace_then_fail(descriptor: int, value: object) -> int:
        nonlocal swapped
        if not swapped:
            destination.rename(displaced)
            replacement.rename(destination)
            swapped = True
            raise OSError("simulated campaign publication failure")
        return real_write(descriptor, value)

    monkeypatch.setattr(producers.os, "write", replace_then_fail)

    with pytest.raises(OSError, match="simulated campaign"):
        research.materialize_phase4_campaign(destination)

    assert swapped is True
    assert (destination.stat().st_dev, destination.stat().st_ino) == (
        replacement_identity
    )


def _seal(path: Path, value: bytes) -> None:
    path.write_bytes(value)
    path.chmod(0o400)


def _reference_digests(scenario_id: str) -> tuple[str, str]:
    fixture = build_canonical_simulation_fixture(scenario_id)
    envelope = build_simulation_envelope(fixture)
    scenario = BacktestScenarioV1.from_mounted_artifacts(
        scenario_bytes=fixture.simulation_scenario,
        catalog_bytes=fixture.instrument_catalog,
        strategy_bytes=fixture.strategy_configuration,
        market_data_bytes=fixture.market_data,
        start_time=envelope.payload.start_time,
        end_time=envelope.payload.end_time,
    )
    expected = calculate_reference_outcome(scenario)
    input_sha256 = hashlib.sha256(
        canonical_json_bytes(
            {
                name: getattr(envelope.payload, name).sha256
                for name in (
                    "engine_configuration",
                    "instrument_catalog",
                    "strategy_configuration",
                    "market_data",
                    "simulation_scenario",
                )
            }
        )
    ).hexdigest()
    attributes: tuple[tuple[str, str | int], ...] = (
        ("input_artifacts_sha256", input_sha256),
        ("scenario_digest", expected.scenario_digest),
        ("scenario_id", expected.scenario_id),
        ("event_digest", expected.event_digest),
        ("iterations", expected.iterations),
        ("total_events", expected.total_events),
        ("total_orders", expected.total_orders),
        ("total_fills", expected.total_fills),
        ("total_positions", expected.total_positions),
        ("filled_quantity", _normalized_decimal_text(expected.filled_quantity)),
        ("remaining_quantity", _normalized_decimal_text(expected.remaining_quantity)),
        ("position_quantity", _normalized_decimal_text(expected.position_quantity)),
        ("average_entry_price", _normalized_decimal_text(expected.average_entry_price)),
        ("fees", _normalized_decimal_text(expected.fees)),
        ("realized_pnl", _normalized_decimal_text(expected.realized_pnl)),
        ("unrealized_pnl", _normalized_decimal_text(expected.unrealized_pnl)),
        ("stop_take_profit_precedence", expected.stop_take_profit_precedence),
    )
    payload = EngineEvent(
        event_type="NautilusBacktestSimulationCompleted",
        family=EventFamily.ENGINE_LIFECYCLE,
        attributes=tuple(
            EventAttribute(name=name, value=value) for name, value in attributes
        ),
    )
    event = EngineEventEnvelope(
        message_id=uuid5(
            envelope.message_id,
            "NautilusBacktestSimulationCompleted",
        ),
        correlation_id=envelope.correlation_id,
        causation_id=envelope.message_id,
        engine_run_id=envelope.engine_run_id,
        stream_sequence=envelope.stream_sequence + 1,
        event_time=envelope.event_time,
        initialization_time=envelope.initialization_time,
        schema_version=envelope.schema_version,
        producer_identity=envelope.producer_identity,
        source_commit=envelope.source_commit,
        config_digest=envelope.config_digest,
        payload_digest=payload_digest(payload),
        payload=payload,
    )
    event_sha256 = hashlib.sha256(canonical_json_bytes(event)).hexdigest()
    result_sha256 = hashlib.sha256(
        canonical_json_bytes(
            {
                "event_sha256": event_sha256,
                "input_artifacts_sha256": input_sha256,
                "request_sha256": hashlib.sha256(
                    canonical_json_bytes(envelope)
                ).hexdigest(),
            }
        )
    ).hexdigest()
    return event_sha256, result_sha256


def _research_authorities(private_root: Path) -> tuple[Path, Path, Path, Path]:
    campaign = private_root / "campaign"
    assert _run_materializer(campaign).returncode == 0
    manifest_raw = (campaign / "campaign-manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    records = manifest["scenarios"]
    candidate_closure = "b" * 64
    candidate_manifest = "c" * 64
    parity_scenarios: list[dict[str, object]] = []
    for record in records:
        event, result = _reference_digests(record["scenario_id"])
        parity_scenarios.append(
            {
                **record,
                "independent_reference_event_sha256": event,
                "independent_reference_result_sha256": result,
                "nautilus_event_sha256": event,
                "nautilus_result_sha256": result,
                "run_1_event_sha256": event,
                "run_2_event_sha256": event,
            }
        )
    parity = {
        "candidate_closure_sha256": candidate_closure,
        "candidate_manifest_schema_version": 6,
        "candidate_manifest_sha256": candidate_manifest,
        "scenario_campaign_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "scenarios": parity_scenarios,
        "schema_version": "nautilus-phase4-parity-evidence-v2",
        "status": "passed",
        "strategy_source_sha256": manifest["strategy_source_sha256"],
    }
    parity_path = private_root / "parity.json"
    parity_raw = canonical_json_bytes(parity) + b"\n"
    _seal(parity_path, parity_raw)
    long_accounting = records[0]
    paper = research.PaperCompatibilityResultV1.create(
        candidate_closure_sha256="d" * 64,
        candidate_manifest_sha256="e" * 64,
        engine_configuration_sha256=long_accounting["engine_configuration_sha256"],
        instrument_catalog_sha256=long_accounting["instrument_catalog_sha256"],
        strategy_configuration_sha256=long_accounting["strategy_configuration_sha256"],
        strategy_source_sha256=manifest["strategy_source_sha256"],
        scenario_campaign_sha256=hashlib.sha256(manifest_raw).hexdigest(),
        parity_record_sha256=hashlib.sha256(parity_raw).hexdigest(),
        launcher_result_sha256="d" * 64,
    )
    paper_path = private_root / "paper.json"
    _seal(paper_path, canonical_json_bytes(paper) + b"\n")
    legacy = private_root / "legacy"
    legacy.mkdir(mode=0o700)
    for index, record in enumerate(records):
        legacy_record = {
            "engine_configuration_sha256": record["engine_configuration_sha256"],
            "instrument_catalog_sha256": record["instrument_catalog_sha256"],
            "legacy_classification": "legacy-minimum-50-bars",
            "legacy_disposition": "explained-difference",
            "legacy_event_sha256": hashlib.sha256(
                f"legacy-event:{index}".encode("ascii")
            ).hexdigest(),
            "legacy_result_sha256": hashlib.sha256(
                f"legacy-result:{index}".encode("ascii")
            ).hexdigest(),
            "legacy_selected": False,
            "market_data_sha256": record["market_data_sha256"],
            "scenario_id": record["scenario_id"],
            "schema_version": "nautilus-legacy-scenario-comparison-v1",
            "simulation_scenario_sha256": record["simulation_scenario_sha256"],
            "strategy_configuration_sha256": record["strategy_configuration_sha256"],
        }
        _seal(
            legacy / f"{record['scenario_id']}.json",
            canonical_json_bytes(legacy_record) + b"\n",
        )
    legacy.chmod(0o500)
    return campaign, parity_path, paper_path, legacy


def _rewrite_paper_record(path: Path, **updates: str) -> None:
    paper = research.PaperCompatibilityResultV1.model_validate_json(path.read_bytes())
    fields = paper.model_dump(
        exclude={"compatible", "result_sha256", "schema_version"}
    )
    fields.update(updates)
    rewritten = research.PaperCompatibilityResultV1.create(**fields)
    path.chmod(0o600)
    path.write_bytes(canonical_json_bytes(rewritten) + b"\n")
    path.chmod(0o400)


def _public_research_authorities(
    private_root: Path,
) -> tuple[Path, Path, Path, Path]:
    """Exercise every reviewed producer boundary before deriving campaign evidence."""

    campaign = private_root / "campaign"
    research.materialize_phase4_campaign(campaign)
    parity_paths = {
        "rollback_closure": private_root / "rollback",
        "candidate_closure": private_root / "candidate",
        "rollback_artifact_directory": private_root / "rollback-artifacts",
        "artifact_directory": private_root / "candidate-artifacts",
        "sandbox": private_root / "sandbox",
        "campaign_directory": campaign,
        "transport_root": private_root / "parity-transport",
        "record": private_root / "parity-evidence" / "parity.json",
    }
    for name in (
        "rollback_closure",
        "candidate_closure",
        "rollback_artifact_directory",
        "artifact_directory",
        "transport_root",
    ):
        parity_paths[name].mkdir(mode=0o700)
    for name in ("rollback_artifact_directory", "artifact_directory"):
        marker = parity_paths[name] / "artifact-manifest.json"
        marker.write_bytes(b"{}")
        marker.chmod(0o400)
        parity_paths[name].chmod(0o500)
    parity_paths["record"].parent.mkdir(mode=0o700)
    parity_harness = ParityHarness()
    parity_verifier.verify_nautilus_v12_r3_parity(
        **parity_paths,
        attest_closure=parity_harness.attest,
        provider_factory=parity_harness.provider_factory,
        consume_spawn=parity_harness.consume,
        popen_factory=parity_harness.popen,
    )
    assert len(parity_harness.prepare_calls) == 16
    parity_path = parity_paths["record"]

    command, bindings, parity_digest = paper_verifier._campaign_authority(
        campaign,
        parity_path,
        candidate_closure_sha256="b" * 64,
        candidate_manifest_sha256="c" * 64,
    )
    resolver = HashBoundArtifactResolver(bindings)

    class InertPaperProvider:
        def prepare(self, observed_command):
            assert observed_command is command
            resolved = resolver(observed_command)
            assert tuple(item.source for item in resolved) == tuple(
                item.source for item in bindings
            )
            return SimpleNamespace(command=observed_command)

    launcher = {
        "compatible": True,
        "engine_configuration_sha256": command.engine_configuration.sha256,
        "event_type": "PaperCompatibilityValidated",
        "instrument_catalog_sha256": command.instrument_catalog.sha256,
        "scenario_campaign_sha256": command.scenario_campaign_sha256,
        "strategy_configuration_sha256": command.strategy_configuration.sha256,
        "strategy_source_sha256": command.strategy_source_sha256,
    }
    paper_result = paper_verifier.capture_paper_compatibility(
        provider=InertPaperProvider(),
        command=command,
        candidate_closure_sha256="d" * 64,
        candidate_manifest_sha256="e" * 64,
        parity_record_sha256=parity_digest,
        consume=lambda prepared: SimpleNamespace(command=prepared.command),
        capture=lambda _built, **_kwargs: SimpleNamespace(
            returncode=0,
            stdout=canonical_json_bytes(launcher) + b"\n",
            stderr=b"",
        ),
    )
    paper_transport = private_root / "paper-transport"
    paper_transport.mkdir(mode=0o700)
    paper_path = paper_verifier.publish_paper_compatibility_result(
        paper_transport,
        paper_result,
    )

    legacy = private_root / "legacy"
    legacy.mkdir(mode=0o700)
    uv_authority = _open_retained_uv_authority(
        LEGACY_UV_PATH,
        expected_sha256=LEGACY_UV_SHA256,
    )
    try:
        version = _run_retained_uv(
            uv_authority,
            ("--version",),
            clean_env=False,
        )
        assert version.returncode == 0
        assert version.stdout == f"{LEGACY_UV_VERSION}\n".encode("ascii")
        assert version.stderr == b""
        synced = _run_retained_uv(
            uv_authority,
            LEGACY_SYNC_ARGUMENTS,
            clean_env=True,
            cwd=LEGACY_ROOT,
        )
        assert synced.returncode == 0, synced.stderr.decode(
            "utf-8", errors="replace"
        )
        _recheck_retained_uv_authority(
            uv_authority,
            LEGACY_UV_PATH,
            expected_sha256=LEGACY_UV_SHA256,
        )
    finally:
        os.close(uv_authority.descriptor)
    legacy_commands: list[tuple[str, ...]] = []
    for scenario_id in SCENARIO_IDS:
        command = (
            *LEGACY_ENV_COMMAND,
            *LEGACY_ADAPTER_COMMAND,
            "--campaign-directory",
            str(campaign),
            "--transport-root",
            str(legacy),
            "--scenario-id",
            scenario_id,
        )
        legacy_commands.append(command)
        completed = subprocess.run(
            command,
            cwd=REPOSITORY_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr.decode(
            "utf-8", errors="replace"
        )
        assert completed.stdout == b""
        assert completed.stderr == b""
    assert len(legacy_commands) == 8
    assert tuple(command[-1] for command in legacy_commands) == SCENARIO_IDS
    assert all(
        command[: len(LEGACY_ENV_COMMAND)] == LEGACY_ENV_COMMAND
        and command[
            len(LEGACY_ENV_COMMAND) : len(LEGACY_ENV_COMMAND)
            + len(LEGACY_ADAPTER_COMMAND)
        ]
        == LEGACY_ADAPTER_COMMAND
        for command in legacy_commands
    )
    legacy.chmod(0o500)
    return campaign, parity_path, paper_path, legacy


def test_research_producer_derives_complete_campaign_evidence_from_sealed_inputs(
    private_root: Path,
) -> None:
    campaign, parity, paper, legacy = _public_research_authorities(private_root)

    evidence = research.produce_research_campaign_evidence(
        campaign_directory=campaign,
        parity_record=parity,
        paper_record=paper,
        legacy_record_directory=legacy,
    )

    assert evidence.schema_version == "research-campaign-evidence-v2"
    assert evidence.candidate_closure_sha256 == "b" * 64
    assert evidence.candidate_manifest_sha256 == "c" * 64
    assert evidence.paper_result.candidate_closure_sha256 == "d" * 64
    assert evidence.paper_result.candidate_manifest_sha256 == "e" * 64
    assert tuple(item.scenario_id for item in evidence.comparisons) == SCENARIO_IDS
    assert len(evidence.point_in_time) == 8
    assert len(evidence.recursive_replays) == 8
    assert len(evidence.walk_forward_folds) == 2
    assert {item.name for item in evidence.cost_scenarios} == {
        "baseline",
        "combined-stress",
        "fee-stress",
        "slippage-stress",
    }
    assert research.evaluate_research_campaign(evidence).passed is True
    assert evidence.promotion_authority == "reference-and-nautilus"


def test_research_producer_accepts_distinct_simulation_and_paper_candidates(
    private_root: Path,
) -> None:
    campaign, parity, paper, legacy = _research_authorities(private_root)

    evidence = research.produce_research_campaign_evidence(
        campaign_directory=campaign,
        parity_record=parity,
        paper_record=paper,
        legacy_record_directory=legacy,
    )

    assert evidence.candidate_closure_sha256 == "b" * 64
    assert evidence.candidate_manifest_sha256 == "c" * 64
    assert evidence.paper_result.candidate_closure_sha256 == "d" * 64
    assert evidence.paper_result.candidate_manifest_sha256 == "e" * 64


@pytest.mark.parametrize(
    "field",
    (
        "scenario_campaign_sha256",
        "strategy_source_sha256",
        "parity_record_sha256",
        "engine_configuration_sha256",
        "instrument_catalog_sha256",
        "strategy_configuration_sha256",
    ),
)
def test_research_producer_rejects_paper_common_authority_drift(
    private_root: Path,
    field: str,
) -> None:
    campaign, parity, paper, legacy = _research_authorities(private_root)
    _rewrite_paper_record(paper, **{field: "0" * 64})

    with pytest.raises(ValueError, match="paper record binding drifted"):
        research.produce_research_campaign_evidence(
            campaign_directory=campaign,
            parity_record=parity,
            paper_record=paper,
            legacy_record_directory=legacy,
        )


@pytest.mark.parametrize(
    "field", ("candidate_closure_sha256", "candidate_manifest_sha256")
)
def test_research_producer_rejects_forged_paper_candidate_digest(
    private_root: Path,
    field: str,
) -> None:
    campaign, parity, paper, legacy = _research_authorities(private_root)
    paper.chmod(0o600)
    document = json.loads(paper.read_bytes())
    document[field] = "0" * 64
    paper.write_bytes(canonical_json_bytes(document) + b"\n")
    paper.chmod(0o400)

    with pytest.raises(ValueError, match="paper record is invalid"):
        research.produce_research_campaign_evidence(
            campaign_directory=campaign,
            parity_record=parity,
            paper_record=paper,
            legacy_record_directory=legacy,
        )


def test_task8_legacy_adapter_command_is_literal_nested_venv_direct_script() -> None:
    assert LEGACY_UV_PATH == Path("/home/thenam176/.local/bin/uv")
    assert LEGACY_UV_SHA256 == (
        "cd952ca51e2c730e848a45c4e0dfb58926d79d90550b6a5feb5543b43d3248b4"
    )
    assert LEGACY_UV_VERSION == "uv 0.11.7 (x86_64-unknown-linux-gnu)"
    authority = _open_retained_uv_authority(
        LEGACY_UV_PATH,
        expected_sha256=LEGACY_UV_SHA256,
    )
    try:
        assert authority.exec_path == f"/proc/self/fd/{authority.descriptor}"
        version = _run_retained_uv(authority, ("--version",), clean_env=False)
        assert version.returncode == 0
        assert version.stdout == f"{LEGACY_UV_VERSION}\n".encode("ascii")
        assert version.stderr == b""
        _recheck_retained_uv_authority(
            authority,
            LEGACY_UV_PATH,
            expected_sha256=LEGACY_UV_SHA256,
        )
    finally:
        os.close(authority.descriptor)
    assert LEGACY_ENV_COMMAND == (
        "/usr/bin/env",
        "-i",
        "PATH=/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE=1",
        "PYTHONHASHSEED=0",
        "PYTHONNOUSERSITE=1",
        "UV_OFFLINE=1",
    )
    assert LEGACY_SYNC_ARGUMENTS == ("sync", "--frozen", "--extra", "test")
    assert LEGACY_ADAPTER_COMMAND == (
        "legacy/research-backend/.venv/bin/python",
        "legacy/research-backend/nautilus_parity_adapter.py",
    )
    assert "-I" not in LEGACY_ADAPTER_COMMAND


def test_retained_uv_fd_executes_verified_inode_and_rejects_named_swap(
    private_root: Path,
) -> None:
    tool = private_root / "uv-fixture"
    original = b"#!/bin/sh\nprintf 'uv-test-original\\n'\n"
    replacement = b"#!/bin/sh\nprintf 'uv-test-replacement\\n'\n"
    tool.write_bytes(original)
    tool.chmod(0o755)
    expected_sha256 = hashlib.sha256(original).hexdigest()
    authority = _open_retained_uv_authority(
        tool,
        expected_sha256=expected_sha256,
    )
    displaced = private_root / "uv-fixture.displaced"
    try:
        tool.rename(displaced)
        tool.write_bytes(replacement)
        tool.chmod(0o755)

        executed = _run_retained_uv(authority, (), clean_env=False)

        assert executed.returncode == 0
        assert executed.stdout == b"uv-test-original\n"
        assert executed.stderr == b""
        with pytest.raises(AssertionError, match="identity changed"):
            _recheck_retained_uv_authority(
                authority,
                tool,
                expected_sha256=expected_sha256,
            )
    finally:
        os.close(authority.descriptor)


def test_research_snapshot_rejects_equivalent_parity_parent_substitution(
    private_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign, parity, paper, legacy = _research_authorities(private_root)
    parity_parent = private_root / "parity-parent"
    paper_parent = private_root / "paper-parent"
    parity_parent.mkdir(mode=0o700)
    paper_parent.mkdir(mode=0o700)
    parity = parity.rename(parity_parent / parity.name)
    paper = paper.rename(paper_parent / paper.name)
    replacement = private_root / "replacement-parity-parent"
    replacement.mkdir(mode=0o700)
    shutil.copy2(parity, replacement / parity.name)
    displaced = private_root / "displaced-parity-parent"
    real_open = producers.os.open
    swapped = False

    def swap_after_parity_read(path, flags, *args, **kwargs):
        nonlocal swapped
        if not swapped and os.fspath(path) == paper.name:
            parity_parent.rename(displaced)
            replacement.rename(parity_parent)
            swapped = True
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(producers.os, "open", swap_after_parity_read)

    with pytest.raises(ValueError, match="parity.*identity|aggregate.*identity"):
        research.produce_research_campaign_evidence(
            campaign_directory=campaign,
            parity_record=parity,
            paper_record=paper,
            legacy_record_directory=legacy,
        )

    assert swapped is True


def test_research_snapshot_rejects_mixed_equivalent_legacy_generations(
    private_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    campaign, parity, paper, legacy = _research_authorities(private_root)
    replacement = private_root / "replacement-legacy"
    shutil.copytree(legacy, replacement)
    replacement.chmod(0o500)
    displaced = private_root / "displaced-legacy"
    trigger = legacy / "short-accounting.json"
    real_open = producers.os.open
    swapped = False

    def swap_between_legacy_members(path, flags, *args, **kwargs):
        nonlocal swapped
        if not swapped and os.fspath(path) == trigger.name:
            legacy.rename(displaced)
            replacement.rename(legacy)
            swapped = True
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(producers.os, "open", swap_between_legacy_members)

    with pytest.raises(ValueError, match="legacy.*identity|aggregate.*identity"):
        research.produce_research_campaign_evidence(
            campaign_directory=campaign,
            parity_record=parity,
            paper_record=paper,
            legacy_record_directory=legacy,
        )

    assert swapped is True


def test_research_derivations_use_independent_replay_and_oracle_oos_values(
    private_root: Path,
) -> None:
    campaign, parity, paper, legacy = _research_authorities(private_root)

    evidence = research.produce_research_campaign_evidence(
        campaign_directory=campaign,
        parity_record=parity,
        paper_record=paper,
        legacy_record_directory=legacy,
    )

    market_rows = (
        campaign / "long-accounting" / "market-data.json"
    ).read_bytes().splitlines()
    state = bytes.fromhex(hashlib.sha256(b"\n".join(market_rows) + b"\n").hexdigest())
    for row in market_rows:
        state = hashlib.sha256(state + canonical_json_bytes(json.loads(row))).digest()
    replay = evidence.recursive_replays[0]
    assert replay.prefix_state_sha256 == state.hex()
    assert replay.replay_state_sha256 == state.hex()
    assert tuple(item.out_of_sample_return for item in evidence.walk_forward_folds) == (
        Decimal("-0.649"),
        Decimal("0.399"),
    )
    assert evidence.minimum_walk_forward_return == Decimal("-0.5")
    assert evidence.walk_forward_folds[0].input_artifacts_sha256 != (
        evidence.walk_forward_folds[1].input_artifacts_sha256
    )
    assert all(
        item.input_artifacts_sha256 != evidence.scenario_campaign_sha256
        for item in evidence.walk_forward_folds
    )


def test_research_producer_rejects_caller_authored_status_or_legacy_authority(
    private_root: Path,
) -> None:
    campaign, parity, paper, legacy = _research_authorities(private_root)
    parity.chmod(0o600)
    value = json.loads(parity.read_bytes())
    value["status"] = "failed"
    parity.write_bytes(canonical_json_bytes(value) + b"\n")
    parity.chmod(0o400)

    with pytest.raises(ValueError, match="parity"):
        research.produce_research_campaign_evidence(
            campaign_directory=campaign,
            parity_record=parity,
            paper_record=paper,
            legacy_record_directory=legacy,
        )

    second = private_root / "second"
    second.mkdir(mode=0o700)
    campaign, parity, paper, legacy = _research_authorities(second)
    selected = legacy / "long-accounting.json"
    legacy.chmod(0o700)
    selected.chmod(0o600)
    value = json.loads(selected.read_bytes())
    value["legacy_selected"] = True
    selected.write_bytes(canonical_json_bytes(value) + b"\n")
    selected.chmod(0o400)
    legacy.chmod(0o500)
    with pytest.raises(ValueError, match="legacy"):
        research.produce_research_campaign_evidence(
            campaign_directory=campaign,
            parity_record=parity,
            paper_record=paper,
            legacy_record_directory=legacy,
        )


def test_research_producer_rejects_equal_but_caller_chosen_parity_digests(
    private_root: Path,
) -> None:
    campaign, parity, paper, legacy = _research_authorities(private_root)
    parity.chmod(0o600)
    parity_value = json.loads(parity.read_bytes())
    parity_value["scenarios"][0].update(
        {
            "independent_reference_event_sha256": "0" * 64,
            "independent_reference_result_sha256": "1" * 64,
            "nautilus_event_sha256": "0" * 64,
            "nautilus_result_sha256": "1" * 64,
            "run_1_event_sha256": "0" * 64,
            "run_2_event_sha256": "0" * 64,
        }
    )
    parity_raw = canonical_json_bytes(parity_value) + b"\n"
    parity.write_bytes(parity_raw)
    parity.chmod(0o400)

    paper_value = research.PaperCompatibilityResultV1.model_validate_json(
        paper.read_bytes()
    )
    paper_value = research.PaperCompatibilityResultV1.create(
        candidate_closure_sha256=paper_value.candidate_closure_sha256,
        candidate_manifest_sha256=paper_value.candidate_manifest_sha256,
        engine_configuration_sha256=paper_value.engine_configuration_sha256,
        instrument_catalog_sha256=paper_value.instrument_catalog_sha256,
        strategy_configuration_sha256=paper_value.strategy_configuration_sha256,
        strategy_source_sha256=paper_value.strategy_source_sha256,
        scenario_campaign_sha256=paper_value.scenario_campaign_sha256,
        parity_record_sha256=hashlib.sha256(parity_raw).hexdigest(),
        launcher_result_sha256=paper_value.launcher_result_sha256,
    )
    paper.chmod(0o600)
    paper.write_bytes(canonical_json_bytes(paper_value) + b"\n")
    paper.chmod(0o400)

    with pytest.raises(ValueError, match="oracle"):
        research.produce_research_campaign_evidence(
            campaign_directory=campaign,
            parity_record=parity,
            paper_record=paper,
            legacy_record_directory=legacy,
        )


def test_research_cli_requires_custody_digests_and_seals_one_fixed_closure_record(
    private_root: Path,
) -> None:
    campaign, parity, paper, legacy = _public_research_authorities(private_root)
    transport = private_root / "research-transport"
    transport.mkdir(mode=0o700)
    campaign_sha256 = hashlib.sha256(
        (campaign / "campaign-manifest.json").read_bytes()
    ).hexdigest()
    parity_sha256 = hashlib.sha256(parity.read_bytes()).hexdigest()
    paper_sha256 = hashlib.sha256(paper.read_bytes()).hexdigest()
    legacy_sha256 = hashlib.sha256(
        canonical_json_bytes(
            [
                hashlib.sha256(
                    (legacy / f"{scenario_id}.json").read_bytes()
                ).hexdigest()
                for scenario_id in SCENARIO_IDS
            ]
        )
    ).hexdigest()
    required = [
        "--campaign-directory",
        str(campaign),
        "--campaign-sha256",
        campaign_sha256,
        "--parity-record",
        str(parity),
        "--parity-record-sha256",
        parity_sha256,
        "--paper-record",
        str(paper),
        "--paper-record-sha256",
        paper_sha256,
        "--legacy-record-directory",
        str(legacy),
        "--legacy-records-sha256",
        legacy_sha256,
        "--transport-root",
        str(transport),
    ]

    completed = subprocess.run(
        [sys.executable, str(RESEARCH_CLOSER), *required],
        cwd=REPOSITORY_ROOT,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    assert completed.stderr == b""
    assert completed.stdout == b""
    result = transport / "ws04-campaign-closure-v2.json"
    closure_raw = result.read_bytes()
    closure = json.loads(closure_raw)
    assert closure_raw == canonical_json_bytes(closure) + b"\n"
    assert stat.S_IMODE(result.stat().st_mode) == 0o400
    assert closure["schema_version"] == "ws04-campaign-closure-v2"
    missing_custody = subprocess.run(
        [sys.executable, str(RESEARCH_CLOSER), *required[:2], *required[4:]],
        cwd=REPOSITORY_ROOT,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert missing_custody.returncode == 2
    rejected = subprocess.run(
        [sys.executable, str(RESEARCH_CLOSER), *required, "--evidence-root", "/tmp/x"],
        cwd=REPOSITORY_ROOT,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert rejected.returncode == 2


def test_research_closure_publication_rejects_transport_root_substitution(
    private_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = private_root / "research-transport"
    transport.mkdir(mode=0o700)
    replacement = private_root / "replacement-transport"
    replacement.mkdir(mode=0o700)
    displaced = private_root / "displaced-transport"
    result = transport / "ws04-campaign-closure-v2.json"
    real_fsync = os.fsync
    swapped = False

    def swap_transport(descriptor: int) -> None:
        nonlocal swapped
        if not swapped and result.exists():
            transport.rename(displaced)
            replacement.rename(transport)
            swapped = True
        real_fsync(descriptor)

    monkeypatch.setattr(research_closer.os, "fsync", swap_transport)

    with pytest.raises(ValueError, match="identity|changed"):
        research_closer._publish_closure(transport, {"closure": "inert"})

    assert swapped is True
    assert not result.exists()
    retained = displaced / "ws04-campaign-closure-v2.json"
    assert retained.exists()
    assert stat.S_IMODE(retained.stat().st_mode) == 0o400


def test_research_closure_publication_preserves_replacement_record_inode(
    private_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = private_root / "research-transport"
    transport.mkdir(mode=0o700)
    result = transport / "ws04-campaign-closure-v2.json"
    real_fsync = os.fsync
    replacement_identity: tuple[int, int] | None = None

    def replace_result(descriptor: int) -> None:
        nonlocal replacement_identity
        if replacement_identity is None and result.exists():
            result.unlink()
            result.write_bytes(b"replacement")
            result.chmod(0o400)
            observed = result.stat()
            replacement_identity = (observed.st_dev, observed.st_ino)
        real_fsync(descriptor)

    monkeypatch.setattr(research_closer.os, "fsync", replace_result)

    with pytest.raises(ValueError, match="identity|changed"):
        research_closer._publish_closure(transport, {"closure": "inert"})

    assert replacement_identity is not None
    observed = result.stat()
    assert (observed.st_dev, observed.st_ino) == replacement_identity


def test_research_closure_publication_retains_its_sealed_partial_record(
    private_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = private_root / "research-transport"
    transport.mkdir(mode=0o700)
    result = transport / "ws04-campaign-closure-v2.json"
    real_write = os.write
    calls = 0

    def partial_then_fail(descriptor: int, value) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(descriptor, value[:3])
        raise OSError("inert partial closure write")

    monkeypatch.setattr(research_closer.os, "write", partial_then_fail)

    with pytest.raises(ValueError, match="cannot be sealed"):
        research_closer._publish_closure(transport, {"closure": "inert"})

    assert calls == 2
    assert result.read_bytes() == canonical_json_bytes({"closure": "inert"})[:3]
    assert stat.S_IMODE(result.stat().st_mode) == 0o400


def test_research_closure_failure_never_calls_production_unlink(
    private_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = private_root / "research-transport"
    transport.mkdir(mode=0o700)
    real_write = os.write
    calls = 0
    unlink_calls: list[object] = []

    def partial_then_fail(descriptor: int, value) -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(descriptor, value[:3])
        raise OSError("inert partial closure write")

    monkeypatch.setattr(research_closer.os, "write", partial_then_fail)
    monkeypatch.setattr(
        research_closer.os,
        "unlink",
        lambda path, **_kwargs: unlink_calls.append(path),
    )

    with pytest.raises(ValueError, match="cannot be sealed"):
        research_closer._publish_closure(transport, {"closure": "inert"})

    assert unlink_calls == []
    assert (transport / "ws04-campaign-closure-v2.json").exists()


def test_authoritative_closer_rejects_missing_or_mismatched_custody_selection(
    private_root: Path,
) -> None:
    campaign, parity, paper, legacy = _research_authorities(private_root)
    expected = {
        "campaign_directory": campaign,
        "campaign_sha256": hashlib.sha256(
            (campaign / "campaign-manifest.json").read_bytes()
        ).hexdigest(),
        "parity_record": parity,
        "parity_record_sha256": hashlib.sha256(parity.read_bytes()).hexdigest(),
        "paper_record": paper,
        "paper_record_sha256": hashlib.sha256(paper.read_bytes()).hexdigest(),
        "legacy_record_directory": legacy,
        "legacy_records_sha256": hashlib.sha256(
            canonical_json_bytes(
                [
                    hashlib.sha256(
                        (legacy / f"{scenario_id}.json").read_bytes()
                    ).hexdigest()
                    for scenario_id in SCENARIO_IDS
                ]
            )
        ).hexdigest(),
    }

    with pytest.raises(ValueError, match="custody|digest"):
        research.close_ws04_research_campaign(
            **(expected | {"parity_record_sha256": "0" * 64})
        )
    with pytest.raises(TypeError):
        research.close_ws04_research_campaign(
            campaign_directory=campaign,
            parity_record=parity,
            paper_record=paper,
            legacy_record_directory=legacy,
        )
