from __future__ import annotations

import ast
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

import psycopg
import pytest

from packages.job_authority import (
    CatalogEvidence,
    UnsafeCatalogSettingError,
    Violation,
    capture_catalog,
    find_event_chain_violations,
    load_frozen_contract,
)
from tests.jobs._postgres import (
    _upgrade_to_revision,
    disposable_database,
    disposable_red_derivation_database,
)


ROOT = Path(__file__).parents[2]
CONTRACT_PATH = (
    ROOT / "ops/postgres/job-plane-authority/query-contract-v1.json"
)
ACL_REPAIR_PATH = (
    ROOT / "ops/postgres/job-plane-authority/acl-repair-v1.sql"
)
CATALOG_OPERATION_ID = "jobs-authority-catalog-red-v1"
GREEN_CATALOG_OPERATION_ID = "jobs-authority-catalog-green-v1"
GREEN_FORWARD_UPGRADE_OPERATION_ID = "jobs-authority-0007-forward-green-v1"
GREEN_REJECTION_OPERATION_ID = "jobs-authority-0007-rejection-green-v1"
GREEN_CATALOG_DIGEST_REJECTION = (
    "0007 preflight catalog digest does not match review"
)
GREEN_EVENT_CHAIN_REJECTION = "0007 event-chain authority violations are present"
DERIVATION_OPERATION_ID = "jobs-authority-catalog-derivation-red-v1"
EVIDENCE_CAPTURE_OPERATION_ID = (
    "jobs-authority-catalog-evidence-capture-red-v1"
)
EVIDENCE_DERIVATION_OPERATION_ID = (
    "jobs-authority-catalog-evidence-derivation-red-v1"
)
CONTRACT = load_frozen_contract(CONTRACT_PATH)

EXACT_0005_HEAD = "0005_job_plane_role_split"
EXACT_0006_HEAD = "0006_job_transition_database_authority"
EXACT_0007_HEAD = "0007_job_event_chain_authority"
EXACT_HEAD_SQL = "SELECT version_num FROM public.alembic_version"
EVIDENCE_OUTPUT_ENV = "TRADING_TEST_JOB_AUTHORITY_EVIDENCE_OUTPUT_DIR"
MIGRATION_0005_PATH = ROOT / "alembic/versions/0005_job_plane_role_split.py"
MIGRATION_0006_PATH = (
    ROOT / "alembic/versions/0006_job_transition_database_authority.py"
)
REVIEWED_0006_SNAPSHOT_PATH = (
    ROOT / "ops/postgres/job-plane-authority/catalog-0006-v1.snapshot"
)
REVIEWED_0007_SNAPSHOT_PATH = (
    ROOT / "ops/postgres/job-plane-authority/catalog-0007-v1.snapshot"
)
_EVIDENCE_OUTPUT_PREFIX = "job-plane-authority-evidence-"
_EVIDENCE_ARTIFACT_NAMES = (
    "catalog-0006-capture-1.snapshot",
    "catalog-0006-capture-2.snapshot",
    "catalog-0007-derivation-1.snapshot",
    "catalog-0007-derivation-2.snapshot",
    "catalog-0006-v1.snapshot",
    "catalog-0007-v1.snapshot",
)
_EVIDENCE_COMPLETION_NAME = "catalog-evidence-completion-v1.json"
_EVIDENCE_FILENAMES = frozenset(
    (*_EVIDENCE_ARTIFACT_NAMES, _EVIDENCE_COMPLETION_NAME)
)
_HEX40 = re.compile(r"[0-9a-f]{40}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
_OUTPUT_NAME = re.compile(
    rf"{re.escape(_EVIDENCE_OUTPUT_PREFIX)}[A-Za-z0-9][A-Za-z0-9._-]*\Z"
)
_GENERIC_SECRET_MARKER = re.compile(
    rb"(?i)(?<![a-z0-9_])(credential|password|verifier|dsn|uri)(?![a-z0-9_])"
)
_URI_MARKER = re.compile(rb"(?i)(?<![a-z0-9])(?:[a-z][a-z0-9+.-]{1,31})://")
_RUNTIME_MARKERS = (
    b"test-only-",
    b"trading_test_",
    b"pgdata",
    b"pgpassword",
    b"pgpassfile",
    b"pgservice",
    b"pgoptions",
    b"pghost",
    b"pgport",
    b"pguser",
    b"pgdatabase",
    b"database_url",
    b"sqlalchemy_url",
    b"conninfo",
    b"/tmp/phase4-postgres-",
    b"127.0.0.1",
    b"55432",
    b"application_name",
    b"sslcert",
    b"sslkey",
)
_SAFE_ROLE_SETTING_VALUES = {
    "default_transaction_read_only": "on",
    "search_path": "pg_catalog",
    "timezone": "UTC",
}
_REVIEWED_INPUT_FILENAMES = {
    "migration_0005": "alembic/versions/0005_job_plane_role_split.py",
    "migration_0006": (
        "alembic/versions/0006_job_transition_database_authority.py"
    ),
    "query_contract": "ops/postgres/job-plane-authority/query-contract-v1.json",
    "reviewed_sql": "ops/postgres/job-plane-authority/acl-repair-v1.sql",
}


class CatalogEvidenceCollectionError(RuntimeError):
    """Evidence collection stopped before completion publication."""


@dataclass(frozen=True, slots=True)
class _EvidenceCapture:
    head: str
    catalog: CatalogEvidence


@dataclass(frozen=True, slots=True)
class _EvidenceDerivation:
    head: str
    before: CatalogEvidence
    derived: CatalogEvidence
    unchanged: CatalogEvidence


@dataclass(frozen=True, slots=True)
class _EvidenceCollection:
    ordinary: tuple[_EvidenceCapture, _EvidenceCapture]
    derivations: tuple[_EvidenceDerivation, _EvidenceDerivation]


@dataclass(frozen=True, slots=True)
class _EvidenceOutput:
    name: str
    directory_fd: int
    device: int
    inode: int


@dataclass(frozen=True, slots=True)
class _GreenAuthorityCapture:
    head: str
    catalog: CatalogEvidence
    violations: tuple[Violation, ...]


def _stop_collection(message: str) -> None:
    raise CatalogEvidenceCollectionError(message)


def _is_safe_evidence_output_path(path: Path) -> bool:
    raw = str(path)
    return (
        path.is_absolute()
        and raw == os.path.normpath(raw)
        and path.parent == Path("/tmp")
        and _OUTPUT_NAME.fullmatch(path.name) is not None
    )


def _require_evidence_output_environment() -> Path:
    controls = (
        os.environ.get("TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES"),
        os.environ.get("TRADING_TEST_DISPOSABLE_APPROVAL_RECORD"),
        os.environ.get("TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE"),
        os.environ.get(EVIDENCE_OUTPUT_ENV),
    )
    output_path = Path(controls[3]) if controls[3] else None
    if (
        controls[0] != "YES"
        or not controls[1]
        or not controls[1].strip()
        or controls[2] != "DISPOSABLE_PG_RED"
        or output_path is None
        or not _is_safe_evidence_output_path(output_path)
    ):
        pytest.skip(
            "exact disposable PostgreSQL RED evidence authority is not present"
        )
    return output_path


def _open_evidence_output_directory(path: Path) -> _EvidenceOutput:
    if not _is_safe_evidence_output_path(path):
        _stop_collection("evidence output directory is unsafe")

    directory_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    tmp_fd: int | None = None
    output_fd: int | None = None
    try:
        tmp_fd = os.open("/tmp", directory_flags)
        output_fd = os.open(path.name, directory_flags, dir_fd=tmp_fd)
        info = os.fstat(output_fd)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700
            or os.listdir(output_fd)
        ):
            _stop_collection("evidence output directory is unsafe")
        return _EvidenceOutput(
            name=path.name,
            directory_fd=output_fd,
            device=info.st_dev,
            inode=info.st_ino,
        )
    except CatalogEvidenceCollectionError:
        if output_fd is not None:
            os.close(output_fd)
        raise
    except OSError:
        if output_fd is not None:
            os.close(output_fd)
        _stop_collection("evidence output directory is unsafe")
    finally:
        if tmp_fd is not None:
            os.close(tmp_fd)


