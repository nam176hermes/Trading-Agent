"""Fixture adapters for the reviewed 0006/0007 disposable authority proofs."""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import LiteralString, cast

from services.job_store.worker_repository import WorkerRepository

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
