"""Fail-closed host check for the official P3 Bubblewrap executor."""

from __future__ import annotations

import os
import hashlib
import json
import resource
from decimal import ROUND_HALF_EVEN, localcontext
import stat
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from packages.alpha_lifecycle.contracts.base import SourceIdentity, parse_contract
from packages.alpha_lifecycle.baseline_campaign import ArtifactStore, _read
from packages.alpha_lifecycle.contracts.execution import BaselineManifest, EvaluationManifest, EnvironmentIdentity, InputSet
from packages.alpha_lifecycle.contracts.results import BaselinePack, EvaluationResult, ReplayReceipt
from packages.alpha_lifecycle.replica_store import ReplicaArtifactStore, retain_replica_outputs
from packages.alpha_lifecycle.replica_validation import validate_replica_result
from packages.alpha_lifecycle.sandbox_policy import SANDBOX_ARGS, FILESYSTEM_ARGS, ENVIRONMENT_ARGS, RO_PATH_FLAG, CHILD_ENTRY, ROOT_READONLY_ARGS
from packages.data_catalog.artifact_store import LocalArtifactStore
from packages.data_contracts import ArtifactRefV1
from packages.engine_contracts.serialization import canonical_json_bytes


class SandboxHeld(RuntimeError):
    """The host cannot prove the required official isolation capability."""


def require_official_sandbox(path: Path = Path("/usr/bin/bwrap")) -> Path:
    try:
        info = path.stat(follow_symlinks=False)
        sealed_inner = (path == Path('/p3/bin/bwrap') and info.st_uid == os.geteuid()
            and stat.S_IMODE(info.st_mode) == 0o500
            and bool(os.statvfs(path).f_flag & os.ST_RDONLY))
    except OSError as error:
        raise SandboxHeld("HELD E_SANDBOX: Bubblewrap is unavailable") from error
    if (
        not path.is_absolute() or not stat.S_ISREG(info.st_mode)
        or (info.st_uid != 0 and not sealed_inner) or info.st_mode & 0o022 or not os.access(path, os.X_OK)
    ):
        raise SandboxHeld("HELD E_SANDBOX: Bubblewrap identity is unsafe")
    return path


