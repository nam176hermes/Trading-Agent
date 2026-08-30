"""P1-only schema-8 validation over the immutable legacy spawn provider."""

from __future__ import annotations

import json
import hashlib
import os
import re
import stat
import weakref
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import NoReturn

from engines.nautilus.runtime_v1.control_channel import PAPER_SOURCE_NAMES
from packages.engine_contracts import (
    EngineCommandEnvelope,
    RunBacktest,
    RunBacktestSimulation,
    ValidatePaperCompatibility,
    canonical_json_bytes,
)

from . import engine_spawn as _legacy
from .engine_profiles import EngineProfilePolicy, P1_REAL_BACKTEST_POLICY
from .engine_spawn_interface import EngineSpawnError


_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_PRODUCT_LINEAGE_TARGET = PurePosixPath("/engine/p1-product-lineage.json")
_PAPER_SOURCE_ROOT = Path(__file__).parents[2] / "engines/nautilus/runtime_v1"
_PAPER_SOURCE_SHA256S = {
    "__init__.py": "58a58bb530f3ca22cd042a33e6c0107f6217ac0aef91a49aab74add78cd6428a",
    "backtest_runner.py": "584d2192a66ac8f77c533a93e42ff4cac9f577afa1c05cb02804bd32228b8817",
    "bootstrap.py": "11be142a57862b61187c031f8cc9efc6b8275b97f54e3e8134e7ec7364bfcfd3",
    "control_channel.py": "2df4a676e6a24c81aa622af1542bbf01a32894b07679bc33a4aa761fd8d9b26b",
    "currency_metadata.py": "07f570e116ca45c7c6f25f695099b3425eb5fb1f9e27895b8a1eb5cc0f409810",
    "dependency_scope.py": "0261c798df03ce8110a3cb8c1f3d9606e6dd3aa9ce23f81c286ee5954eb93131",
    "diagnostics.py": "c92e196e26bd265f4c7d6c75afe25957aa9b684d30d017eb521adea12b26fdca",
    "errors.py": "c0776fb03c972f812cc893931d4a1d2bb1e2b6ef9f25e02a6c722a6065e62a90",
    "event_collector.py": "d8edfbcc69550a3af598bd2e60e36a6bf4624d733a7df9e340ebc2a6467d25b0",
    "event_projector.py": "1f4b2065ff6e9a438de60dac2216b1205a38fe82c26e73e7da0129bef7bf54aa",
    "final_state.py": "022e716c3e2b2fa5bdbace08772c8e2db24b943035aac6b873f943ab09a1aa82",
    "generated_protocol.py": "e89404378d1c9dbadddd75a12e5f184eaf4dd347befcd5913cab61a34a17d734",
    "input_loader.py": "c715ef68a877c887319c075b224ef991a2bf09cbfb25a6b9b833a6fad9823e1d",
    "instrument_factory.py": "5863522456b12a0396804690f3410b7ba49f67c1de777b876fb71e0a64f915e2",
    "jsonl_writer.py": "52e4c9fdc254f07590e322bdad6fe20eecc2086f74b56dab81f70d3d8125805b",
    "main.py": "acd55815fb3a146c76dd05bdc3d98e0c9853115b8f28199ad80c10f2c07a2cd3",
    "market_data_loader.py": "02c0c651e34d167746a87410007213fd1cc40f43ba9948748e017f4ac7ce061a",
    "paper_main.py": "28c7e7c960584fa285393ef45301e52f7f73dc378cb305db9f36de244baf2c8c",
    "paper_prefix.py": "cb4e3eaec8915bea5041505e685883daaed71b7226841c4d0d7339a355ad81fb",
    "paper_runner.py": "53f0354e999ea49b12b5c994e2013c240bbc4bdc92bc73b920042698bf77910e",
    "paper_session.py": "72e016587b19a5942671ddbac4d4ecdbc67d4e5433797a29bf500da857403195",
    "profile.py": "d28e2d22f6d1c915fdd73329ce42e70d0db6bce9d64442f95a8c6394829d851c",
    "session.py": "7120f0e11c5b010efc3bd5dc787bafec2f14413bdbd483b0c57755675c140a38",
    "target_planner.py": "2444d8c0c2feda4a226980f98286bf521e4241c46fdd0d9056bbffa77a944aa2",
    "target_strategy.py": "57eccf4a1f9e496f900865aa09f72dc0230aba0cb9fb21792c8069b0963b8942",
}
P1_PAPER_SOURCE_SHA256 = hashlib.sha256(
    canonical_json_bytes(tuple(sorted(_PAPER_SOURCE_SHA256S.items())))
).hexdigest()


