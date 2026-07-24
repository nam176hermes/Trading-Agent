"""Deterministic, read-only job-plane authority verification."""

from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Mapping


HEAD_SQL = "SELECT version_num FROM public.alembic_version"

_CONTRACT_FIELDS = frozenset(
    {
        "catalog_query_id",
        "catalog_sql",
        "event_chain_query_id",
        "event_chain_sql",
    }
)
_MIGRATION_LITERAL_NAMES = (
    "CATALOG_QUERY_ID",
    "CATALOG_SNAPSHOT_SQL",
    "EVENT_CHAIN_QUERY_ID",
    "EVENT_CHAIN_VIOLATIONS_SQL",
)
_MIGRATION_AUTHORITY_LITERAL_NAMES = (
    *_MIGRATION_LITERAL_NAMES,
    "ACL_REPAIR_SQL",
    "REVIEWED_0006_CATALOG_SHA256",
    "REVIEWED_0007_CATALOG_SHA256",
)
_UNSAFE_SETTING_LABELS = {
    "UNSAFE_FUNCTION_SETTING": "function",
    "UNSAFE_ROLE_SETTING": "role",
}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_AUTHORITY_MANIFEST_FIELDS = frozenset(
    {
        "record_kind",
        "schema_version",
        "exact_head",
        "catalog",
        "frozen_inputs",
    }
)
_AUTHORITY_MANIFEST_CATALOG_FIELDS = frozenset(
    {"query_id", "pre_sha256", "post_sha256"}
)
_AUTHORITY_MANIFEST_INPUT_FILENAMES = (
    ("migration_0006", "alembic/versions/0006_job_transition_database_authority.py"),
    ("migration_0007", "alembic/versions/0007_job_event_chain_authority.py"),
    ("query_contract", "ops/postgres/job-plane-authority/query-contract-v1.json"),
    ("acl_repair_sql", "ops/postgres/job-plane-authority/acl-repair-v1.sql"),
    ("catalog_0006_snapshot", "ops/postgres/job-plane-authority/catalog-0006-v1.snapshot"),
    ("catalog_0007_snapshot", "ops/postgres/job-plane-authority/catalog-0007-v1.snapshot"),
    ("catalog_0006_manifest", "ops/postgres/job-plane-authority/catalog-0006-v1.manifest.json"),
    ("catalog_0007_manifest", "ops/postgres/job-plane-authority/catalog-0007-v1.manifest.json"),
)


@dataclass(frozen=True, slots=True)
class FrozenAuthorityContract:
    catalog_query_id: str
    catalog_sql: str
    event_chain_query_id: str
    event_chain_sql: str


@dataclass(frozen=True, slots=True)
class MigrationAuthorityLiterals:
    """Literal 0007 authority inputs read safely without importing the migration."""

    contract: FrozenAuthorityContract
    acl_repair_sql: str
    pre_catalog_sha256: str
    post_catalog_sha256: str


@dataclass(frozen=True, slots=True)
class AuthorityManifest:
    """Strict external binding for every frozen 0007 authority input."""

    exact_head: str
    catalog_query_id: str
    pre_catalog_sha256: str
    post_catalog_sha256: str
    frozen_inputs: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True, slots=True)
class CatalogEvidence:
    query_id: str
    sha256: str
    row_count: int
    canonical_bytes: bytes


@dataclass(frozen=True, slots=True)
class Violation:
    code: str
    job_id: str
    event_id: str | None
    sequence: int | None


@dataclass(frozen=True, slots=True)
class AuthorityEvidence:
    head: str
    catalog: CatalogEvidence
    event_chain_query_id: str
    violations: tuple[Violation, ...]


class UnsafeCatalogSettingError(ValueError):
    """An unreviewed setting key exists; its value was never selected."""


def _reject_duplicate_json_key(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"frozen authority contract has duplicate key: {key}")
        result[key] = value
    return result


def _contract_from_mapping(document: Mapping[str, object]) -> FrozenAuthorityContract:
    if set(document) != _CONTRACT_FIELDS:
        raise ValueError("frozen authority contract fields are missing or unknown")
    if any(
        not isinstance(document[name], str) or not document[name]
        for name in _CONTRACT_FIELDS
    ):
        raise ValueError("frozen authority contract values must be non-empty strings")
    return FrozenAuthorityContract(
        catalog_query_id=document["catalog_query_id"],  # type: ignore[arg-type]
        catalog_sql=document["catalog_sql"],  # type: ignore[arg-type]
        event_chain_query_id=document["event_chain_query_id"],  # type: ignore[arg-type]
        event_chain_sql=document["event_chain_sql"],  # type: ignore[arg-type]
    )