class BubblewrapExecutor:
    """Parent-observed, credential-free executor for deterministic P3 replicas."""

    def __init__(
        self, *, store: ArtifactStore, store_root: Path, release_root: Path,
        python: Path, source: SourceIdentity, environment_ref: ArtifactRefV1,
        sandbox_policy_digest: str, bwrap: Path = Path("/usr/bin/bwrap"),
        runtime_mounts: tuple[Path, ...] = (),
    ) -> None:
        self._bwrap = require_official_sandbox(bwrap)
        if (type(runtime_mounts) is not tuple or len(runtime_mounts) != len(set(runtime_mounts))
            or any(not path.is_absolute() or ".." in path.parts or not (path == Path("/p3/python")
                or any(path.is_relative_to(root) and path != root for root in
                    (Path("/lib"), Path("/lib64"), Path("/usr/lib")))) for path in runtime_mounts)):
            raise ValueError("sandbox runtime mounts must be explicit sealed runtime paths")
        self._runtime_mounts = runtime_mounts
        self._store = store
        self._store_root = store_root
        self._release_root = release_root
        self._python = python
        self._source = SourceIdentity.model_validate(source)
        self._environment_ref = ArtifactRefV1.model_validate(environment_ref)
        self._sandbox_policy_digest = sandbox_policy_digest
        if any(not path.is_absolute() for path in (store_root, release_root, python)):
            raise ValueError("sandbox paths must be absolute")

    def read_bytes(self, ref: ArtifactRefV1) -> bytes:
        return self._store.read_bytes(ref)

    def put_bytes(self, value: bytes, *, media_type: str) -> ArtifactRefV1:
        return self._store.put_bytes(value, media_type=media_type)

    def _argv(self, manifest_path: Path, result_path: Path, output_dir: Path, seccomp_fd: int) -> tuple[str, ...]:
        child = self._release_root / CHILD_ENTRY
        return (
            str(self._bwrap), *SANDBOX_ARGS, "--seccomp", str(seccomp_fd),
            *(arg for path in self._runtime_mounts for arg in (RO_PATH_FLAG, str(path), str(path))),
            RO_PATH_FLAG, str(self._release_root), str(self._release_root),
            RO_PATH_FLAG, str(self._store_root), str(self._store_root),
            "--bind", str(output_dir), str(output_dir), *FILESYSTEM_ARGS,
            *ROOT_READONLY_ARGS, "--chdir", str(self._release_root), *ENVIRONMENT_ARGS,
            str(self._python), "-I", "-B", str(child), str(manifest_path),
            str(self._store_root), str(result_path),
        )

    @staticmethod
    def _limits() -> None:
        resource.setrlimit(resource.RLIMIT_CPU, (240, 240))
        resource.setrlimit(resource.RLIMIT_AS, (2_147_483_648, 2_147_483_648))
        resource.setrlimit(resource.RLIMIT_FSIZE, (268_435_456, 268_435_456))
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, 256))
        resource.setrlimit(resource.RLIMIT_NPROC, (16, 16))

    @staticmethod
    def _utc(value: datetime) -> str:
        return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")

    def execute(
        self, manifest_ref: ArtifactRefV1, *, replicate: str,
        logical_trial_id: str, output_dir: Path,
    ) -> ReplayReceipt:
        manifest_ref = ArtifactRefV1.model_validate(manifest_ref)
        try:
            kind = json.loads(self._store.read_bytes(manifest_ref))['schema_version']
            manifest = _read(self._store,manifest_ref,
                BaselineManifest if kind == 'p3-baseline-manifest-v1' else EvaluationManifest)
            input_set = _read(self._store,manifest.input_set_ref,InputSet)
            environment = _read(self._store,input_set.environment_ref,EnvironmentIdentity)
            if (input_set.source != self._source or input_set.environment_ref != self._environment_ref
                or environment.sandbox_policy_digest != self._sandbox_policy_digest):
                raise ValueError('input source or environment binding differs')
            if isinstance(manifest, EvaluationManifest):
                from packages.alpha_lifecycle.publication import validate_evaluation_registration
                validate_evaluation_registration(manifest, store=self._store)
        except (KeyError,TypeError,ValueError,OSError) as error:
            raise SandboxHeld('HELD E_SANDBOX: input contract or binding is invalid') from error
        manifest_path = output_dir / "manifest-ref.json"
        result_path = output_dir / "result.json"
        manifest_path.write_bytes(canonical_json_bytes(manifest_ref))
        os.chmod(manifest_path, 0o600)
        started = datetime.now(UTC)
        from packages.alpha_lifecycle.sandbox_policy import socket_filter_bytes
        from services.job_worker.engine_spawn import _sealed_memfd
        from services.job_worker.engine_spawn_interface import EngineSpawnError
        seccomp_fd = -1
        try:
            seccomp_fd = _sealed_memfd("p3-child-seccomp", socket_filter_bytes(), mode=0o400)
            completed = subprocess.run(
                self._argv(manifest_path, result_path, output_dir, seccomp_fd), env={}, cwd="/",
                pass_fds=(seccomp_fd,),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=300, check=False, preexec_fn=self._limits,
            )
        except (OSError, subprocess.SubprocessError, EngineSpawnError) as error:
            raise SandboxHeld("HELD E_SANDBOX: bounded child execution failed") from error
        finally:
            if seccomp_fd >= 0:
                os.close(seccomp_fd)
        if completed.returncode != 0:
            raise SandboxHeld("HELD E_SANDBOX: child did not produce one valid result")
        descriptor = -1
        try:
            descriptor = os.open(result_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
            before = os.fstat(descriptor)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or not 1 <= before.st_size <= 64 * 1024**2):
                raise ValueError("result must be a bounded regular file")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                raw = stream.read(before.st_size + 1)
            after = os.fstat(descriptor)
            if (len(raw) != before.st_size or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns or before.st_ctime_ns != after.st_ctime_ns):
                raise ValueError("result changed during descriptor read")
        except (OSError, ValueError) as error:
            raise SandboxHeld("HELD E_SANDBOX: child result file is unsafe") from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        try:
            if isinstance(manifest,BaselineManifest):
                result = parse_contract("BaselinePack", raw)
                matches = (isinstance(result, BaselinePack)
                           and result.input_set_ref == manifest.input_set_ref)
            else:
                result = parse_contract("EvaluationResult", raw)
                matches = isinstance(result, EvaluationResult) and result.manifest_ref == manifest_ref
            if not matches or canonical_json_bytes(result) != raw:
                raise ValueError("result differs from the requested manifest")
            if not isinstance(result, (BaselinePack, EvaluationResult)):
                raise ValueError("unsupported result or manifest")
            with localcontext() as context:
                context.prec = 50
                context.rounding = ROUND_HALF_EVEN
                validate_replica_result(
                    result, manifest, self._store,
                    LocalArtifactStore(output_dir / "artifacts"),
                    ReplicaArtifactStore(self._store_root, output_dir / "artifacts"),
                )
        except (KeyError, TypeError, ValueError) as error:
            raise SandboxHeld("HELD E_SANDBOX: child result contract or binding is invalid") from error
        result_ref = self._store.put_bytes(raw, media_type="application/json")
        inventory_digest = retain_replica_outputs(output_dir / "artifacts", self._store, result_ref)
        payload = {
            "schema_version": "p3-replay-receipt-v1", "logical_trial_id": logical_trial_id,
            "replicate": replicate, "manifest_digest": manifest_ref.content_sha256,
            "result_ref": result_ref, "source": self._source,
            "environment_ref": self._environment_ref,
            "sandbox_policy_digest": self._sandbox_policy_digest,
            "started_at": self._utc(started), "completed_at": self._utc(datetime.now(UTC)),
            "process_exit": 0, "network_denied": True,
            "output_inventory_digest": inventory_digest,
        }
        payload["digest"] = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
        return ReplayReceipt.model_validate(payload)


__all__ = ["BubblewrapExecutor", "SandboxHeld", "require_official_sandbox"]