def _blocked(message: str) -> NoReturn:
    raise EngineSpawnError("ENGINE_CLOSURE_INVALID", message)


@dataclass(frozen=True, slots=True)
class P1EngineClosureAttestation:
    """Exact schema-8 P1 closure authority before legacy spawn adaptation."""

    manifest_schema_version: int
    profile: str
    source_commit: str
    closure_sha256: str
    mounts: tuple[_legacy.ReadOnlyClosureMount, ...]
    entrypoint: PurePosixPath
    argv_prefix: tuple[str, ...]
    timeout_seconds: int
    result_validator_id: str
    sandbox: _legacy.OsSandboxProof
    semantic_profile: str
    closure_manifest: _legacy.ReadOnlyClosureMount
    native_entry_guard: _legacy.NativeEntryGuardAttestation
    dependency_import_policy: str
    runtime_family: str
    engine_version: str
    engine_upstream_commit: str
    event_schema: str
    runtime_inventory_sha256: str
    product_lineage: _legacy.ReadOnlyClosureMount


@dataclass(
    frozen=True,
    slots=True,
    init=False,
    eq=False,
    repr=False,
    weakref_slot=True,
)
class P1PaperLaunchAuthority:
    """One-use exact Bubblewrap launch with sealed P1 paper source snapshots."""

    built: _legacy.EngineBuiltSpawn
    closure_sha256: str
    request_sha256: str
    paper_source_sha256: str
    argv_sha256: str


_ISSUED_PAPER_LAUNCHES: weakref.WeakSet[P1PaperLaunchAuthority] = weakref.WeakSet()


def _paper_source_snapshots() -> tuple[tuple[str, str, int], ...]:
    names = tuple(sorted(PAPER_SOURCE_NAMES))
    if tuple(sorted(_PAPER_SOURCE_SHA256S)) != names:
        _blocked("P1 paper source policy is invalid")
    observed = tuple(sorted(path.name for path in _PAPER_SOURCE_ROOT.glob("*.py")))
    if observed != names:
        _blocked("P1 paper source inventory is invalid")
    snapshots: list[tuple[str, str, int]] = []
    try:
        for name in names:
            path = _PAPER_SOURCE_ROOT / name
            before = path.lstat()
            raw = path.read_bytes()
            after = path.lstat()
            if (
                stat.S_ISLNK(before.st_mode)
                or not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.geteuid()
                or stat.S_IMODE(before.st_mode) & 0o022
                or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            ):
                raise ValueError
            digest = hashlib.sha256(raw).hexdigest()
            if digest != _PAPER_SOURCE_SHA256S[name]:
                raise ValueError
            descriptor = _legacy._sealed_memfd(f"p1-paper-{name}", raw, mode=0o400)
            snapshots.append((name, digest, descriptor))
    except (OSError, ValueError) as exc:
        for _name, _digest, descriptor in snapshots:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise EngineSpawnError(
            "ENGINE_INPUT_STALE", "P1 paper source authority changed"
        ) from exc
    return tuple(snapshots)