def load_frozen_contract(path: Path) -> FrozenAuthorityContract:
    """Load the exact four-field query contract as strict UTF-8 JSON."""

    try:
        source = path.read_bytes().decode("utf-8", errors="strict")
        document = json.loads(source, object_pairs_hook=_reject_duplicate_json_key)
    except ValueError as exc:
        if "duplicate key" in str(exc):
            raise
        raise ValueError("frozen authority contract is not strict UTF-8 JSON") from None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("frozen authority contract is not strict UTF-8 JSON") from None
    if not isinstance(document, dict):
        raise ValueError("frozen authority contract fields are missing or unknown")
    return _contract_from_mapping(document)


def _direct_string_assignments(
    path: Path,
    names: tuple[str, ...],
) -> dict[str, str]:
    try:
        source = path.read_bytes().decode("utf-8", errors="strict")
        tree = ast.parse(source, filename=str(path))
    except (OSError, UnicodeDecodeError, SyntaxError):
        raise ValueError("migration is not valid strict UTF-8 Python") from None

    wanted = frozenset(names)
    values: dict[str, str] = {}
    for statement in tree.body:
        name: str | None = None
        value: ast.expr | None = None
        if (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
        ):
            name = statement.targets[0].id
            value = statement.value
        elif isinstance(statement, ast.AnnAssign) and isinstance(
            statement.target, ast.Name
        ):
            name = statement.target.id
            value = statement.value
        if name not in wanted:
            continue
        if name in values:
            raise ValueError(f"duplicate migration literal: {name}")
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            raise ValueError(f"migration {name} must be a literal string assignment")
        if not value.value:
            raise ValueError(f"migration {name} must be a literal string assignment")
        values[name] = value.value

    missing = wanted.difference(values)
    if missing:
        raise ValueError(f"missing migration literal: {sorted(missing)[0]}")
    return values


def load_migration_literals(path: Path) -> FrozenAuthorityContract:
    """Read frozen query constants through AST parsing, never import execution."""

    values = _direct_string_assignments(path, _MIGRATION_LITERAL_NAMES)
    return FrozenAuthorityContract(
        catalog_query_id=values["CATALOG_QUERY_ID"],
        catalog_sql=values["CATALOG_SNAPSHOT_SQL"],
        event_chain_query_id=values["EVENT_CHAIN_QUERY_ID"],
        event_chain_sql=values["EVENT_CHAIN_VIOLATIONS_SQL"],
    )


def _validated_sha256(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or _SHA256.fullmatch(value) is None
        or len(set(value)) == 1
    ):
        raise ValueError(f"{label} must be a non-placeholder lowercase SHA-256")
    return value


def load_migration_authority_literals(path: Path) -> MigrationAuthorityLiterals:
    """Load every 0007 frozen literal through AST parsing only."""

    values = _direct_string_assignments(path, _MIGRATION_AUTHORITY_LITERAL_NAMES)
    return MigrationAuthorityLiterals(
        contract=FrozenAuthorityContract(
            catalog_query_id=values["CATALOG_QUERY_ID"],
            catalog_sql=values["CATALOG_SNAPSHOT_SQL"],
            event_chain_query_id=values["EVENT_CHAIN_QUERY_ID"],
            event_chain_sql=values["EVENT_CHAIN_VIOLATIONS_SQL"],
        ),
        acl_repair_sql=values["ACL_REPAIR_SQL"],
        pre_catalog_sha256=_validated_sha256(
            values["REVIEWED_0006_CATALOG_SHA256"],
            "migration pre-catalog digest",
        ),
        post_catalog_sha256=_validated_sha256(
            values["REVIEWED_0007_CATALOG_SHA256"],
            "migration post-catalog digest",
        ),
    )