def _revalidate_evidence_output(output: _EvidenceOutput) -> None:
    directory_flags = (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    tmp_fd: int | None = None
    current_fd: int | None = None
    try:
        retained = os.fstat(output.directory_fd)
        if (
            not stat.S_ISDIR(retained.st_mode)
            or retained.st_uid != os.getuid()
            or stat.S_IMODE(retained.st_mode) != 0o700
            or (retained.st_dev, retained.st_ino)
            != (output.device, output.inode)
        ):
            _stop_collection("evidence output directory identity changed")

        tmp_fd = os.open("/tmp", directory_flags)
        current_fd = os.open(
            output.name,
            directory_flags,
            dir_fd=tmp_fd,
        )
        current = os.fstat(current_fd)
        if (
            not stat.S_ISDIR(current.st_mode)
            or current.st_uid != os.getuid()
            or stat.S_IMODE(current.st_mode) != 0o700
            or (current.st_dev, current.st_ino)
            != (output.device, output.inode)
        ):
            _stop_collection("evidence output directory identity changed")
        if os.listdir(output.directory_fd):
            _stop_collection("evidence output directory is no longer empty")
    except CatalogEvidenceCollectionError:
        raise
    except OSError:
        _stop_collection("evidence output directory could not be revalidated")
    finally:
        if current_fd is not None:
            os.close(current_fd)
        if tmp_fd is not None:
            os.close(tmp_fd)


def _write_all(fd: int, content: bytes) -> None:
    remaining = memoryview(content)
    while remaining:
        written = os.write(fd, remaining)
        if written <= 0:
            _stop_collection("evidence output write failed")
        remaining = remaining[written:]


def _write_exclusive_evidence_file(
    directory_fd: int,
    filename: str,
    content: bytes,
) -> None:
    if filename not in _EVIDENCE_FILENAMES or not isinstance(content, bytes):
        _stop_collection("evidence output filename is unsafe")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    fd: int | None = None
    try:
        fd = os.open(filename, flags, 0o600, dir_fd=directory_fd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            _stop_collection("evidence output file is unsafe")
        os.fchmod(fd, 0o600)
        _write_all(fd, content)
        os.fsync(fd)
    except CatalogEvidenceCollectionError:
        raise
    except OSError:
        _stop_collection("evidence output file could not be created exclusively")
    finally:
        if fd is not None:
            os.close(fd)


def _secret_scan_catalog_bytes(content: bytes) -> None:
    failed = False
    lowered = content.lower()
    if (
        not content
        or b"\r" in content
        or _GENERIC_SECRET_MARKER.search(content) is not None
        or _URI_MARKER.search(content) is not None
        or any(marker in lowered for marker in _RUNTIME_MARKERS)
    ):
        failed = True
    try:
        text = content.decode("utf-8", errors="strict")
        lines = text[:-1].split("\n") if text.endswith("\n") else []
        if not lines or any(not line for line in lines):
            failed = True
        for line in lines:
            record = json.loads(line)
            if not isinstance(record, dict):
                failed = True
                continue
            if record.get("kind") == "role_setting":
                key = record.get("key")
                value = record.get("value")
                if (
                    not isinstance(key, str)
                    or key not in _SAFE_ROLE_SETTING_VALUES
                    or value != _SAFE_ROLE_SETTING_VALUES.get(key)
                ):
                    failed = True
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        failed = True
    if failed:
        _stop_collection("catalog evidence failed secret scan")


def _validate_catalog_evidence(evidence: CatalogEvidence) -> None:
    if not isinstance(evidence, CatalogEvidence):
        _stop_collection("catalog evidence has an invalid record shape")
    content = evidence.canonical_bytes
    if (
        evidence.query_id != CONTRACT.catalog_query_id
        or not isinstance(content, bytes)
        or not content.endswith(b"\n")
        or content.endswith(b"\n\n")
        or evidence.row_count <= 0
        or evidence.row_count != content.count(b"\n")
        or evidence.sha256 != hashlib.sha256(content).hexdigest()
        or _HEX64.fullmatch(evidence.sha256) is None
    ):
        _stop_collection("catalog evidence has invalid canonical metadata")
    lines = content[:-1].split(b"\n")
    if any(not line for line in lines) or lines != sorted(lines):
        _stop_collection("catalog evidence is not canonically sorted")
    _secret_scan_catalog_bytes(content)


def _require_exact_0006_head(connection: object) -> str:
    rows = connection.execute(EXACT_HEAD_SQL).fetchall()  # type: ignore[attr-defined]
    if rows != [(EXACT_0006_HEAD,)]:
        _stop_collection("catalog evidence database is not at exact 0006 head")
    return EXACT_0006_HEAD


def _validate_evidence_collection(collection: _EvidenceCollection) -> None:
    if len(collection.ordinary) != 2 or len(collection.derivations) != 2:
        _stop_collection("catalog evidence requires exactly two workflows")
    for capture in collection.ordinary:
        if capture.head != EXACT_0006_HEAD:
            _stop_collection("ordinary catalog evidence head is invalid")
        _validate_catalog_evidence(capture.catalog)
    baseline = collection.ordinary[0].catalog
    if collection.ordinary[1].catalog != baseline:
        _stop_collection("independent 0006 catalog captures differ")

    for derivation in collection.derivations:
        if derivation.head != EXACT_0006_HEAD:
            _stop_collection("derived catalog evidence head is invalid")
        for evidence in (
            derivation.before,
            derivation.derived,
            derivation.unchanged,
        ):
            _validate_catalog_evidence(evidence)
        if derivation.before != baseline:
            _stop_collection("derived catalog baseline differs from reviewed 0006")
        if derivation.unchanged != derivation.before:
            _stop_collection("reviewed catalog derivation did not roll back")
    derived = collection.derivations[0].derived
    if collection.derivations[1].derived != derived:
        _stop_collection("independent derived catalog captures differ")
    if derived == baseline or derived.canonical_bytes == baseline.canonical_bytes:
        _stop_collection("derived catalog does not differ from 0006")


def _collect_catalog_evidence() -> _EvidenceCollection:
    ordinary: list[_EvidenceCapture] = []
    for _capture_number in range(2):
        with disposable_database(
            operation_id=EVIDENCE_CAPTURE_OPERATION_ID,
        ) as owner:
            _upgrade_to_revision(owner, EXACT_0006_HEAD)
            with psycopg.connect(owner.conninfo()) as connection:
                head = _require_exact_0006_head(connection)
                catalog = capture_catalog(connection, CONTRACT)
                ordinary.append(_EvidenceCapture(head, catalog))

    if ordinary[0].catalog != ordinary[1].catalog:
        _stop_collection("independent 0006 catalog captures differ")
    reviewed_baseline = ordinary[0].catalog

    derivations: list[_EvidenceDerivation] = []
    for _derivation_number in range(2):
        with disposable_red_derivation_database(
            operation_id=EVIDENCE_DERIVATION_OPERATION_ID,
            red_sql_file=ACL_REPAIR_PATH,
        ) as workflow:
            connection = workflow.database
            head = _require_exact_0006_head(connection)
            before = capture_catalog(connection, CONTRACT)
            if before != reviewed_baseline:
                _stop_collection(
                    "derived catalog baseline differs from reviewed 0006"
                )
            with connection.transaction(force_rollback=True):
                workflow.execute_reviewed_sql()
                derived = capture_catalog(connection, CONTRACT)
            unchanged = capture_catalog(connection, CONTRACT)
            if unchanged != before:
                _stop_collection("reviewed catalog derivation did not roll back")
            derivations.append(
                _EvidenceDerivation(head, before, derived, unchanged)
            )

    collection = _EvidenceCollection(
        ordinary=(ordinary[0], ordinary[1]),
        derivations=(derivations[0], derivations[1]),
    )
    _validate_evidence_collection(collection)
    return collection


def _validated_artifact_contents(
    collection: _EvidenceCollection,
) -> dict[str, bytes]:
    _validate_evidence_collection(collection)
    baseline = collection.ordinary[0].catalog.canonical_bytes
    derived = collection.derivations[0].derived.canonical_bytes
    contents = (
        collection.ordinary[0].catalog.canonical_bytes,
        collection.ordinary[1].catalog.canonical_bytes,
        collection.derivations[0].derived.canonical_bytes,
        collection.derivations[1].derived.canonical_bytes,
        baseline,
        derived,
    )
    return dict(zip(_EVIDENCE_ARTIFACT_NAMES, contents, strict=True))


def _git_output(*arguments: str) -> bytes:
    try:
        completed = subprocess.run(
            ("git", *arguments),
            cwd=ROOT,
            check=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        _stop_collection("clean source identity could not be resolved")
    return completed.stdout


def _resolve_clean_source_identity() -> tuple[str, str]:
    if _git_output("status", "--porcelain=v1", "--untracked-files=all"):
        _stop_collection("evidence collection requires a clean source tree")
    try:
        commit = _git_output("rev-parse", "--verify", "HEAD").decode("ascii").strip()
        tree = _git_output(
            "rev-parse", "--verify", f"{commit}^{{tree}}"
        ).decode("ascii").strip()
    except UnicodeDecodeError:
        _stop_collection("clean source identity could not be resolved")
    if _HEX40.fullmatch(commit) is None or _HEX40.fullmatch(tree) is None:
        _stop_collection("clean source identity could not be resolved")
    return commit, tree


def _sha256_source_file(path: Path) -> str:
    if path.is_symlink():
        _stop_collection("frozen evidence input is not a regular source file")
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(ROOT.resolve(strict=True))
        info = resolved.stat()
        content = resolved.read_bytes()
    except (OSError, ValueError):
        _stop_collection("frozen evidence input is not a regular source file")
    if not stat.S_ISREG(info.st_mode):
        _stop_collection("frozen evidence input is not a regular source file")
    return hashlib.sha256(content).hexdigest()


def _frozen_input_metadata() -> dict[str, dict[str, str]]:
    paths = {
        "migration_0005": MIGRATION_0005_PATH,
        "migration_0006": MIGRATION_0006_PATH,
        "query_contract": CONTRACT_PATH,
        "reviewed_sql": ACL_REPAIR_PATH,
    }
    return {
        label: {
            "filename": path.relative_to(ROOT).as_posix(),
            "sha256": _sha256_source_file(path),
        }
        for label, path in paths.items()
    }


def _file_metadata(filename: str, content: bytes) -> dict[str, object]:
    return {
        "filename": filename,
        "mode": "0600",
        "sha256": hashlib.sha256(content).hexdigest(),
        "size": len(content),
    }


def _catalog_metadata(evidence: CatalogEvidence) -> dict[str, object]:
    return {"row_count": evidence.row_count, "sha256": evidence.sha256}


def _build_completion_metadata(
    collection: _EvidenceCollection,
    *,
    source_commit: str,
    source_tree: str,
    input_metadata: dict[str, dict[str, str]],
    artifacts: dict[str, bytes],
) -> dict[str, object]:
    _validate_evidence_collection(collection)
    if (
        _HEX40.fullmatch(source_commit) is None
        or _HEX40.fullmatch(source_tree) is None
        or set(input_metadata)
        != {"migration_0005", "migration_0006", "query_contract", "reviewed_sql"}
        or tuple(artifacts) != _EVIDENCE_ARTIFACT_NAMES
    ):
        _stop_collection("completion metadata inputs are invalid")
    for label, item in input_metadata.items():
        if (
            set(item) != {"filename", "sha256"}
            or item["filename"] != _REVIEWED_INPUT_FILENAMES[label]
            or Path(item["filename"]).is_absolute()
            or ".." in Path(item["filename"]).parts
            or _HEX64.fullmatch(item["sha256"]) is None
        ):
            _stop_collection("completion metadata inputs are invalid")

    baseline = collection.ordinary[0].catalog
    derived = collection.derivations[0].derived
    ordinary_records = [
        {
            "capture": number,
            "filename": _EVIDENCE_ARTIFACT_NAMES[number - 1],
            "head": capture.head,
            **_catalog_metadata(capture.catalog),
        }
        for number, capture in enumerate(collection.ordinary, start=1)
    ]
    derivation_records = [
        {
            "capture": number,
            "head": derivation.head,
            "before": _catalog_metadata(derivation.before),
            "derived": {
                "filename": _EVIDENCE_ARTIFACT_NAMES[number + 1],
                **_catalog_metadata(derivation.derived),
            },
            "unchanged_after_rollback": _catalog_metadata(
                derivation.unchanged
            ),
        }
        for number, derivation in enumerate(collection.derivations, start=1)
    ]
    return {
        "captures": {
            "catalog_0006": ordinary_records,
            "catalog_0007_derivations": derivation_records,
        },
        "expected_head": EXACT_0006_HEAD,
        "files": [
            _file_metadata(filename, artifacts[filename])
            for filename in _EVIDENCE_ARTIFACT_NAMES
        ],
        "inputs": input_metadata,
        "proofs": {
            "catalog_0006_bytes_equal": (
                collection.ordinary[0].catalog.canonical_bytes
                == collection.ordinary[1].catalog.canonical_bytes
            ),
            "catalog_0007_bytes_equal": (
                collection.derivations[0].derived.canonical_bytes
                == collection.derivations[1].derived.canonical_bytes
            ),
            "catalog_0007_differs_from_0006": (
                derived.canonical_bytes != baseline.canonical_bytes
            ),
            "derivation_pre_matches_0006": [
                item.before.canonical_bytes == baseline.canonical_bytes
                for item in collection.derivations
            ],
            "rollback_unchanged": [
                item.unchanged.canonical_bytes == item.before.canonical_bytes
                for item in collection.derivations
            ],
            "secret_scan_passed": True,
        },
        "query_id": CONTRACT.catalog_query_id,
        "record_kind": "JOB_AUTHORITY_CATALOG_EVIDENCE_COMPLETION",
        "schema_version": 1,
        "source": {"commit": source_commit, "tree": source_tree},
    }


def _canonical_completion_json(document: dict[str, object]) -> bytes:
    try:
        canonical = (
            json.dumps(
                document,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("ascii")
        if json.loads(canonical) != document or b"\r" in canonical:
            _stop_collection("completion metadata is not strict canonical JSON")
        return canonical
    except (TypeError, ValueError, UnicodeError):
        _stop_collection("completion metadata is not strict canonical JSON")


def _emit_evidence(
    output: _EvidenceOutput,
    artifacts: dict[str, bytes],
    completion: bytes,
) -> None:
    if tuple(artifacts) != _EVIDENCE_ARTIFACT_NAMES:
        _stop_collection("evidence artifact set is incomplete")
    _revalidate_evidence_output(output)
    for filename, content in artifacts.items():
        _write_exclusive_evidence_file(
            output.directory_fd,
            filename,
            content,
        )
    _write_exclusive_evidence_file(
        output.directory_fd,
        _EVIDENCE_COMPLETION_NAME,
        completion,
    )
    try:
        os.fsync(output.directory_fd)
    except OSError:
        _stop_collection("evidence output directory sync failed")


def test_capture_reviewed_catalog_evidence_for_operator_review() -> None:
    output_path = _require_evidence_output_environment()
    output = _open_evidence_output_directory(output_path)
    try:
        source_before = _resolve_clean_source_identity()
        input_metadata = _frozen_input_metadata()
        collection = _collect_catalog_evidence()
        artifacts = _validated_artifact_contents(collection)
        source_after = _resolve_clean_source_identity()
        if source_after != source_before:
            _stop_collection("source identity changed during evidence collection")
        metadata = _build_completion_metadata(
            collection,
            source_commit=source_before[0],
            source_tree=source_before[1],
            input_metadata=input_metadata,
            artifacts=artifacts,
        )
        completion = _canonical_completion_json(metadata)
        _emit_evidence(output, artifacts, completion)
    finally:
        os.close(output.directory_fd)

NAMED_ROLES = {
    "trading_owner",
    "trading_migrator",
    "trading_reader",
    "trading_jobs",
    "trading_job_api",
    "trading_job_worker",
    "trading_job_scheduler",
}
SAFE_ROLE_SETTING_KEYS = {
    "default_transaction_read_only",
    "search_path",
    "timezone",
}
SAFE_FUNCTION_SETTING_KEYS = {"search_path"}


def _quoted_literals(source: str) -> list[str]:
    return re.findall(r"'([^']+)'", source)


def _catalog_vocabularies(sql: str) -> tuple[list[str], list[list[str]], list[str]]:
    named_role_match = re.search(
        r"named_roles\(role_name\) AS \(\s*VALUES(?P<body>.*?)\n\),\n"
        r"application_namespaces AS",
        sql,
        flags=re.DOTALL,
    )
    assert named_role_match is not None
    role_key_blocks = re.findall(
        r"(?:pg_catalog\.lower\(\s*)?"
        r"(?:pg_catalog\.split_part\(setting_entry\.entry, '=', 1\)|"
        r"setting_entry\.setting_key)\s*\)?\s+"
        r"(?:NOT\s+)?IN\s*\((.*?)\)",
        sql,
        flags=re.DOTALL,
    )
    function_block_match = re.search(
        r"function_unsafe_settings AS \((?P<body>.*?)\n\),\nrecords",
        sql,
        flags=re.DOTALL,
    )
    assert function_block_match is not None
    function_keys = re.findall(
        r"split_part\(setting_entry\.entry, '=', 1\) <> '([^']+)'",
        function_block_match.group("body"),
    )
    return (
        _quoted_literals(named_role_match.group("body")),
        [_quoted_literals(block) for block in role_key_blocks],
        function_keys,
    )


def _assert_catalog_vocabularies_closed(sql: str) -> None:
    roles, role_key_policies, function_keys = _catalog_vocabularies(sql)
    assert len(roles) == 7
    assert set(roles) == NAMED_ROLES
    assert len(role_key_policies) == 3
    assert all(len(keys) == 3 for keys in role_key_policies)
    assert all(set(keys) == SAFE_ROLE_SETTING_KEYS for keys in role_key_policies)
    assert len(function_keys) == 1
    assert set(function_keys) == SAFE_FUNCTION_SETTING_KEYS


def _require_green_upgrade_authority() -> None:
    if (
        os.environ.get("TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES") != "YES"
        or not os.environ.get("TRADING_TEST_DISPOSABLE_APPROVAL_RECORD", "").strip()
        or os.environ.get("TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE")
        != "DISPOSABLE_PG_GREEN"
    ):
        pytest.skip("exact disposable PostgreSQL GREEN authority is not present")


def _upgrade_exactly_through_0007(owner: object) -> None:
    for revision in (EXACT_0005_HEAD, EXACT_0006_HEAD, EXACT_0007_HEAD):
        _upgrade_to_revision(owner, revision)


def _upgrade_exactly_through_0006(owner: object) -> None:
    for revision in (EXACT_0005_HEAD, EXACT_0006_HEAD):
        _upgrade_to_revision(owner, revision)


def _capture_green_authority(owner: object) -> _GreenAuthorityCapture:
    with psycopg.connect(owner.conninfo()) as connection:  # type: ignore[attr-defined]
        head = connection.execute(EXACT_HEAD_SQL).fetchall()
        catalog = capture_catalog(connection, CONTRACT)
        violations = find_event_chain_violations(connection, CONTRACT)
    if head not in ([(EXACT_0006_HEAD,)], [(EXACT_0007_HEAD,)]):
        raise AssertionError("green authority capture has an unexpected Alembic head")
    return _GreenAuthorityCapture(head[0][0], catalog, violations)


def _fresh_0007_capture() -> _GreenAuthorityCapture:
    _require_green_upgrade_authority()
    with disposable_database(operation_id=GREEN_FORWARD_UPGRADE_OPERATION_ID) as owner:
        _upgrade_exactly_through_0007(owner)
        return _capture_green_authority(owner)


def _assert_0007_rejection_is_atomic(
    owner: object,
    expected_error: str,
) -> _GreenAuthorityCapture:
    before = _capture_green_authority(owner)
    assert before.head == EXACT_0006_HEAD

    with pytest.raises(RuntimeError) as captured:
        _upgrade_to_revision(owner, EXACT_0007_HEAD)
    assert str(captured.value) == expected_error

    after = _capture_green_authority(owner)
    assert after == before
    return before


@pytest.fixture(scope="module")
def authority_database():
    if (
        os.environ.get("TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES") != "YES"
        or os.environ.get("TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE")
        != "DISPOSABLE_PG_RED"
    ):
        pytest.skip("exact disposable PostgreSQL RED authority is not present")
    with disposable_database(
        operation_id=CATALOG_OPERATION_ID,
    ) as owner:
        _upgrade_to_revision(owner, EXACT_0006_HEAD)
        yield owner


@pytest.fixture(scope="module")
def green_authority_database():
    _require_green_upgrade_authority()
    with disposable_database(operation_id=GREEN_CATALOG_OPERATION_ID) as owner:
        _upgrade_exactly_through_0007(owner)
        yield owner


def test_query_contract_has_only_exact_query_ids_and_sql() -> None:
    document = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    assert set(document) == {
        "catalog_query_id",
        "catalog_sql",
        "event_chain_query_id",
        "event_chain_sql",
    }
    assert document["catalog_query_id"] == "job-plane-catalog-v1"
    assert document["event_chain_query_id"] == "job-plane-event-chain-v1"
    assert not any("digest" in key or "sha256" in key for key in document)


def test_acl_repair_is_one_exact_fixed_statement() -> None:
    assert ACL_REPAIR_PATH.read_bytes() == (
        b"ALTER DEFAULT PRIVILEGES FOR ROLE trading_owner REVOKE EXECUTE "
        b"ON FUNCTIONS FROM PUBLIC;\n"
    )


def test_red_catalog_collector_remains_pinned_to_exact_0006_after_0007() -> None:
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    for name in ("_collect_catalog_evidence", "authority_database"):
        calls = [
            node
            for node in ast.walk(functions[name])
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_upgrade_to_revision"
        ]
        assert len(calls) == 1
        assert [
            argument.id if isinstance(argument, ast.Name) else None
            for argument in calls[0].args
        ] == ["owner", "EXACT_0006_HEAD"]


def test_green_authority_fixtures_remain_pinned_to_exact_0007() -> None:
    fixture_sources = (
        (
            Path(__file__),
            "green_authority_database",
            "_upgrade_exactly_through_0007",
        ),
        (
            ROOT / "tests/jobs/test_job_event_chain_authority.py",
            "authority_database",
            "_upgrade_to_revision",
        ),
        (
            ROOT / "tests/jobs/test_job_transition_authority.py",
            "authority_database",
            "_upgrade_to_revision",
        ),
        (
            ROOT / "tests/jobs/test_job_role_permissions.py",
            "role_database",
            "_upgrade_to_revision",
        ),
    )

    for source_path, fixture_name, expected_upgrade in fixture_sources:
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        fixture = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == fixture_name
        )
        calls = [node for node in ast.walk(fixture) if isinstance(node, ast.Call)]
        expected_call_args = (
            ["owner", "EXACT_0007_HEAD"]
            if expected_upgrade == "_upgrade_to_revision"
            else ["owner"]
        )

        assert not any(
            isinstance(call.func, ast.Name) and call.func.id == "upgrade_to_head"
            for call in calls
        )
        assert any(
            isinstance(call.func, ast.Name)
            and call.func.id == expected_upgrade
            and [
                argument.id if isinstance(argument, ast.Name) else None
                for argument in call.args
            ]
            == expected_call_args
            for call in calls
        )


def test_0007_catalog_matches_reviewed_forward_repair(
    green_authority_database,
) -> None:
    owner = green_authority_database
    expected_snapshot = (
        ROOT / "ops/postgres/job-plane-authority/catalog-0007-v1.snapshot"
    ).read_bytes()
    with psycopg.connect(owner.conninfo()) as connection:
        head = connection.execute(EXACT_HEAD_SQL).fetchall()
        catalog = capture_catalog(connection, CONTRACT)
        violations = find_event_chain_violations(connection, CONTRACT)

    assert head == [(EXACT_0007_HEAD,)]
    assert catalog.canonical_bytes == expected_snapshot
    assert catalog.sha256 == "1d83e9bc3f5cffe9e2dded41c33f46ce0b6d4395df84d3081d0b5132db487a40"
    assert catalog.row_count == 724
    assert violations == ()


def test_0007_green_gate_requires_two_independent_reviewed_catalog_captures() -> None:
    _require_green_upgrade_authority()

    first = _fresh_0007_capture()
    second = _fresh_0007_capture()
    expected_snapshot = REVIEWED_0007_SNAPSHOT_PATH.read_bytes()

    assert first.head == EXACT_0007_HEAD
    assert second.head == EXACT_0007_HEAD
    assert first.catalog.canonical_bytes == second.catalog.canonical_bytes
    assert first.catalog.canonical_bytes == expected_snapshot
    assert second.catalog.canonical_bytes == expected_snapshot
    assert first.catalog.sha256 == "1d83e9bc3f5cffe9e2dded41c33f46ce0b6d4395df84d3081d0b5132db487a40"
    assert second.catalog.sha256 == first.catalog.sha256
    assert first.violations == ()
    assert second.violations == ()


def test_0007_rejects_catalog_drift_without_changing_pre_0007_authority() -> None:
    _require_green_upgrade_authority()

    with disposable_database(operation_id=GREEN_REJECTION_OPERATION_ID) as owner:
        _upgrade_exactly_through_0006(owner)
        with psycopg.connect(owner.conninfo(), autocommit=True) as connection:
            connection.execute(
                "ALTER DEFAULT PRIVILEGES FOR ROLE trading_owner "
                "GRANT SELECT ON TABLES TO trading_reader"
            )

        before = _assert_0007_rejection_is_atomic(
            owner,
            GREEN_CATALOG_DIGEST_REJECTION,
        )

    assert before.catalog.canonical_bytes != REVIEWED_0006_SNAPSHOT_PATH.read_bytes()
    assert before.violations == ()


def test_0007_rejects_event_chain_violation_without_changing_pre_0007_authority() -> None:
    _require_green_upgrade_authority()

    with disposable_database(operation_id=GREEN_REJECTION_OPERATION_ID) as owner:
        _upgrade_exactly_through_0006(owner)
        with psycopg.connect(owner.conninfo(), autocommit=True) as connection:
            connection.execute(
                """
                INSERT INTO public.jobs (
                  job_id, job_type, state, payload, payload_fingerprint,
                  idempotency_key, actor_type, actor_id, max_attempts,
                  attempt_count
                ) VALUES (
                  'task6-event-chain-corruption', 'SNAPSHOT', 'QUEUED',
                  '{}'::jsonb, %s, 'task6:event-chain-corruption',
                  'OPERATOR', 'task6-green-test', 3, 0
                )
                """,
                ("0" * 64,),
            )

        before = _assert_0007_rejection_is_atomic(
            owner,
            GREEN_EVENT_CHAIN_REJECTION,
        )

    assert before.catalog.canonical_bytes == REVIEWED_0006_SNAPSHOT_PATH.read_bytes()
    assert before.violations


def test_database_call_sites_separate_ordinary_and_hash_bound_operations() -> None:
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]

    ordinary_calls = [
        call
        for call in calls
        if isinstance(call.func, ast.Name) and call.func.id == "disposable_database"
    ]
    derivation_calls = [
        call
        for call in calls
        if isinstance(call.func, ast.Name)
        and call.func.id == "disposable_red_derivation_database"
    ]

    assert len(ordinary_calls) == 6
    assert all(
        keyword.arg != "red_sql_file"
        for call in ordinary_calls
        for keyword in call.keywords
    )
    assert len(derivation_calls) == 2
    derivation_bindings = []
    for call in derivation_calls:
        keywords = {keyword.arg: keyword.value for keyword in call.keywords}
        assert isinstance(keywords["operation_id"], ast.Name)
        assert isinstance(keywords["red_sql_file"], ast.Name)
        assert keywords["red_sql_file"].id == "ACL_REPAIR_PATH"
        derivation_bindings.append(keywords["operation_id"].id)
    assert set(derivation_bindings) == {
        "DERIVATION_OPERATION_ID",
        "EVIDENCE_DERIVATION_OPERATION_ID",
    }

    evidence_ordinary = next(
        call
        for call in ordinary_calls
        if any(
            keyword.arg == "operation_id"
            and isinstance(keyword.value, ast.Name)
            and keyword.value.id == "EVIDENCE_CAPTURE_OPERATION_ID"
            for keyword in call.keywords
        )
    )
    evidence_derivation = next(
        call
        for call in derivation_calls
        if any(
            keyword.arg == "operation_id"
            and isinstance(keyword.value, ast.Name)
            and keyword.value.id == "EVIDENCE_DERIVATION_OPERATION_ID"
            for keyword in call.keywords
        )
    )
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    for call in (evidence_ordinary, evidence_derivation):
        ancestor = parents[call]
        while not isinstance(ancestor, ast.For):
            ancestor = parents[ancestor]
        assert isinstance(ancestor.iter, ast.Call)
        assert isinstance(ancestor.iter.func, ast.Name)
        assert ancestor.iter.func.id == "range"
        assert len(ancestor.iter.args) == 1
        assert isinstance(ancestor.iter.args[0], ast.Constant)
        assert ancestor.iter.args[0].value == 2


@pytest.mark.parametrize(
    "required_fragment",
    (
        "pg_catalog.pg_database",
        "pg_catalog.pg_namespace",
        "pg_catalog.pg_class",
        "pg_catalog.pg_attribute",
        "pg_catalog.pg_constraint",
        "pg_catalog.pg_index",
        "pg_catalog.pg_sequence",
        "pg_catalog.pg_proc",
        "pg_catalog.pg_trigger",
        "pg_catalog.pg_policy",
        "pg_catalog.pg_default_acl",
        "pg_catalog.pg_roles",
        "pg_catalog.pg_auth_members",
        "pg_catalog.pg_db_role_setting",
        "pg_catalog.pg_get_functiondef",
        "pg_catalog.pg_get_function_result",
        "pg_catalog.pg_get_triggerdef",
        "database_row.datlocprovider",
        "database_row.datconnlimit",
        "procedure_row.proowner",
        "database_row.datdba",
        "namespace_row.nspowner",
        "relation_row.relowner",
        "default_acl.defaclrole",
        "row_security",
        "force_row_security",
        "role_row.rolcanlogin",
        "role_row.rolsuper",
        "role_row.rolcreatedb",
        "role_row.rolcreaterole",
        "role_row.rolinherit",
        "role_row.rolreplication",
        "role_row.rolbypassrls",
        "role_row.rolconnlimit",
        "role_row.rolvaliduntil",
        "membership.roleid",
        "membership.member",
        "membership.grantor",
        "admin_option",
        "inherit_option",
        "set_option",
        "UNSAFE_ROLE_SETTING",
    ),
)
def test_catalog_query_contains_every_reviewed_authority_surface(
    required_fragment: str,
) -> None:
    assert required_fragment in CONTRACT.catalog_sql


def test_catalog_query_scope_and_safe_setting_policy_are_closed() -> None:
    _assert_catalog_vocabularies_closed(CONTRACT.catalog_sql)
    assert "pg_catalog.pg_authid" not in CONTRACT.catalog_sql
    assert "rolpassword" not in CONTRACT.catalog_sql
    assert "pg_catalog.pg_shadow" not in CONTRACT.catalog_sql
    assert "pg_catalog.pg_relation_filepath" not in CONTRACT.catalog_sql
    assert "version_num" not in CONTRACT.catalog_sql
    assert 'COLLATE "C"' in CONTRACT.catalog_sql


def test_postgresql_timezone_key_is_normalized_under_closed_safe_policy() -> None:
    postgres_entry = "TimeZone=UTC"
    raw_key, separator, raw_value = postgres_entry.partition("=")
    assert (raw_key.lower(), separator, raw_value) == ("timezone", "=", "UTC")

    sql = CONTRACT.catalog_sql
    normalized_key = (
        "pg_catalog.lower(pg_catalog.split_part("
        "setting_entry.entry, '=', 1))"
    )
    role_setting_entries = re.search(
        r"role_setting_entries AS \((?P<body>.*?)\n\),\n"
        r"function_unsafe_settings AS",
        sql,
        flags=re.DOTALL,
    )
    assert role_setting_entries is not None
    role_setting_body = role_setting_entries.group("body")

    assert f"{normalized_key} AS setting_key" in role_setting_body
    assert f"WHEN {normalized_key} IN (" in role_setting_body
    assert role_setting_body.count(normalized_key) == 2
    assert "pg_catalog.substr(\n             setting_entry.entry," in (
        role_setting_body
    )
    assert "'key', setting_entry.setting_key" in sql
    _assert_catalog_vocabularies_closed(sql)

    unknown_entry = "application_name=DO_NOT_RETURN_THIS_SETTING_VALUE"
    unknown_key, _, secret_value = unknown_entry.partition("=")
    assert unknown_key.lower() not in SAFE_ROLE_SETTING_KEYS
    assert (
        "SELECT 'UNSAFE_ROLE_SETTING', setting_entry.setting_key, NULL::text"
        in sql
    )

    class Cursor:
        def fetchall(self):
            return [("UNSAFE_ROLE_SETTING", unknown_key.lower(), None)]

    class Connection:
        def execute(self, _query):
            return Cursor()

    with pytest.raises(UnsafeCatalogSettingError) as captured:
        capture_catalog(Connection(), CONTRACT)

    assert str(captured.value) == (
        "unknown role setting key: application_name"
    )
    assert secret_value not in str(captured.value)


def test_catalog_query_serializes_exact_role_membership_and_owner_authority() -> None:
    sql = CONTRACT.catalog_sql

    for fragment in (
        "'owner', pg_catalog.pg_get_userbyid(database_row.datdba)",
        "'owner', pg_catalog.pg_get_userbyid(namespace_row.nspowner)",
        "'owner', pg_catalog.pg_get_userbyid(relation_row.relowner)",
        "'owner', pg_catalog.pg_get_userbyid(procedure_row.proowner)",
        "'owner', pg_catalog.pg_get_userbyid(default_acl.defaclrole)",
        "'granted_role', granted_role.rolname",
        "'member_role', member_role.rolname",
        "'grantor', grantor_role.rolname",
        "'admin_option', membership.admin_option",
        "'inherit_option', membership.inherit_option",
        "'set_option', membership.set_option",
        "'superuser', role_row.rolsuper",
        "'create_database', role_row.rolcreatedb",
        "'create_role', role_row.rolcreaterole",
        "'replication', role_row.rolreplication",
        "'bypass_rls', role_row.rolbypassrls",
    ):
        assert fragment in sql


@pytest.mark.parametrize(
    "drifted_sql",
    (
        CONTRACT.catalog_sql.replace(
            "    ('trading_job_scheduler')\n),\napplication_namespaces",
            "    ('trading_job_scheduler'),\n"
            "    ('trading_unreviewed')\n),\napplication_namespaces",
            1,
        ),
        CONTRACT.catalog_sql.replace(
            "'default_transaction_read_only', 'search_path', 'timezone'",
            "'default_transaction_read_only', 'search_path', 'timezone', "
            "'application_name'",
            1,
        ),
        CONTRACT.catalog_sql.replace(
            "pg_catalog.split_part(setting_entry.entry, '=', 1) <> "
            "'search_path'",
            "pg_catalog.split_part(setting_entry.entry, '=', 1) <> "
            "'search_path'\n  AND "
            "pg_catalog.split_part(setting_entry.entry, '=', 1) <> 'timezone'",
            1,
        ),
    ),
    ids=("extra-role", "extra-role-setting", "extra-function-setting"),
)
def test_catalog_vocabulary_closure_rejects_explicit_extras(
    drifted_sql: str,
) -> None:
    with pytest.raises(AssertionError):
        _assert_catalog_vocabularies_closed(drifted_sql)


def test_catalog_contains_alembic_relation_but_not_head_row(
    authority_database,
) -> None:
    with psycopg.connect(authority_database.conninfo()) as connection:
        evidence = capture_catalog(connection, CONTRACT)

    assert b'"name": "alembic_version"' in evidence.canonical_bytes
    assert b"0006_job_transition_database_authority" not in (
        evidence.canonical_bytes
    )


def test_hash_bound_acl_derivation_is_rolled_back_on_the_same_connection() -> None:
    if (
        os.environ.get("TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES") != "YES"
        or os.environ.get("TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE")
        != "DISPOSABLE_PG_RED"
    ):
        pytest.skip("exact disposable PostgreSQL RED authority is not present")

    with disposable_red_derivation_database(
        operation_id=DERIVATION_OPERATION_ID,
        red_sql_file=ACL_REPAIR_PATH,
    ) as workflow:
        connection = workflow.database
        head = connection.execute(
            "SELECT version_num FROM public.alembic_version"
        ).fetchall()
        assert head == [("0006_job_transition_database_authority",)]
        before = capture_catalog(connection, CONTRACT)
        with connection.transaction(force_rollback=True):
            workflow.execute_reviewed_sql()
            derived = capture_catalog(connection, CONTRACT)
        unchanged = capture_catalog(connection, CONTRACT)

    assert derived.sha256 != before.sha256
    assert derived.canonical_bytes != before.canonical_bytes
    assert unchanged == before


def test_0006_discloses_missing_global_default_function_acl(
    authority_database,
) -> None:
    with psycopg.connect(authority_database.conninfo()) as connection:
        missing_repair = connection.execute(
            """
            SELECT NOT EXISTS (
              SELECT 1
              FROM pg_catalog.pg_default_acl default_acl
              WHERE pg_catalog.pg_get_userbyid(default_acl.defaclrole) =
                    'trading_owner'
                AND default_acl.defaclnamespace = 0
                AND default_acl.defaclobjtype = 'f'
            )
            """
        ).fetchone()[0]

    assert missing_repair is True


@pytest.mark.parametrize(
    "case_id,statements",
    (
        (
            "extra-event-suppressing-trigger",
            (
                "CREATE FUNCTION public.suppress_job_event() RETURNS trigger "
                "LANGUAGE plpgsql SET search_path = pg_catalog "
                "AS 'BEGIN RETURN NULL; END'",
                "CREATE TRIGGER trg_suppress_job_event BEFORE INSERT ON "
                "public.job_events FOR EACH ROW EXECUTE FUNCTION "
                "public.suppress_job_event()",
            ),
        ),
        (
            "disabled-trigger",
            (
                "ALTER TABLE public.job_events DISABLE TRIGGER "
                "trg_job_events_append_only",
            ),
        ),
        (
            "altered-trigger",
            (
                "ALTER TRIGGER trg_job_events_append_only ON public.job_events "
                "RENAME TO trg_job_events_drifted",
            ),
        ),
        (
            "changed-function-body",
            (
                "CREATE OR REPLACE FUNCTION public.reject_job_event_mutation() "
                "RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog "
                "AS 'BEGIN RAISE EXCEPTION ''changed body''; END'",
            ),
        ),
        (
            "changed-function-search-path",
            (
                "ALTER FUNCTION public.reject_job_event_mutation() "
                "SET search_path = public",
            ),
        ),
        (
            "changed-function-result",
            (
                "DROP TRIGGER trg_job_events_append_only ON public.job_events",
                "DROP FUNCTION public.reject_job_event_mutation()",
                "CREATE FUNCTION public.reject_job_event_mutation() "
                "RETURNS text LANGUAGE sql AS 'SELECT ''changed'''",
            ),
        ),
        (
            "function-overload",
            (
                "CREATE FUNCTION public.reject_job_event_mutation(integer) "
                "RETURNS integer LANGUAGE sql IMMUTABLE AS 'SELECT $1'",
            ),
        ),
        (
            "public-execute",
            (
                "GRANT EXECUTE ON FUNCTION "
                "job_plane.api_cancel_snapshot(text, text, text, text) "
                "TO PUBLIC",
            ),
        ),
        (
            "wrong-function-grantee",
            (
                "GRANT EXECUTE ON FUNCTION "
                "job_plane.api_cancel_snapshot(text, text, text, text) "
                "TO trading_reader",
            ),
        ),
        (
            "wrong-function-grant-option",
            (
                "GRANT EXECUTE ON FUNCTION "
                "job_plane.api_cancel_snapshot(text, text, text, text) "
                "TO trading_job_api WITH GRANT OPTION",
            ),
        ),
        (
            "policy-drift",
            (
                "ALTER POLICY job_plane_api_jobs_select ON public.jobs "
                "USING (false)",
            ),
        ),
        (
            "rls-drift",
            ("ALTER TABLE public.jobs DISABLE ROW LEVEL SECURITY",),
        ),
        (
            "extra-relation-acl",
            ("GRANT SELECT ON TABLE public.jobs TO trading_reader",),
        ),
        (
            "extra-sequence-acl",
            (
                "CREATE SEQUENCE public.authority_drift_sequence",
                "GRANT USAGE ON SEQUENCE public.authority_drift_sequence "
                "TO trading_reader",
            ),
        ),
        (
            "extra-column-acl",
            ("GRANT SELECT (state) ON public.jobs TO trading_reader",),
        ),
        (
            "extra-default-acl",
            (
                "ALTER DEFAULT PRIVILEGES FOR ROLE trading_owner "
                "GRANT SELECT ON TABLES TO trading_reader",
            ),
        ),
        (
            "safe-role-setting-drift",
            (
                "ALTER ROLE trading_owner IN DATABASE trading_agent "
                "SET timezone = 'Etc/GMT+1'",
            ),
        ),
    ),
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_catalog_digest_changes_for_every_adversarial_catalog_drift(
    authority_database,
    case_id: str,
    statements: tuple[str, ...],
) -> None:
    del case_id
    with psycopg.connect(
        authority_database.conninfo(), autocommit=True
    ) as connection:
        before = capture_catalog(connection, CONTRACT)
        with connection.transaction(force_rollback=True):
            for statement in statements:
                connection.execute(statement)
            after = capture_catalog(connection, CONTRACT)

    assert after.sha256 != before.sha256
    assert after.canonical_bytes != before.canonical_bytes


def test_unknown_role_setting_returns_only_key_and_never_secret_value(
    authority_database,
) -> None:
    secret_marker = "DO_NOT_RETURN_THIS_SETTING_VALUE"
    with psycopg.connect(
        authority_database.conninfo(), autocommit=True
    ) as connection:
        with connection.transaction(force_rollback=True):
            connection.execute(
                "ALTER ROLE trading_owner IN DATABASE trading_agent "
                f"SET application_name = '{secret_marker}'"
            )
            with pytest.raises(UnsafeCatalogSettingError) as captured:
                capture_catalog(connection, CONTRACT)

    assert str(captured.value) == "unknown role setting key: application_name"
    assert secret_marker not in str(captured.value)


def test_role_attribute_membership_and_owner_records_change_canonical_digest() -> None:
    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

    class Connection:
        def __init__(self, rows):
            self.rows = rows

        def execute(self, _query):
            return Cursor(self.rows)

    baseline = capture_catalog(
        Connection(
            [
                (
                    "ROLE",
                    None,
                    '{"kind":"role","name":"trading_job_api",'
                    '"login":true,"inherit":false}',
                )
            ]
        ),
        CONTRACT,
    )
    attribute_drift = capture_catalog(
        Connection(
            [
                (
                    "ROLE",
                    None,
                    '{"kind":"role","name":"trading_job_api",'
                    '"login":true,"inherit":true}',
                )
            ]
        ),
        CONTRACT,
    )
    membership_drift = capture_catalog(
        Connection(
            [
                (
                    "ROLE",
                    None,
                    '{"kind":"role","name":"trading_job_api",'
                    '"login":true,"inherit":false}',
                ),
                (
                    "MEMBERSHIP",
                    None,
                    '{"kind":"membership","granted_role":'
                    '"trading_reader","member_role":"trading_job_api"}',
                ),
            ]
        ),
        CONTRACT,
    )
    owner_baseline = capture_catalog(
        Connection(
            [
                (
                    "OBJECT",
                    None,
                    '{"kind":"object","name":"jobs",'
                    '"owner":"trading_owner"}',
                )
            ]
        ),
        CONTRACT,
    )
    owner_drift = capture_catalog(
        Connection(
            [
                (
                    "OBJECT",
                    None,
                    '{"kind":"object","name":"jobs",'
                    '"owner":"trading_reader"}',
                )
            ]
        ),
        CONTRACT,
    )

    assert attribute_drift.sha256 != baseline.sha256
    assert membership_drift.sha256 != baseline.sha256
    assert owner_drift.sha256 != owner_baseline.sha256


def _fake_catalog_evidence(content: bytes) -> CatalogEvidence:
    return CatalogEvidence(
        query_id=CONTRACT.catalog_query_id,
        sha256=hashlib.sha256(content).hexdigest(),
        row_count=content.count(b"\n"),
        canonical_bytes=content,
    )


@contextmanager
def _temporary_evidence_directory(
    prefix: str = "job-plane-authority-evidence-test-",
):
    with tempfile.TemporaryDirectory(prefix=prefix, dir="/tmp") as raw:
        path = Path(raw)
        path.chmod(0o700)
        yield path


def _fake_collection():
    baseline = _fake_catalog_evidence(
        b'{"kind":"role_setting","key":"timezone","value":"UTC"}\n'
    )
    derived = _fake_catalog_evidence(
        b'{"kind":"default_acl","acl":[],"owner":"trading_owner"}\n'
    )
    ordinary = (
        _EvidenceCapture(EXACT_0006_HEAD, baseline),
        _EvidenceCapture(EXACT_0006_HEAD, baseline),
    )
    derivations = (
        _EvidenceDerivation(EXACT_0006_HEAD, baseline, derived, baseline),
        _EvidenceDerivation(EXACT_0006_HEAD, baseline, derived, baseline),
    )
    return _EvidenceCollection(ordinary, derivations)


def _fake_input_metadata() -> dict[str, dict[str, str]]:
    return {
        "migration_0005": {
            "filename": _REVIEWED_INPUT_FILENAMES["migration_0005"],
            "sha256": "5" * 64,
        },
        "migration_0006": {
            "filename": _REVIEWED_INPUT_FILENAMES["migration_0006"],
            "sha256": "6" * 64,
        },
        "query_contract": {
            "filename": _REVIEWED_INPUT_FILENAMES["query_contract"],
            "sha256": "c" * 64,
        },
        "reviewed_sql": {
            "filename": _REVIEWED_INPUT_FILENAMES["reviewed_sql"],
            "sha256": "a" * 64,
        },
    }


@pytest.mark.parametrize(
    "control_name,invalid_value",
    (
        ("TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES", None),
        ("TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES", "NO"),
        ("TRADING_TEST_DISPOSABLE_APPROVAL_RECORD", None),
        ("TRADING_TEST_DISPOSABLE_APPROVAL_RECORD", " "),
        ("TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE", None),
        ("TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE", "DISPOSABLE_PG_GREEN"),
        ("TRADING_TEST_JOB_AUTHORITY_EVIDENCE_OUTPUT_DIR", None),
        ("TRADING_TEST_JOB_AUTHORITY_EVIDENCE_OUTPUT_DIR", "relative-output"),
    ),
)
def test_catalog_evidence_gate_skips_before_output_or_harness_discovery(
    monkeypatch: pytest.MonkeyPatch,
    control_name: str,
    invalid_value: str | None,
) -> None:
    valid_controls = {
        "TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES": "YES",
        "TRADING_TEST_DISPOSABLE_APPROVAL_RECORD": "/tmp/reviewed-record",
        "TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE": "DISPOSABLE_PG_RED",
        "TRADING_TEST_JOB_AUTHORITY_EVIDENCE_OUTPUT_DIR": (
            "/tmp/job-plane-authority-evidence-guard-test"
        ),
    }
    for name, value in valid_controls.items():
        monkeypatch.setenv(name, value)
    if invalid_value is None:
        monkeypatch.delenv(control_name)
    else:
        monkeypatch.setenv(control_name, invalid_value)

    called: list[str] = []

    def forbidden_output(*_args, **_kwargs):
        called.append("output")
        raise AssertionError("output discovery must remain unreachable")

    def forbidden_collection(*_args, **_kwargs):
        called.append("harness")
        raise AssertionError("harness discovery must remain unreachable")

    monkeypatch.setattr(
        sys.modules[__name__],
        "_collect_catalog_evidence",
        forbidden_collection,
    )
    monkeypatch.setattr(
        sys.modules[__name__],
        "_open_evidence_output_directory",
        forbidden_output,
    )

    with pytest.raises(pytest.skip.Exception):
        test_capture_reviewed_catalog_evidence_for_operator_review()

    assert called == []


def test_catalog_evidence_environment_gate_is_collector_first_statement() -> None:
    tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
    collector = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name
        == "test_capture_reviewed_catalog_evidence_for_operator_review"
    )
    first = collector.body[0]

    assert isinstance(first, ast.Assign)
    assert isinstance(first.value, ast.Call)
    assert isinstance(first.value.func, ast.Name)
    assert first.value.func.id == "_require_evidence_output_environment"


def test_catalog_evidence_output_directory_accepts_only_exact_safe_shape() -> None:
    with _temporary_evidence_directory() as valid:
        output = _open_evidence_output_directory(valid)
        try:
            info = os.fstat(output.directory_fd)
            assert stat.S_ISDIR(info.st_mode)
            assert info.st_uid == os.getuid()
            assert stat.S_IMODE(info.st_mode) == 0o700
            assert output.name == valid.name
            assert (output.device, output.inode) == (
                info.st_dev,
                info.st_ino,
            )
        finally:
            os.close(output.directory_fd)

    with pytest.raises(CatalogEvidenceCollectionError):
        _open_evidence_output_directory(Path("relative-output"))

    with tempfile.TemporaryDirectory(prefix="outside-prefix-", dir="/tmp") as raw:
        outside_prefix = Path(raw)
        outside_prefix.chmod(0o700)
        with pytest.raises(CatalogEvidenceCollectionError):
            _open_evidence_output_directory(outside_prefix)

    with _temporary_evidence_directory() as wrong_mode:
        wrong_mode.chmod(0o750)
        with pytest.raises(CatalogEvidenceCollectionError):
            _open_evidence_output_directory(wrong_mode)

    with _temporary_evidence_directory() as nonempty:
        (nonempty / "unexpected").write_bytes(b"x")
        with pytest.raises(CatalogEvidenceCollectionError):
            _open_evidence_output_directory(nonempty)

    with _temporary_evidence_directory(
        prefix="job-plane-authority-evidence-target-"
    ) as target:
        link = target.with_name(
            target.name.replace(
                "job-plane-authority-evidence-target-",
                "job-plane-authority-evidence-link-",
                1,
            )
        )
        assert not link.exists() and not link.is_symlink()
        link.symlink_to(target, target_is_directory=True)
        try:
            with pytest.raises(CatalogEvidenceCollectionError):
                _open_evidence_output_directory(link)
        finally:
            link.unlink()


def test_catalog_evidence_files_are_exclusive_nofollow_and_exact_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _temporary_evidence_directory() as output:
        destination = _open_evidence_output_directory(output)
        real_open = os.open
        observed_flags: list[int] = []

        def recording_open(path, flags, mode=0o777, *, dir_fd=None):
            if dir_fd == destination.directory_fd:
                observed_flags.append(flags)
            return real_open(path, flags, mode, dir_fd=dir_fd)

        monkeypatch.setattr(os, "open", recording_open)
        try:
            _write_exclusive_evidence_file(
                destination.directory_fd,
                "catalog-0006-capture-1.snapshot",
                b"reviewed\n",
            )
            written = output / "catalog-0006-capture-1.snapshot"
            assert written.read_bytes() == b"reviewed\n"
            assert stat.S_IMODE(written.stat().st_mode) == 0o600
            assert observed_flags[-1] & os.O_EXCL
            assert observed_flags[-1] & os.O_NOFOLLOW

            with pytest.raises(CatalogEvidenceCollectionError):
                _write_exclusive_evidence_file(
                    destination.directory_fd,
                    "catalog-0006-capture-1.snapshot",
                    b"replacement\n",
                )
            assert written.read_bytes() == b"reviewed\n"
        finally:
            os.close(destination.directory_fd)


def test_catalog_evidence_rechecks_empty_directory_before_any_emission() -> None:
    collection = _fake_collection()
    artifacts = _validated_artifact_contents(collection)
    completion = _canonical_completion_json({"complete": True})
    with _temporary_evidence_directory() as output:
        destination = _open_evidence_output_directory(output)
        try:
            (output / "appeared-after-validation").write_bytes(b"x")
            with pytest.raises(CatalogEvidenceCollectionError):
                _emit_evidence(destination, artifacts, completion)
            assert {item.name for item in output.iterdir()} == {
                "appeared-after-validation"
            }
        finally:
            os.close(destination.directory_fd)


def test_catalog_evidence_rejects_chmod_race_before_any_emission() -> None:
    collection = _fake_collection()
    artifacts = _validated_artifact_contents(collection)
    completion = _canonical_completion_json({"complete": True})
    with _temporary_evidence_directory() as output:
        destination = _open_evidence_output_directory(output)
        output.chmod(0o750)
        try:
            with pytest.raises(CatalogEvidenceCollectionError):
                _emit_evidence(destination, artifacts, completion)
            assert list(output.iterdir()) == []
        finally:
            output.chmod(0o700)
            os.close(destination.directory_fd)


def test_catalog_evidence_rejects_rename_replacement_before_any_emission() -> None:
    collection = _fake_collection()
    artifacts = _validated_artifact_contents(collection)
    completion = _canonical_completion_json({"complete": True})
    with _temporary_evidence_directory() as output:
        destination = _open_evidence_output_directory(output)
        moved = output.with_name(f"{output.name}-moved")
        assert not moved.exists()
        output.rename(moved)
        output.mkdir(mode=0o700)
        try:
            with pytest.raises(CatalogEvidenceCollectionError):
                _emit_evidence(destination, artifacts, completion)
            assert list(output.iterdir()) == []
            assert list(moved.iterdir()) == []
        finally:
            output.rmdir()
            moved.rename(output)
            os.close(destination.directory_fd)


@pytest.mark.parametrize(
    "unsafe_content",
    (
        b'{"kind":"role","password":"do-not-disclose"}\n',
        b'{"kind":"role","note":"test-only-owner-credential-0001"}\n',
        b'{"kind":"role","dsn":"host=unreviewed"}\n',
        b'{"kind":"role","uri":"unreviewed-endpoint"}\n',
        b'{"kind":"role","endpoint":"postgresql://unreviewed"}\n',
        b'{"kind":"role","pgservice":"unreviewed-runtime-setting"}\n',
        b'{"kind":"role_setting","key":"application_name",'
        b'"value":"do-not-disclose"}\n',
        b'{"kind":"schema","name":"public"}\r\n',
    ),
)
def test_catalog_evidence_secret_scan_rejects_generically_without_values(
    unsafe_content: bytes,
) -> None:
    with pytest.raises(CatalogEvidenceCollectionError) as captured:
        _secret_scan_catalog_bytes(unsafe_content)

    assert str(captured.value) == "catalog evidence failed secret scan"
    assert "do-not-disclose" not in str(captured.value)
    assert "test-only-owner" not in str(captured.value)
    assert "host=unreviewed" not in str(captured.value)


def test_completion_metadata_is_deterministic_strict_canonical_json() -> None:
    collection = _fake_collection()
    inputs = _fake_input_metadata()

    artifacts = _validated_artifact_contents(collection)
    first = _build_completion_metadata(
        collection,
        source_commit="1" * 40,
        source_tree="2" * 40,
        input_metadata=inputs,
        artifacts=artifacts,
    )
    second = _build_completion_metadata(
        collection,
        source_commit="1" * 40,
        source_tree="2" * 40,
        input_metadata=inputs,
        artifacts=artifacts,
    )
    canonical = _canonical_completion_json(first)

    assert first == second
    assert canonical == _canonical_completion_json(second)
    assert canonical.endswith(b"\n")
    assert b"\r" not in canonical
    assert canonical == (
        json.dumps(first, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("ascii")
    assert json.loads(canonical) == first
    assert first["source"] == {"commit": "1" * 40, "tree": "2" * 40}
    assert first["proofs"] == {
        "catalog_0006_bytes_equal": True,
        "catalog_0007_bytes_equal": True,
        "catalog_0007_differs_from_0006": True,
        "derivation_pre_matches_0006": [True, True],
        "rollback_unchanged": [True, True],
        "secret_scan_passed": True,
    }
    serialized = canonical.decode("ascii")
    assert "postgresql://" not in serialized
    assert "/tmp/" not in serialized
    assert "dsn" not in serialized.lower()


def test_completion_metadata_accepts_only_reviewed_relative_input_filenames() -> None:
    collection = _fake_collection()
    artifacts = _validated_artifact_contents(collection)
    inputs = _fake_input_metadata()
    inputs["reviewed_sql"] = {
        "filename": "unreviewed/alternate.sql",
        "sha256": "a" * 64,
    }

    with pytest.raises(CatalogEvidenceCollectionError):
        _build_completion_metadata(
            collection,
            source_commit="1" * 40,
            source_tree="2" * 40,
            input_metadata=inputs,
            artifacts=artifacts,
        )


def test_clean_source_tree_is_resolved_from_the_exact_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commit = "1" * 40
    tree = "2" * 40
    calls: list[tuple[str, ...]] = []

    def fake_git_output(*arguments: str) -> bytes:
        calls.append(arguments)
        if arguments[0] == "status":
            return b""
        if arguments == ("rev-parse", "--verify", "HEAD"):
            return f"{commit}\n".encode("ascii")
        if arguments == ("rev-parse", "--verify", f"{commit}^{{tree}}"):
            return f"{tree}\n".encode("ascii")
        raise AssertionError("tree must be resolved from the captured commit")

    monkeypatch.setattr(sys.modules[__name__], "_git_output", fake_git_output)

    assert _resolve_clean_source_identity() == (commit, tree)
    assert calls[-1] == ("rev-parse", "--verify", f"{commit}^{{tree}}")


def test_evidence_operation_ids_are_globally_unique_stable_literals() -> None:
    expected_capture = "jobs-authority-catalog-" + "evidence-capture-red-v1"
    expected_derivation = "jobs-authority-catalog-" + "evidence-derivation-red-v1"
    expected_forward = "jobs-authority-0007-" + "forward-green-v1"
    expected_rejection = "jobs-authority-0007-" + "rejection-green-v1"
    tracked_sources = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.py"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
    ).stdout.decode("utf-8").split("\0")
    source_paths = tuple(ROOT / path for path in tracked_sources if path)
    literal_values = [
        node.value
        for path in source_paths
        for node in ast.walk(
            ast.parse(
                path.read_text(encoding="utf-8"),
                filename=str(path.relative_to(ROOT)),
            )
        )
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]

    assert EVIDENCE_CAPTURE_OPERATION_ID == expected_capture
    assert EVIDENCE_DERIVATION_OPERATION_ID == expected_derivation
    assert GREEN_FORWARD_UPGRADE_OPERATION_ID == expected_forward
    assert GREEN_REJECTION_OPERATION_ID == expected_rejection
    assert literal_values.count(expected_capture) == 1
    assert literal_values.count(expected_derivation) == 1
    assert literal_values.count(expected_forward) == 1
    assert literal_values.count(expected_rejection) == 1


def test_two_capture_workflow_has_exact_order_with_injected_fakes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline_bytes = b'{"kind":"schema","name":"public"}\n'
    derived_bytes = (
        b'{"kind":"default_acl","owner":"trading_owner","acl":[]}\n'
    )
    events: list[str] = []
    ordinary_number = 0
    derivation_number = 0

    class Cursor:
        def fetchall(self):
            return [(EXACT_0006_HEAD,)]

    class Connection:
        def __init__(self, label: str, captures: tuple[bytes, ...]):
            self.label = label
            self.captures = iter(captures)

        def __enter__(self):
            events.append(f"{self.label}:connect-enter")
            return self

        def __exit__(self, *_exc):
            events.append(f"{self.label}:connect-exit")

        def execute(self, query: str):
            assert query == EXACT_HEAD_SQL
            events.append(f"{self.label}:head")
            return Cursor()

        @contextmanager
        def transaction(self, *, force_rollback: bool):
            assert force_rollback is True
            events.append(f"{self.label}:rollback-enter")
            yield
            events.append(f"{self.label}:rollback-exit")

    def fake_upgrade(owner, revision: str):
        assert revision == EXACT_0006_HEAD
        events.append(f"{owner}:upgrade")

    def fake_connect(conninfo: str):
        label = conninfo.removesuffix(":conninfo")
        return Connection(label, (baseline_bytes,))

    class Owner(str):
        def conninfo(self):
            return f"{self}:conninfo"

    @contextmanager
    def ordinary_factory(*, operation_id: str):
        nonlocal ordinary_number
        ordinary_number += 1
        label = f"ordinary-{ordinary_number}"
        assert operation_id == EVIDENCE_CAPTURE_OPERATION_ID
        events.append(f"{label}:database-enter")
        yield Owner(label)
        events.append(f"{label}:database-exit")

    class Workflow:
        def __init__(self, connection: Connection):
            self.database = connection

        def execute_reviewed_sql(self):
            events.append(f"{self.database.label}:reviewed-sql")

    @contextmanager
    def derivation_factory(*, operation_id: str, red_sql_file: Path):
        nonlocal derivation_number
        derivation_number += 1
        label = f"derivation-{derivation_number}"
        assert operation_id == EVIDENCE_DERIVATION_OPERATION_ID
        assert red_sql_file == ACL_REPAIR_PATH
        events.append(f"{label}:database-enter")
        connection = Connection(
            label,
            (baseline_bytes, derived_bytes, baseline_bytes),
        )
        yield Workflow(connection)
        events.append(f"{label}:database-exit")

    def fake_capture(connection: Connection, contract):
        assert contract is CONTRACT
        events.append(f"{connection.label}:capture")
        return _fake_catalog_evidence(next(connection.captures))

    monkeypatch.setattr(sys.modules[__name__], "disposable_database", ordinary_factory)
    monkeypatch.setattr(
        sys.modules[__name__],
        "disposable_red_derivation_database",
        derivation_factory,
    )
    monkeypatch.setattr(
        sys.modules[__name__], "_upgrade_to_revision", fake_upgrade
    )
    monkeypatch.setattr(psycopg, "connect", fake_connect)
    monkeypatch.setattr(sys.modules[__name__], "capture_catalog", fake_capture)

    collection = _collect_catalog_evidence()

    expected: list[str] = []
    for number in (1, 2):
        label = f"ordinary-{number}"
        expected.extend(
            (
                f"{label}:database-enter",
                f"{label}:upgrade",
                f"{label}:connect-enter",
                f"{label}:head",
                f"{label}:capture",
                f"{label}:connect-exit",
                f"{label}:database-exit",
            )
        )
    for number in (1, 2):
        label = f"derivation-{number}"
        expected.extend(
            (
                f"{label}:database-enter",
                f"{label}:head",
                f"{label}:capture",
                f"{label}:rollback-enter",
                f"{label}:reviewed-sql",
                f"{label}:capture",
                f"{label}:rollback-exit",
                f"{label}:capture",
                f"{label}:database-exit",
            )
        )

    assert events == expected
    assert len(collection.ordinary) == 2
    assert len(collection.derivations) == 2
    assert all(
        item.catalog.canonical_bytes == baseline_bytes
        for item in collection.ordinary
    )
    assert all(
        item.derived.canonical_bytes == derived_bytes
        for item in collection.derivations
    )