def _validate_native_entry_guard(
    attestation: P1EngineClosureAttestation,
) -> None:
    guard = attestation.native_entry_guard
    if type(guard) is not _legacy.NativeEntryGuardAttestation:
        _blocked("P1 native entry guard contract is invalid")
    digest_fields = (
        guard.binary_sha256,
        guard.source_sha256,
        guard.cargo_manifest_sha256,
        guard.cargo_lock_sha256,
        guard.rust_toolchain_policy_sha256,
        guard.llvm_toolchain_policy_sha256,
    )
    if (
        guard.target != _legacy._NATIVE_GUARD_TARGET
        or guard.guarded_executable != _legacy._NATIVE_GUARDED_EXECUTABLE
        or attestation.entrypoint != guard.target
        or attestation.argv_prefix != P1_REAL_BACKTEST_POLICY.argv_prefix
        or guard.mode != 0o500
        or guard.source != _legacy._NATIVE_GUARD_SOURCE
        or guard.cargo_manifest != _legacy._NATIVE_GUARD_CARGO_MANIFEST
        or guard.cargo_lock != _legacy._NATIVE_GUARD_CARGO_LOCK
        or isinstance(guard.binary_size, bool)
        or not isinstance(guard.binary_size, int)
        or guard.binary_size <= 0
        or any(
            not isinstance(digest, str) or _SHA256.fullmatch(digest) is None
            for digest in digest_fields
        )
        or not isinstance(guard.cargo_identity, str)
        or _legacy._NATIVE_CARGO_IDENTITY.fullmatch(guard.cargo_identity) is None
        or not isinstance(guard.rustc_identity, str)
        or _legacy._NATIVE_RUSTC_IDENTITY.fullmatch(guard.rustc_identity) is None
        or guard.target_triple != "x86_64-unknown-linux-gnu"
    ):
        _blocked("P1 native entry guard contract is invalid")
    matching_guard = [
        mount for mount in attestation.mounts if mount.target == guard.target
    ]
    matching_python = [
        mount
        for mount in attestation.mounts
        if mount.target == guard.guarded_executable
    ]
    if (
        len(matching_guard) != 1
        or matching_guard[0].mode != guard.mode
        or matching_guard[0].size != guard.binary_size
        or matching_guard[0].sha256 != guard.binary_sha256
        or len(matching_python) != 1
        or matching_python[0].mode != 0o500
        or not matching_python[0].mode & 0o111
        or guard.guarded_executable == guard.target
    ):
        _blocked("P1 native entry guard executable binding is invalid")


