"""Fixture adapters for the reviewed 0006/0007 disposable authority proofs."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import re
from typing import LiteralString, cast

from services.job_store.worker_repository import WorkerRepository
from packages.job_authority import capture_catalog

ROOT = Path(__file__).parents[2]


def reviewed_disposable_catalog_snapshot(revision: str) -> bytes:
    if revision not in {"0006", "0007"}:
        raise ValueError("only reviewed 0006/0007 snapshots are supported")
    names = {f"REVIEWED_{revision}_CATALOG_SHA256",
             f"REVIEWED_DISPOSABLE_{revision}_CATALOG_SHA256"}
    tree = ast.parse((ROOT / "alembic/versions/0007_job_event_chain_authority.py").read_text())
    pins = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in names}
    snapshot = (ROOT / f"ops/postgres/job-plane-authority/catalog-{revision}-v1.snapshot").read_bytes()
    if hashlib.sha256(snapshot).hexdigest() != pins[f"REVIEWED_{revision}_CATALOG_SHA256"]:
        raise ValueError("reviewed source catalog pin differs")
    snapshot = snapshot.replace(b'"name": "trading_agent"', b'"name": "trading_agent_disposable_test"').replace(
        b'"database": "trading_agent"', b'"database": "trading_agent_disposable_test"')
    if hashlib.sha256(snapshot).hexdigest() != pins[f"REVIEWED_DISPOSABLE_{revision}_CATALOG_SHA256"]:
        raise ValueError("reviewed disposable catalog pin differs")
    return snapshot


_ARRAY_COERCION = re.compile(r"([a-z_][a-z_0-9]*)::text = ANY \(ARRAY\[(.*?)\]::text\[\]\)")


def logical_restore_catalog_representation(snapshot: bytes) -> tuple[bytes, list[tuple[dict, str]]]:
    """Exact known PG16 dump/reparse representation; all other catalog bytes stay strict."""
    lines = []
    repairs = []
    for line in snapshot.splitlines():
        row = json.loads(line)
        if row["kind"] == "constraint" and row["constraint_kind"] == "c":
            definition = row["definition"]

            def expanded(match):
                values = match[2].replace("::character varying", "::character varying::text")
                return f"{match[1]}::text = ANY (ARRAY[{values}])"

            restored = _ARRAY_COERCION.sub(expanded, definition)
            if restored != definition:
                def original_in(match):
                    literals = re.findall(r"'(?:''|[^'])*'::character varying", match[2])
                    if ", ".join(literals) != match[2]:
                        raise ValueError("reviewed array coercion is not a literal IN predicate")
                    values = ", ".join(value.removesuffix("::character varying") for value in literals)
                    return f"{match[1]} IN ({values})"

                original = _ARRAY_COERCION.sub(original_in, definition)
                repairs.append((dict(row), original.removeprefix("CHECK (").removesuffix(")")))
                old = b'"definition": ' + json.dumps(definition).encode()
                new = b'"definition": ' + json.dumps(restored).encode()
                if line.count(old) != 1:
                    raise ValueError("reviewed constraint serialization differs")
                line = line.replace(old, new, 1)
        if row["kind"] == "object" and row.get("name") == "scheduler_heartbeats":
            if row["acl"] != ["trading_owner=arwdDxt/trading_owner"]:
                raise ValueError("reviewed heartbeat owner ACL differs")
            line = line.replace(b'"acl": ["trading_owner=arwdDxt/trading_owner"]', b'"acl": null', 1)
        lines.append(line)
    return b"\n".join(sorted(lines)) + b"\n", repairs


def repair_restored_catalog_representation(connection, contract) -> None:
    """Repair only exact dump representation drift, then require the unchanged frozen pin."""
    from psycopg import sql

    original = reviewed_disposable_catalog_snapshot("0007")
    expected, repairs = logical_restore_catalog_representation(original)
    before = capture_catalog(connection, contract)
    if before.canonical_bytes != expected:
        raise ValueError("catalog is not the exact reviewed logical-restore representation")
    for row, predicate in repairs:
        connection.execute(sql.SQL("ALTER TABLE {} DROP CONSTRAINT {}, ADD CONSTRAINT {} CHECK ({})").format(
            sql.Identifier(row["schema"], row["relation"]), sql.Identifier(row["name"]),
            sql.Identifier(row["name"]), sql.SQL(predicate)))
    connection.execute("GRANT ALL PRIVILEGES ON TABLE public.scheduler_heartbeats TO trading_owner")
    after = capture_catalog(connection, contract)
    if after.canonical_bytes != original or after.sha256 != hashlib.sha256(original).hexdigest():
        raise ValueError("repaired catalog differs from the unchanged reviewed pin")


def repair_disposable_snapshot_restore(owner, contract) -> None:
    import psycopg
    from tests.jobs._postgres import DISPOSABLE_DATABASE, _require_active_disposable_endpoint

    _require_active_disposable_endpoint(owner.conninfo(), {})
    if owner.database != DISPOSABLE_DATABASE or owner.user != "trading_owner":
        raise ValueError("restore repair requires an active disposable owner")
    with psycopg.connect(owner.conninfo()) as connection:
        repair_restored_catalog_representation(connection, contract)


class Snapshot0007WorkerRepository(WorkerRepository):
    """Use the snapshot capabilities that exist at the fixture's exact 0007 head."""

    @staticmethod
    def _session_statement(statement: LiteralString, parameters: tuple[int | str, ...]) -> LiteralString:
        if not parameters:
            for capability in ("worker_start", "worker_finalize"):
                prefix = f"SELECT job_plane.{capability}_paper("
                if statement.startswith(prefix):
                    return cast(LiteralString, statement.replace(prefix, f"SELECT job_plane.{capability}_snapshot(", 1))
        raise ValueError("only 0007 snapshot capabilities without session authority are supported")