def load_authority_manifest(path: Path) -> AuthorityManifest:
    """Load the exact non-self-referential 0007 external authority manifest."""

    try:
        source = path.read_bytes().decode("utf-8", errors="strict")
        document = json.loads(source, object_pairs_hook=_reject_duplicate_json_key)
    except ValueError as exc:
        if "duplicate key" in str(exc):
            raise
        raise ValueError("authority manifest is not strict UTF-8 JSON") from None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("authority manifest is not strict UTF-8 JSON") from None

    if not isinstance(document, dict) or set(document) != _AUTHORITY_MANIFEST_FIELDS:
        raise ValueError("authority manifest fields are missing or unknown")
    if document["record_kind"] != "JOB_PLANE_AUTHORITY_MANIFEST":
        raise ValueError("authority manifest record kind is invalid")
    if document["schema_version"] != 1:
        raise ValueError("authority manifest schema version is invalid")
    if document["exact_head"] != "0007_job_event_chain_authority":
        raise ValueError("authority manifest exact head is invalid")

    catalog = document["catalog"]
    if not isinstance(catalog, dict) or set(catalog) != _AUTHORITY_MANIFEST_CATALOG_FIELDS:
        raise ValueError("authority manifest catalog fields are missing or unknown")
    if catalog["query_id"] != "job-plane-catalog-v1":
        raise ValueError("authority manifest catalog query is invalid")

    frozen_inputs = document["frozen_inputs"]
    expected_filenames = dict(_AUTHORITY_MANIFEST_INPUT_FILENAMES)
    if not isinstance(frozen_inputs, dict) or set(frozen_inputs) != set(expected_filenames):
        raise ValueError("authority manifest frozen inputs are missing or unknown")

    bindings: list[tuple[str, str, str]] = []
    for name, filename in _AUTHORITY_MANIFEST_INPUT_FILENAMES:
        item = frozen_inputs[name]
        if (
            not isinstance(item, dict)
            or set(item) != {"filename", "sha256"}
            or item["filename"] != filename
        ):
            raise ValueError(f"authority manifest frozen input is invalid: {name}")
        bindings.append(
            (
                name,
                filename,
                _validated_sha256(item["sha256"], f"authority manifest {name}"),
            )
        )

    pre_catalog_sha256 = _validated_sha256(
        catalog["pre_sha256"], "authority manifest pre-catalog digest"
    )
    post_catalog_sha256 = _validated_sha256(
        catalog["post_sha256"], "authority manifest post-catalog digest"
    )
    bound_input_sha256s = {name: sha256 for name, _filename, sha256 in bindings}
    if pre_catalog_sha256 != bound_input_sha256s["catalog_0006_snapshot"]:
        raise ValueError("authority manifest pre-catalog digest does not bind 0006 snapshot")
    if post_catalog_sha256 != bound_input_sha256s["catalog_0007_snapshot"]:
        raise ValueError("authority manifest post-catalog digest does not bind 0007 snapshot")

    return AuthorityManifest(
        exact_head=document["exact_head"],
        catalog_query_id=catalog["query_id"],
        pre_catalog_sha256=pre_catalog_sha256,
        post_catalog_sha256=post_catalog_sha256,
        frozen_inputs=tuple(bindings),
    )


def verify_authority_manifest(
    manifest_path: Path,
    *,
    root: Path,
    contract_path: Path,
    migration_path: Path,
    repair_path: Path,
) -> AuthorityManifest:
    """Verify frozen source bytes and bindings without touching PostgreSQL."""

    manifest = load_authority_manifest(manifest_path)
    literals = load_migration_authority_literals(migration_path)
    contract = load_frozen_contract(contract_path)
    if literals.contract != contract:
        raise ValueError("migration literals do not match frozen authority contract")
    if literals.acl_repair_sql.encode("utf-8") != repair_path.read_bytes():
        raise ValueError("migration repair literal does not match frozen repair SQL")
    if _migration_revision(migration_path) != manifest.exact_head:
        raise ValueError("migration revision does not match authority manifest")
    if (
        literals.contract.catalog_query_id != manifest.catalog_query_id
        or literals.pre_catalog_sha256 != manifest.pre_catalog_sha256
        or literals.post_catalog_sha256 != manifest.post_catalog_sha256
    ):
        raise ValueError("migration digests do not match authority manifest")

    for _name, filename, expected_sha256 in manifest.frozen_inputs:
        try:
            actual = hashlib.sha256((root / filename).read_bytes()).hexdigest()
        except OSError:
            raise ValueError(f"authority manifest input is unavailable: {filename}") from None
        if actual != expected_sha256:
            raise ValueError(f"authority manifest input digest mismatch: {filename}")
    return manifest