def _adapt_p1_closure(value: object) -> _legacy.CompleteEngineClosureAttestation:
    if type(value) is not P1EngineClosureAttestation:
        raise EngineSpawnError(
            "ENGINE_CLOSURE_UNAVAILABLE",
            "typed P1 engine closure attestation is required",
        )
    attestation = value
    profile = P1_REAL_BACKTEST_POLICY
    if (
        attestation.manifest_schema_version != profile.manifest_schema_version
        or attestation.profile != profile.profile
        or attestation.closure_sha256 != profile.closure_sha256
        or attestation.semantic_profile != profile.semantic_profile
        or attestation.entrypoint != PurePosixPath(profile.entrypoint)
        or attestation.argv_prefix != profile.argv_prefix
        or attestation.timeout_seconds != profile.timeout_seconds
        or attestation.result_validator_id != profile.result_validator_id
        or attestation.dependency_import_policy != profile.dependency_import_policy
        or attestation.runtime_family != profile.runtime_family
        or attestation.engine_version != profile.engine_version
        or attestation.engine_upstream_commit != profile.engine_upstream_commit
        or attestation.event_schema != profile.event_schema
        or attestation.runtime_inventory_sha256
        != profile.runtime_inventory_sha256
        or type(attestation.mounts) is not tuple
        or not attestation.mounts
        or any(
            type(mount) is not _legacy.ReadOnlyClosureMount
            for mount in attestation.mounts
        )
        or type(attestation.closure_manifest) is not _legacy.ReadOnlyClosureMount
        or type(attestation.product_lineage) is not _legacy.ReadOnlyClosureMount
    ):
        _blocked("complete P1 engine closure profile is invalid")
    lineage = attestation.product_lineage
    if (
        lineage.target != _PRODUCT_LINEAGE_TARGET
        or lineage.mode != 0o400
        or any(mount.target == _PRODUCT_LINEAGE_TARGET for mount in attestation.mounts)
    ):
        _blocked("P1 product lineage attestation is invalid")
    _validate_native_entry_guard(attestation)
    adapted = _legacy.CompleteEngineClosureAttestation(
        manifest_schema_version=4,
        profile="zero-order",
        source_commit=attestation.source_commit,
        closure_sha256=attestation.closure_sha256,
        mounts=(*attestation.mounts, lineage),
        entrypoint=attestation.entrypoint,
        argv_prefix=attestation.argv_prefix,
        timeout_seconds=attestation.timeout_seconds,
        result_validator_id=attestation.result_validator_id,
        sandbox=attestation.sandbox,
        closure_manifest=attestation.closure_manifest,
    )
    _legacy._validate_closure(adapted, expected_manifest_schema_version=4)
    expected_lineage = (
        json.dumps(
            {
                "closure_sha256": attestation.closure_sha256,
                "engine_version": attestation.engine_version,
                "event_schema": attestation.event_schema,
                "profile": attestation.profile,
                "profile_manifest_schema_version": attestation.manifest_schema_version,
                "runtime_family": attestation.runtime_family,
                "runtime_inventory_sha256": attestation.runtime_inventory_sha256,
            },
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("ascii")
    if _legacy._verified_closure_file(lineage) != expected_lineage:
        _blocked("P1 product lineage authority is invalid")
    return adapted


class P1EngineSpawnProvider:
    """Schema-8 P1 authority using the unchanged legacy spawn implementation."""

    def __init__(
        self,
        *,
        transport_root: Path,
        attest_closure: Callable[[], P1EngineClosureAttestation],
        expected_manifest_schema_version: int,
        profile_policy: EngineProfilePolicy,
        attest_inputs: Callable[
            [RunBacktest | RunBacktestSimulation | ValidatePaperCompatibility],
            tuple[_legacy.HashBoundEngineInput, ...],
        ]
        | None = None,
        monotonic_ns: Callable[[], int],
    ) -> None:
        if expected_manifest_schema_version != 8:
            raise ValueError("P1 closure manifest schema must be exactly 8")
        if profile_policy is not P1_REAL_BACKTEST_POLICY:
            raise ValueError("exact code-owned P1 engine profile policy is required")
        self._provider = _legacy.EngineSpawnProvider(
            transport_root=transport_root,
            attest_closure=lambda: _adapt_p1_closure(attest_closure()),
            expected_manifest_schema_version=4,
            attest_inputs=attest_inputs,
            monotonic_ns=monotonic_ns,
        )

    def prepare(self, envelope: EngineCommandEnvelope) -> _legacy.PreparedEngineSpawn:
        if (
            type(envelope) is not EngineCommandEnvelope
            or type(envelope.payload) is not RunBacktest
        ):
            raise EngineSpawnError(
                "ENGINE_REQUEST_INVALID",
                "P1 engine command does not match the code-owned profile",
            )
        return self._provider.prepare(envelope)


def validate_p1_engine_closure_attestation(
    value: object,
) -> P1EngineClosureAttestation:
    """Return the exact schema-8 attestation after the shared spawn validation."""

    _adapt_p1_closure(value)
    assert isinstance(value, P1EngineClosureAttestation)
    return value


def consume_prepared_p1_paper_launch(
    prepared: _legacy.PreparedEngineSpawn,
    closure: P1EngineClosureAttestation,
    request: EngineCommandEnvelope,
) -> P1PaperLaunchAuthority:
    """Consume the existing exact spawn authority into one paper-only launch."""

    exact = validate_p1_engine_closure_attestation(closure)
    if (
        type(request) is not EngineCommandEnvelope
        or type(request.payload) is not RunBacktest
    ):
        raise EngineSpawnError(
            "ENGINE_REQUEST_INVALID", "exact P1 paper request is required"
        )
    built = _legacy.consume_prepared_engine_spawn(prepared)
    snapshots: tuple[tuple[str, str, int], ...] = ()
    try:
        request_sha256 = hashlib.sha256(canonical_json_bytes(request)).hexdigest()
        if (
            built.cwd != Path("/")
            or dict(built.environment) != {}
            or built.lineage.closure_sha256 != exact.closure_sha256
            or built.lineage.request_sha256 != request_sha256
            or built.argv.count("--proc") != 1
            or built.argv.count("--chdir") != 1
        ):
            _blocked("P1 paper prepared spawn authority is invalid")
        snapshots = _paper_source_snapshots()
        proc_index = built.argv.index("--proc")
        chdir_index = built.argv.index("--chdir")
        if proc_index >= chdir_index or built.argv[chdir_index + 1] != "/":
            _blocked("P1 paper prepared spawn layout is invalid")
        paper_mounts = tuple(
            argument
            for name, _digest, descriptor in snapshots
            for argument in (
                "--perms",
                "0400",
                "--ro-bind-data",
                str(descriptor),
                f"/engine/runtime_v1/{name}",
            )
        )
        argv = (
            *built.argv[:proc_index],
            *paper_mounts,
            *built.argv[proc_index : chdir_index + 2],
            "/usr/bin/python3.12",
            "-I",
            "-S",
            "/engine/runtime_v1/paper_main.py",
            "/inputs/request.json",
            "/inputs/request.sha256",
        )
        paper_fds = tuple(item[2] for item in snapshots)
        source_sha256 = hashlib.sha256(
            canonical_json_bytes(
                tuple((name, digest) for name, digest, _descriptor in snapshots)
            )
        ).hexdigest()
        if source_sha256 != P1_PAPER_SOURCE_SHA256:
            _blocked("P1 paper source inventory digest is invalid")
        paper_built = replace(
            built,
            argv=argv,
            pass_fds=(*built.pass_fds, *paper_fds),
            close_after_spawn_fds=(
                *built.close_after_spawn_fds,
                *paper_fds,
            ),
        )
        authority = object.__new__(P1PaperLaunchAuthority)
        for name, value in (
            ("built", paper_built),
            ("closure_sha256", exact.closure_sha256),
            ("request_sha256", request_sha256),
            ("paper_source_sha256", source_sha256),
            ("argv_sha256", hashlib.sha256(canonical_json_bytes(argv)).hexdigest()),
        ):
            object.__setattr__(authority, name, value)
        _ISSUED_PAPER_LAUNCHES.add(authority)
        return authority
    except BaseException:
        for descriptor in (
            *built.close_after_spawn_fds,
            *(item[2] for item in snapshots),
        ):
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise


def is_issued_p1_paper_launch(value: object) -> bool:
    return type(value) is P1PaperLaunchAuthority and value in _ISSUED_PAPER_LAUNCHES


def claim_p1_paper_launch(
    value: P1PaperLaunchAuthority,
) -> _legacy.EngineBuiltSpawn:
    if not is_issued_p1_paper_launch(value):
        raise EngineSpawnError(
            "ENGINE_PREPARED_SPAWN_INVALID", "P1 paper launch is unavailable"
        )
    _ISSUED_PAPER_LAUNCHES.discard(value)
    return value.built


__all__ = [
    "P1EngineClosureAttestation",
    "P1EngineSpawnProvider",
    "P1PaperLaunchAuthority",
    "P1_PAPER_SOURCE_SHA256",
    "claim_p1_paper_launch",
    "consume_prepared_p1_paper_launch",
    "is_issued_p1_paper_launch",
    "validate_p1_engine_closure_attestation",
]
