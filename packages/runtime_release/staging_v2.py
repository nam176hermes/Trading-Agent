"""Candidate-bound staging-only Release Authority v2 primitives.

This module is projected byte-for-byte into the immutable paper application.
It never enables production authority. The default path remains unavailable;
callers must provide explicit Package 6 staging authority and activation files.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import errno
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping, Sequence


STAGING_SCOPE = "PACKAGE6_STAGING_ONLY"
STAGING_SCOPE_ENV = "TRADING_PACKAGE6_STAGING_SCOPE"
STAGING_AUTHORITY_PATH_ENV = "TRADING_PACKAGE6_STAGING_AUTHORITY_PATH"
STAGING_ACTIVATION_PATH_ENV = "TRADING_PACKAGE6_STAGING_ACTIVATION_PATH"
PACKAGE6_APPROVAL_SHA256_ENV = "TRADING_PACKAGE6_APPROVAL_SHA256"
AUTHORITY_KIND = "PACKAGE6_STAGING_RELEASE_AUTHORITY_V2"
ACTIVATION_KIND = "PACKAGE6_STAGING_RELEASE_ACTIVATION_V2"
SCHEMA_VERSION = 1
MAX_AUTHORITY_BYTES = 32 * 1024 * 1024
MAX_DYNAMIC_BYTES = 8192
MAX_AUTHORITY_WINDOW = timedelta(minutes=30)

_SHA256 = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_CONSTRAINTS = {
    "live_execution_approved": False,
    "live_trading_approved": False,
    "live_trading_enabled": False,
    "network_policy": "LOOPBACK_ONLY",
    "persistent_services_allowed": False,
    "systemd_allowed": False,
}
_RUNTIME_SUFFIXES = {
    "artifact_root": ("runtime", "artifacts"),
    "reports_root": ("runtime", "reports"),
    "safety_snapshot": ("runtime", "safety-state.json"),
    "scratch_root": ("runtime", "scratch"),
    "semantic_authority": ("semantic", "active.json"),
    "semantic_input_root": ("semantic", "input"),
    "signals_root": ("runtime", "signals"),
}


class StagingAuthorityError(RuntimeError):
    """Sanitized fail-closed staging authority failure."""


@dataclass(frozen=True, slots=True)
class StagingAuthorityMaterial:
    scope: str
    source_commit: str
    source_tree: str
    disposable_root: Path
    installation_root: Path
    application_root: Path
    backend_root: Path
    application_python: Path
    backend_python: Path
    application_python_sha256: str
    backend_python_sha256: str
    application_artifact_sha256: str
    backend_artifact_sha256: str
    production_release_authority_sha256: str
    stage_file_set_sha256: str
    command_manifest: Mapping[str, object]
    command_authority_sha256: str
    runtime_paths: Mapping[str, Path]
    safety_snapshot_sha256: str
    safety_exporter_commit: str
    safety_source_fingerprint: str
    semantic_active_authority_sha256: str
    semantic_version_manifest_sha256: str
    semantic_input_fingerprint: str
    semantic_manifest_version: str
    semantic_generated_at: datetime
    semantic_expires_at: datetime
    semantic_policy_sha256: str
    package6_approval_sha256: str
    authority_path: Path
    authority_identity: tuple[int, int]
    authority_sha256: str
    activation_path: Path
    activation_binding_sha256: str
    activation_sha256: str
    authority_generated_at: datetime
    authority_expires_at: datetime
    stage_entries: tuple[Mapping[str, object], ...]

    @property
    def authority_pin(self) -> tuple[object, ...]:
        return (
            self.authority_identity,
            self.authority_sha256,
            self.activation_binding_sha256,
            self.source_commit,
            self.source_tree,
            self.stage_file_set_sha256,
            self.package6_approval_sha256,
        )

    @property
    def dynamic_evidence_pin(self) -> tuple[object, ...]:
        return (
            self.safety_snapshot_sha256,
            self.semantic_active_authority_sha256,
            self.activation_sha256,
        )


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical_digest(value: object) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def command_manifest_for_stage(stage: Path) -> dict[str, object]:
    backend = stage / "backend"
    backend_python = backend / ".venv/bin/python3.11"
    commands = [
        {
            "argv": [str(backend_python), "-I", "-B", "paper_main.py"],
            "cwd": str(backend),
            "environment_policy": "CANONICAL_PAPER_CHILD_V1",
            "executable": str(backend_python),
            "job_type": "SNAPSHOT",
            "shell": False,
        }
    ]
    encoded = json.dumps(
        commands,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "commands": commands,
        "manifest_sha256": sha256_bytes(encoded),
        "schema_version": 3,
    }


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _exact(value: object, fields: set[str]) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError
    return value


def _digest(value: object) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError
    return value


def _commit(value: object) -> str:
    if not isinstance(value, str) or _COMMIT.fullmatch(value) is None:
        raise ValueError
    return value


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or _TIMESTAMP.fullmatch(value) is None:
        raise ValueError
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    return parsed.astimezone(UTC)


def _timestamp_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError
    return value.astimezone(UTC).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _canonical_absolute(value: object) -> Path:
    if not isinstance(value, str):
        raise ValueError
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError
    if Path(os.path.abspath(path)) != path:
        raise ValueError
    return path


def _is_relative(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _validate_private_root(root: Path) -> None:
    if not _is_relative(root, Path("/tmp")) or root == Path("/tmp"):
        raise ValueError
    info = root.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o700
        or info.st_mode & 0o7000
    ):
        raise ValueError
    current = Path("/tmp")
    tmp_info = current.lstat()
    filesystem_root_uid = Path("/").lstat().st_uid
    if (
        not stat.S_ISDIR(tmp_info.st_mode)
        or stat.S_ISLNK(tmp_info.st_mode)
        or tmp_info.st_uid != filesystem_root_uid
        or stat.S_IMODE(tmp_info.st_mode) != 0o1777
    ):
        raise ValueError
    for part in root.relative_to("/tmp").parts[:-1]:
        current /= part
        info = current.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & (0o022 | 0o7000)
        ):
            raise ValueError


def _safe_read(path: Path, *, max_bytes: int, mode: int) -> tuple[bytes, tuple[int, int]]:
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != mode
            or before.st_mode & 0o7000
        ):
            raise ValueError
        chunks: list[bytes] = []
        observed = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, max_bytes + 1 - observed))
            if not chunk:
                break
            chunks.append(chunk)
            observed += len(chunk)
            if observed > max_bytes:
                raise ValueError
        after = os.fstat(descriptor)
        current = path.lstat()
        identity = (before.st_dev, before.st_ino)
        if (
            identity != (after.st_dev, after.st_ino)
            or identity != (current.st_dev, current.st_ino)
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
        ):
            raise ValueError
        return b"".join(chunks), identity
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise ValueError from exc
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _load_canonical(path: Path, *, max_bytes: int, mode: int) -> tuple[dict[str, object], bytes, tuple[int, int]]:
    raw, identity = _safe_read(path, max_bytes=max_bytes, mode=mode)
    document = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
    if not isinstance(document, dict) or raw != canonical_json_bytes(document):
        raise ValueError
    return document, raw, identity


def _entry(value: object) -> dict[str, object]:
    item = _exact(value, {"mode", "path", "sha256", "size", "type"})
    relative = item["path"]
    if (
        not isinstance(relative, str)
        or not relative
        or relative.startswith("/")
        or ".." in Path(relative).parts
        or Path(relative).as_posix() != relative
    ):
        raise ValueError
    mode = item["mode"]
    if mode not in {"0444", "0555"}:
        raise ValueError
    kind = item["type"]
    if kind not in {"directory", "file"}:
        raise ValueError
    if type(item["size"]) is not int or item["size"] < 0:
        raise ValueError
    _digest(item["sha256"])
    if kind == "directory" and (
        mode != "0555"
        or item["size"] != 0
        or item["sha256"] != sha256_bytes(b"")
    ):
        raise ValueError
    return dict(item)


def walk_sealed_stage(stage: Path) -> list[dict[str, object]]:
    info = stage.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o555
    ):
        raise ValueError
    entries: list[dict[str, object]] = []
    pending = [stage]
    while pending:
        directory = pending.pop()
        children = sorted(
            directory.iterdir(),
            key=lambda item: os.fsencode(item.relative_to(stage).as_posix()),
            reverse=True,
        )
        for path in children:
            child_info = path.lstat()
            relative = path.relative_to(stage).as_posix()
            if stat.S_ISLNK(child_info.st_mode) or child_info.st_uid != os.geteuid():
                raise ValueError
            mode = stat.S_IMODE(child_info.st_mode)
            if stat.S_ISDIR(child_info.st_mode):
                if mode != 0o555:
                    raise ValueError
                entry = {
                    "mode": "0555",
                    "path": relative,
                    "sha256": sha256_bytes(b""),
                    "size": 0,
                    "type": "directory",
                }
                pending.append(path)
            elif stat.S_ISREG(child_info.st_mode):
                if mode not in {0o444, 0o555}:
                    raise ValueError
                raw, current_identity = _safe_read(
                    path,
                    max_bytes=max(child_info.st_size + 1, 1),
                    mode=mode,
                )
                if current_identity != (child_info.st_dev, child_info.st_ino):
                    raise ValueError
                entry = {
                    "mode": f"{mode:04o}",
                    "path": relative,
                    "sha256": sha256_bytes(raw),
                    "size": len(raw),
                    "type": "file",
                }
            else:
                raise ValueError
            entries.append(entry)
    entries.sort(key=lambda item: os.fsencode(str(item["path"])))
    return entries


def component_digest(
    entries: Sequence[Mapping[str, object]], prefix: str
) -> str:
    selected = [
        dict(item)
        for item in entries
        if item["path"] == prefix or str(item["path"]).startswith(prefix + "/")
    ]
    if not selected:
        raise ValueError
    return canonical_digest(selected)


def _validate_validity(value: object, *, now: datetime) -> tuple[datetime, datetime]:
    validity = _exact(value, {"expires_at_utc", "generated_at_utc"})
    generated = _timestamp(validity["generated_at_utc"])
    expires = _timestamp(validity["expires_at_utc"])
    if (
        generated > now
        or now >= expires
        or expires <= generated
        or expires - generated > MAX_AUTHORITY_WINDOW
    ):
        raise ValueError
    return generated, expires


def _validate_constraints(value: object) -> None:
    if value != _CONSTRAINTS:
        raise ValueError


def _validate_runtime_paths(value: object, *, disposable_root: Path) -> dict[str, Path]:
    document = _exact(value, set(_RUNTIME_SUFFIXES))
    paths: dict[str, Path] = {}
    for name, suffix in _RUNTIME_SUFFIXES.items():
        path = _canonical_absolute(document[name])
        if path != disposable_root.joinpath(*suffix):
            raise ValueError
        paths[name] = path
    for name in ("artifact_root", "reports_root", "scratch_root", "signals_root"):
        info = paths[name].lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_ISLNK(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            raise ValueError
    info = paths["semantic_input_root"].lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or stat.S_ISLNK(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o711
    ):
        raise ValueError
    return paths


def _validate_static_authority(
    document: object,
    *,
    now: datetime,
) -> tuple[dict[str, object], dict[str, Any]]:
    authority = _exact(
        document,
        {
            "authority_kind",
            "command_manifest",
            "components",
            "constraints",
            "disposable_root",
            "installation_root",
            "interpreters",
            "production_release_authority_sha256",
            "runtime_paths",
            "schema_version",
            "scope",
            "source",
            "stage",
            "validity",
        },
    )
    if (
        authority["authority_kind"] != AUTHORITY_KIND
        or authority["schema_version"] != SCHEMA_VERSION
        or authority["scope"] != STAGING_SCOPE
    ):
        raise ValueError
    _validate_constraints(authority["constraints"])
    generated, expires = _validate_validity(authority["validity"], now=now)
    disposable_root = _canonical_absolute(authority["disposable_root"])
    _validate_private_root(disposable_root)
    installation_root = _canonical_absolute(authority["installation_root"])
    if installation_root != disposable_root / "stage":
        raise ValueError
    source = _exact(authority["source"], {"commit", "tree"})
    commit = _commit(source["commit"])
    tree = _commit(source["tree"])
    production_release = _digest(authority["production_release_authority_sha256"])

    stage = _exact(authority["stage"], {"entries", "file_set_sha256"})
    raw_entries = stage["entries"]
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ValueError
    entries = [_entry(item) for item in raw_entries]
    if entries != sorted(entries, key=lambda item: os.fsencode(str(item["path"]))):
        raise ValueError
    if stage["file_set_sha256"] != canonical_digest(entries):
        raise ValueError
    observed = walk_sealed_stage(installation_root)
    if not hmac.compare_digest(canonical_json_bytes(entries), canonical_json_bytes(observed)):
        raise ValueError

    components = _exact(authority["components"], {"application", "backend"})
    component_values: dict[str, str] = {}
    for name in ("application", "backend"):
        component = _exact(components[name], {"artifact_root", "artifact_set_sha256"})
        if component["artifact_root"] != name:
            raise ValueError
        digest = _digest(component["artifact_set_sha256"])
        if digest != component_digest(entries, name):
            raise ValueError
        component_values[name] = digest

    interpreters = _exact(authority["interpreters"], {"application", "backend"})
    interpreter_values: dict[str, tuple[Path, str]] = {}
    for name in ("application", "backend"):
        interpreter = _exact(interpreters[name], {"path", "sha256"})
        path = _canonical_absolute(interpreter["path"])
        expected = installation_root / name / ".venv/bin/python3.11"
        digest = _digest(interpreter["sha256"])
        if path != expected:
            raise ValueError
        raw, _identity = _safe_read(path, max_bytes=128 * 1024 * 1024, mode=0o555)
        if not hmac.compare_digest(sha256_bytes(raw), digest):
            raise ValueError
        interpreter_values[name] = (path, digest)

    command_manifest = _exact(
        authority["command_manifest"],
        {"commands", "manifest_sha256", "schema_version"},
    )
    if command_manifest != command_manifest_for_stage(installation_root):
        raise ValueError
    runtime_paths = _validate_runtime_paths(
        authority["runtime_paths"], disposable_root=disposable_root
    )
    return authority, {
        "application_artifact_sha256": component_values["application"],
        "application_python": interpreter_values["application"][0],
        "application_python_sha256": interpreter_values["application"][1],
        "backend_artifact_sha256": component_values["backend"],
        "backend_python": interpreter_values["backend"][0],
        "backend_python_sha256": interpreter_values["backend"][1],
        "command_manifest": dict(command_manifest),
        "commit": commit,
        "disposable_root": disposable_root,
        "entries": entries,
        "expires": expires,
        "generated": generated,
        "installation_root": installation_root,
        "production_release_authority_sha256": production_release,
        "runtime_paths": runtime_paths,
        "stage_file_set_sha256": stage["file_set_sha256"],
        "tree": tree,
    }


def _validate_activation(
    document: object,
    *,
    authority_sha256: str,
    approval_sha256: str,
    now: datetime,
) -> tuple[dict[str, object], dict[str, Any]]:
    activation = _exact(
        document,
        {
            "activation_kind",
            "authority_sha256",
            "constraints",
            "package6_approval_sha256",
            "safety",
            "schema_version",
            "scope",
            "semantic",
            "validity",
        },
    )
    if (
        activation["activation_kind"] != ACTIVATION_KIND
        or activation["schema_version"] != SCHEMA_VERSION
        or activation["scope"] != STAGING_SCOPE
        or activation["authority_sha256"] != authority_sha256
        or activation["package6_approval_sha256"] != approval_sha256
    ):
        raise ValueError
    _validate_constraints(activation["constraints"])
    generated, expires = _validate_validity(activation["validity"], now=now)
    safety = _exact(
        activation["safety"],
        {"exporter_commit", "snapshot_sha256", "source_fingerprint"},
    )
    safety_values = {
        "exporter_commit": _commit(safety["exporter_commit"]),
        "snapshot_sha256": _digest(safety["snapshot_sha256"]),
        "source_fingerprint": _digest(safety["source_fingerprint"]),
    }
    semantic = _exact(
        activation["semantic"],
        {
            "active_authority_sha256",
            "expires_at",
            "generated_at",
            "manifest_version",
            "policy_sha256",
            "semantic_input_fingerprint",
            "version_manifest_sha256",
        },
    )
    manifest_version = semantic["manifest_version"]
    if not isinstance(manifest_version, str) or not manifest_version or len(manifest_version) > 128:
        raise ValueError
    semantic_generated = _timestamp(semantic["generated_at"])
    semantic_expires = _timestamp(semantic["expires_at"])
    if (
        semantic_generated > now
        or now >= semantic_expires
        or semantic_expires <= semantic_generated
        or semantic_generated < generated
        or semantic_expires > expires
    ):
        raise ValueError
    semantic_values = {
        "active_authority_sha256": _digest(semantic["active_authority_sha256"]),
        "expires_at": semantic_expires,
        "generated_at": semantic_generated,
        "manifest_version": manifest_version,
        "policy_sha256": _digest(semantic["policy_sha256"]),
        "semantic_input_fingerprint": _digest(
            semantic["semantic_input_fingerprint"]
        ),
        "version_manifest_sha256": _digest(
            semantic["version_manifest_sha256"]
        ),
    }
    binding = {
        key: activation[key]
        for key in (
            "activation_kind",
            "authority_sha256",
            "constraints",
            "package6_approval_sha256",
            "schema_version",
            "scope",
            "validity",
        )
    }
    return activation, {
        "activation_binding_sha256": canonical_digest(binding),
        "expires": expires,
        "generated": generated,
        "safety": safety_values,
        "semantic": semantic_values,
    }


def _validate_dynamic_files(
    *,
    runtime_paths: Mapping[str, Path],
    safety: Mapping[str, object],
    semantic: Mapping[str, object],
) -> None:
    safety_raw, _identity = _safe_read(
        runtime_paths["safety_snapshot"], max_bytes=MAX_DYNAMIC_BYTES, mode=0o600
    )
    if sha256_bytes(safety_raw) != safety["snapshot_sha256"]:
        raise ValueError
    safety_document = json.loads(safety_raw.decode("utf-8"), object_pairs_hook=_pairs)
    safety_document = _exact(
        safety_document,
        {
            "effective_mode",
            "expires_at",
            "exporter_commit",
            "generated_at",
            "kill_switch_state",
            "live_execution_enabled",
            "live_trading_approved",
            "requested_mode",
            "schema_version",
            "source_fingerprint",
        },
    )
    if (
        safety_document["schema_version"] != 1
        or safety_document["requested_mode"] != "PAPER"
        or safety_document["effective_mode"] != "PAPER"
        or safety_document["live_execution_enabled"] is not False
        or safety_document["live_trading_approved"] is not False
        or safety_document["kill_switch_state"] != "INACTIVE"
        or safety_document["exporter_commit"] != safety["exporter_commit"]
        or safety_document["source_fingerprint"] != safety["source_fingerprint"]
    ):
        raise ValueError
    _timestamp(safety_document["generated_at"])
    _timestamp(safety_document["expires_at"])

    semantic_raw, _identity = _safe_read(
        runtime_paths["semantic_authority"],
        max_bytes=MAX_DYNAMIC_BYTES,
        mode=0o444,
    )
    if sha256_bytes(semantic_raw) != semantic["active_authority_sha256"]:
        raise ValueError


def load_staging_authority_material(
    source: Mapping[str, str] | None = None,
    *,
    now: datetime | None = None,
) -> StagingAuthorityMaterial:
    """Load exact staging authority and activation or fail with no details."""

    environment = os.environ if source is None else source
    current = datetime.now(UTC) if now is None else now.astimezone(UTC)
    try:
        if environment.get(STAGING_SCOPE_ENV) != STAGING_SCOPE:
            raise ValueError
        authority_path = _canonical_absolute(
            environment.get(STAGING_AUTHORITY_PATH_ENV)
        )
        activation_path = _canonical_absolute(
            environment.get(STAGING_ACTIVATION_PATH_ENV)
        )
        approval_sha256 = _digest(
            environment.get(PACKAGE6_APPROVAL_SHA256_ENV)
        )
        authority_document, authority_raw, authority_identity = _load_canonical(
            authority_path,
            max_bytes=MAX_AUTHORITY_BYTES,
            mode=0o444,
        )
        authority, static_values = _validate_static_authority(
            authority_document,
            now=current,
        )
        disposable_root = static_values["disposable_root"]
        if (
            authority_path.parent != disposable_root / "authority"
            or activation_path.parent != disposable_root / "authority"
        ):
            raise ValueError
        activation_document, activation_raw, _activation_identity = _load_canonical(
            activation_path,
            max_bytes=MAX_DYNAMIC_BYTES,
            mode=0o444,
        )
        authority_sha256 = sha256_bytes(authority_raw)
        _activation, dynamic_values = _validate_activation(
            activation_document,
            authority_sha256=authority_sha256,
            approval_sha256=approval_sha256,
            now=current,
        )
        if (
            dynamic_values["generated"] != static_values["generated"]
            or dynamic_values["expires"] != static_values["expires"]
        ):
            raise ValueError
        _validate_dynamic_files(
            runtime_paths=static_values["runtime_paths"],
            safety=dynamic_values["safety"],
            semantic=dynamic_values["semantic"],
        )
        semantic = dynamic_values["semantic"]
        safety = dynamic_values["safety"]
        return StagingAuthorityMaterial(
            scope=STAGING_SCOPE,
            source_commit=static_values["commit"],
            source_tree=static_values["tree"],
            disposable_root=disposable_root,
            installation_root=static_values["installation_root"],
            application_root=static_values["installation_root"] / "application",
            backend_root=static_values["installation_root"] / "backend",
            application_python=static_values["application_python"],
            backend_python=static_values["backend_python"],
            application_python_sha256=static_values["application_python_sha256"],
            backend_python_sha256=static_values["backend_python_sha256"],
            application_artifact_sha256=static_values[
                "application_artifact_sha256"
            ],
            backend_artifact_sha256=static_values["backend_artifact_sha256"],
            production_release_authority_sha256=static_values[
                "production_release_authority_sha256"
            ],
            stage_file_set_sha256=static_values["stage_file_set_sha256"],
            command_manifest=static_values["command_manifest"],
            command_authority_sha256=canonical_digest(
                static_values["command_manifest"]
            ),
            runtime_paths=static_values["runtime_paths"],
            safety_snapshot_sha256=safety["snapshot_sha256"],
            safety_exporter_commit=safety["exporter_commit"],
            safety_source_fingerprint=safety["source_fingerprint"],
            semantic_active_authority_sha256=semantic[
                "active_authority_sha256"
            ],
            semantic_version_manifest_sha256=semantic[
                "version_manifest_sha256"
            ],
            semantic_input_fingerprint=semantic["semantic_input_fingerprint"],
            semantic_manifest_version=semantic["manifest_version"],
            semantic_generated_at=semantic["generated_at"],
            semantic_expires_at=semantic["expires_at"],
            semantic_policy_sha256=semantic["policy_sha256"],
            package6_approval_sha256=approval_sha256,
            authority_path=authority_path,
            authority_identity=authority_identity,
            authority_sha256=authority_sha256,
            activation_path=activation_path,
            activation_binding_sha256=dynamic_values[
                "activation_binding_sha256"
            ],
            activation_sha256=sha256_bytes(activation_raw),
            authority_generated_at=static_values["generated"],
            authority_expires_at=static_values["expires"],
            stage_entries=tuple(static_values["entries"]),
        )
    except StagingAuthorityError:
        raise
    except Exception:
        raise StagingAuthorityError("staging authority is unavailable") from None


def attest_staging_material(
    material: object,
    *,
    runtime_python_path: Path,
) -> bool:
    """Recompute immutable stage and interpreter evidence for one authority."""

    try:
        if not isinstance(material, StagingAuthorityMaterial):
            return False
        if runtime_python_path != material.application_python:
            return False
        observed = walk_sealed_stage(material.installation_root)
        if not hmac.compare_digest(
            canonical_json_bytes(observed),
            canonical_json_bytes(list(material.stage_entries)),
        ):
            return False
        if canonical_digest(observed) != material.stage_file_set_sha256:
            return False
        if component_digest(observed, "application") != material.application_artifact_sha256:
            return False
        if component_digest(observed, "backend") != material.backend_artifact_sha256:
            return False
        if command_manifest_for_stage(material.installation_root) != material.command_manifest:
            return False
        return True
    except Exception:
        return False


def build_staging_release_authority_v2(
    stage: Path,
    *,
    source_commit: str,
    source_tree: str,
    disposable_root: Path,
    production_release_authority_sha256: str,
    runtime_paths: Mapping[str, Path],
    generated_at: datetime,
    expires_at: datetime,
) -> tuple[dict[str, object], str]:
    """Build, but do not publish, one staging-only static authority."""

    try:
        commit = _commit(source_commit)
        tree = _commit(source_tree)
        production_release = _digest(production_release_authority_sha256)
        root = _canonical_absolute(str(disposable_root))
        _validate_private_root(root)
        stage = _canonical_absolute(str(stage))
        if stage != root / "stage":
            raise ValueError
        entries = walk_sealed_stage(stage)
        path_document = {name: str(runtime_paths[name]) for name in _RUNTIME_SUFFIXES}
        _validate_runtime_paths(path_document, disposable_root=root)
        app_python = stage / "application/.venv/bin/python3.11"
        backend_python = stage / "backend/.venv/bin/python3.11"
        app_raw, _identity = _safe_read(
            app_python, max_bytes=128 * 1024 * 1024, mode=0o555
        )
        backend_raw, _identity = _safe_read(
            backend_python, max_bytes=128 * 1024 * 1024, mode=0o555
        )
        document: dict[str, object] = {
            "authority_kind": AUTHORITY_KIND,
            "command_manifest": command_manifest_for_stage(stage),
            "components": {
                "application": {
                    "artifact_root": "application",
                    "artifact_set_sha256": component_digest(entries, "application"),
                },
                "backend": {
                    "artifact_root": "backend",
                    "artifact_set_sha256": component_digest(entries, "backend"),
                },
            },
            "constraints": dict(_CONSTRAINTS),
            "disposable_root": str(root),
            "installation_root": str(stage),
            "interpreters": {
                "application": {
                    "path": str(app_python),
                    "sha256": sha256_bytes(app_raw),
                },
                "backend": {
                    "path": str(backend_python),
                    "sha256": sha256_bytes(backend_raw),
                },
            },
            "production_release_authority_sha256": production_release,
            "runtime_paths": path_document,
            "schema_version": SCHEMA_VERSION,
            "scope": STAGING_SCOPE,
            "source": {"commit": commit, "tree": tree},
            "stage": {
                "entries": entries,
                "file_set_sha256": canonical_digest(entries),
            },
            "validity": {
                "expires_at_utc": _timestamp_text(expires_at),
                "generated_at_utc": _timestamp_text(generated_at),
            },
        }
        _validate_static_authority(document, now=generated_at.astimezone(UTC))
        return document, canonical_digest(document)
    except Exception:
        raise StagingAuthorityError("staging authority cannot be built") from None


def build_staging_activation_v2(
    *,
    authority_sha256: str,
    package6_approval_sha256: str,
    safety_snapshot_sha256: str,
    safety_exporter_commit: str,
    safety_source_fingerprint: str,
    semantic_active_authority_sha256: str,
    semantic_version_manifest_sha256: str,
    semantic_input_fingerprint: str,
    semantic_manifest_version: str,
    semantic_policy_sha256: str,
    semantic_generated_at: datetime,
    semantic_expires_at: datetime,
    generated_at: datetime,
    expires_at: datetime,
) -> tuple[dict[str, object], str]:
    """Build the dynamic envelope that binds approval and fresh evidence."""

    try:
        document: dict[str, object] = {
            "activation_kind": ACTIVATION_KIND,
            "authority_sha256": _digest(authority_sha256),
            "constraints": dict(_CONSTRAINTS),
            "package6_approval_sha256": _digest(package6_approval_sha256),
            "safety": {
                "exporter_commit": _commit(safety_exporter_commit),
                "snapshot_sha256": _digest(safety_snapshot_sha256),
                "source_fingerprint": _digest(safety_source_fingerprint),
            },
            "schema_version": SCHEMA_VERSION,
            "scope": STAGING_SCOPE,
            "semantic": {
                "active_authority_sha256": _digest(
                    semantic_active_authority_sha256
                ),
                "expires_at": _timestamp_text(semantic_expires_at),
                "generated_at": _timestamp_text(semantic_generated_at),
                "manifest_version": semantic_manifest_version,
                "policy_sha256": _digest(semantic_policy_sha256),
                "semantic_input_fingerprint": _digest(
                    semantic_input_fingerprint
                ),
                "version_manifest_sha256": _digest(
                    semantic_version_manifest_sha256
                ),
            },
            "validity": {
                "expires_at_utc": _timestamp_text(expires_at),
                "generated_at_utc": _timestamp_text(generated_at),
            },
        }
        _validate_activation(
            document,
            authority_sha256=authority_sha256,
            approval_sha256=package6_approval_sha256,
            now=generated_at.astimezone(UTC),
        )
        return document, canonical_digest(document)
    except Exception:
        raise StagingAuthorityError("staging activation cannot be built") from None


__all__ = [
    "ACTIVATION_KIND",
    "AUTHORITY_KIND",
    "PACKAGE6_APPROVAL_SHA256_ENV",
    "SCHEMA_VERSION",
    "STAGING_ACTIVATION_PATH_ENV",
    "STAGING_AUTHORITY_PATH_ENV",
    "STAGING_SCOPE",
    "STAGING_SCOPE_ENV",
    "StagingAuthorityError",
    "StagingAuthorityMaterial",
    "attest_staging_material",
    "build_staging_activation_v2",
    "build_staging_release_authority_v2",
    "canonical_digest",
    "canonical_json_bytes",
    "command_manifest_for_stage",
    "component_digest",
    "load_staging_authority_material",
    "sha256_bytes",
    "walk_sealed_stage",
]