def _migration_revision(path: Path) -> str:
    return _direct_string_assignments(path, ("revision",))["revision"]


def _catalog_rows(connection: object, sql: str) -> list[tuple[object, ...]]:
    cursor = connection.execute(sql)  # type: ignore[attr-defined]
    return cursor.fetchall()  # type: ignore[no-any-return]


def capture_catalog(
    connection: object,
    contract: FrozenAuthorityContract,
) -> CatalogEvidence:
    """Execute the frozen catalog query and hash its canonical safe records."""

    rows = _catalog_rows(connection, contract.catalog_sql)
    validated: list[bytes] = []
    for row in rows:
        if not isinstance(row, (tuple, list)) or len(row) != 3:
            raise ValueError("catalog query returned malformed rows")
        record_type, unsafe_key, canonical_line = row
        if record_type in _UNSAFE_SETTING_LABELS:
            if (
                not isinstance(unsafe_key, str)
                or not unsafe_key
                or canonical_line is not None
            ):
                raise ValueError("catalog query returned malformed rows")
            raise UnsafeCatalogSettingError(
                "unknown "
                f"{_UNSAFE_SETTING_LABELS[record_type]} setting key: {unsafe_key}"
            )
        if (
            not isinstance(record_type, str)
            or not record_type
            or unsafe_key is not None
            or not isinstance(canonical_line, str)
            or not canonical_line
            or "\n" in canonical_line
            or "\r" in canonical_line
        ):
            raise ValueError("catalog query returned malformed rows")
        try:
            validated.append(canonical_line.encode("utf-8", errors="strict"))
        except UnicodeEncodeError:
            raise ValueError("catalog query returned malformed rows") from None

    validated.sort()
    canonical_bytes = b"\n".join(validated)
    if validated:
        canonical_bytes += b"\n"
    return CatalogEvidence(
        query_id=contract.catalog_query_id,
        sha256=hashlib.sha256(canonical_bytes).hexdigest(),
        row_count=len(validated),
        canonical_bytes=canonical_bytes,
    )


def find_event_chain_violations(
    connection: object,
    contract: FrozenAuthorityContract,
) -> tuple[Violation, ...]:
    """Execute the frozen event-chain query and decode stable violation rows."""

    rows = _catalog_rows(connection, contract.event_chain_sql)
    violations: list[Violation] = []
    for row in rows:
        if not isinstance(row, (tuple, list)) or len(row) != 4:
            raise ValueError("event-chain query returned malformed rows")
        code, job_id, event_id, sequence = row
        if (
            not isinstance(code, str)
            or not code
            or not isinstance(job_id, str)
            or not job_id
            or (event_id is not None and not isinstance(event_id, str))
            or isinstance(sequence, bool)
            or (sequence is not None and not isinstance(sequence, int))
        ):
            raise ValueError("event-chain query returned malformed rows")
        violations.append(Violation(code, job_id, event_id, sequence))
    return tuple(violations)


def _read_head(connection: object) -> str:
    rows = _catalog_rows(connection, HEAD_SQL)
    if (
        len(rows) != 1
        or not isinstance(rows[0], (tuple, list))
        or len(rows[0]) != 1
        or not isinstance(rows[0][0], str)
        or not rows[0][0]
    ):
        raise ValueError("Alembic head query returned malformed rows")
    return rows[0][0]


def verify_authority(
    connection: object,
    contract_path: Path,
    migration_path: Path,
) -> AuthorityEvidence:
    """Verify frozen bytes, exact head, catalog, and event history read-only."""

    contract = load_frozen_contract(contract_path)
    migration_contract = load_migration_literals(migration_path)
    if migration_contract != contract:
        raise ValueError("migration literals do not match frozen authority contract")
    expected_head = _migration_revision(migration_path)

    head_before = _read_head(connection)
    if head_before != expected_head:
        raise ValueError("Alembic head does not match migration revision")
    catalog = capture_catalog(connection, contract)
    violations = find_event_chain_violations(connection, contract)
    head_after = _read_head(connection)
    if head_after != head_before:
        raise ValueError("Alembic head changed during verification")
    return AuthorityEvidence(
        head=head_before,
        catalog=catalog,
        event_chain_query_id=contract.event_chain_query_id,
        violations=violations,
    )
