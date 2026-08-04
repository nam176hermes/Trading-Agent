"""Injected PostgreSQL boundary for immutable canonical market-data snapshots.

This module discovers neither configuration nor credentials.  Its caller owns the
connection or pool and the database function owns the atomic parent/child write.
"""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from typing import Protocol

from pydantic import ValidationError

from packages.domain.market_data import MarketSnapshot


_DIGEST = re.compile(r"^[0-9a-f]{64}$", re.ASCII)


class MarketDataIntegrityError(ValueError):
    """Persisted market-data bytes do not reproduce the canonical domain value."""


class _Cursor(Protocol):
    def fetchone(self) -> object: ...


class _Connection(Protocol):
    def transaction(self) -> AbstractContextManager[object]: ...
    def execute(self, statement: str, params: Mapping[str, object]) -> _Cursor: ...


class _Pool(Protocol):
    def connection(self) -> AbstractContextManager[_Connection]: ...


@dataclass(frozen=True)
class MarketDataPersistenceOutcome:
    snapshot_digest: str
    inserted: bool


@dataclass(frozen=True)
class MarketDataSnapshotIdentity:
    symbol: str
    venue: str
    product_type: str
    timeframe: str
    range_start: datetime
    range_end: datetime
    known_at: datetime
    observed_at: datetime
    fetched_at: datetime
    provider: str
    raw_evidence_sha256: str
    schema_version: str
    provenance_schema_version: str
    normalization_version: str

    @classmethod
    def from_snapshot(cls, snapshot: MarketSnapshot) -> "MarketDataSnapshotIdentity":
        first = snapshot.candles[0].open_time
        last = snapshot.candles[-1].open_time + timedelta(
            seconds=snapshot.timeframe.interval_seconds
        )
        return cls(
            symbol=snapshot.instrument.symbol,
            venue=snapshot.instrument.venue,
            product_type=snapshot.instrument.product_type.value,
            timeframe=snapshot.timeframe.value,
            range_start=first,
            range_end=last,
            known_at=snapshot.known_at,
            observed_at=snapshot.provenance.observed_at,
            fetched_at=snapshot.provenance.fetched_at,
            provider=snapshot.provenance.provider,
            raw_evidence_sha256=snapshot.provenance.raw_evidence_sha256,
            schema_version=snapshot.schema_version,
            provenance_schema_version=snapshot.provenance.schema_version,
            normalization_version=snapshot.normalization_version,
        )


class PostgresMarketDataSql:
    """Parameterized SQL; source tests do not prove PostgreSQL runtime behavior."""

    SAVE_SNAPSHOT = """SELECT public.save_market_data_snapshot(
    %(canonical_snapshot_text)s
) AS inserted;"""
    LOAD_BY_DIGEST = """SELECT canonical_snapshot_text, snapshot_digest
FROM public.market_data_snapshots
WHERE snapshot_digest = %(snapshot_digest)s;"""
    LOAD_BY_IDENTITY = """SELECT canonical_snapshot_text, snapshot_digest
FROM public.market_data_snapshots
WHERE symbol = %(symbol)s
  AND venue = %(venue)s
  AND product_type = %(product_type)s
  AND timeframe = %(timeframe)s
  AND range_start = %(range_start)s
  AND range_end = %(range_end)s
  AND known_at = %(known_at)s
  AND observed_at = %(observed_at)s
  AND fetched_at = %(fetched_at)s
  AND provider = %(provider)s
  AND raw_evidence_sha256 = %(raw_evidence_sha256)s
  AND schema_version = %(schema_version)s
  AND provenance_schema_version = %(provenance_schema_version)s
  AND normalization_version = %(normalization_version)s
ORDER BY created_at, snapshot_digest;"""


class PostgresMarketDataRepository:
    """Persist validated snapshots through the database-owned atomic authority."""

    def __init__(self, pool: _Pool) -> None:
        self._pool = pool

    @staticmethod
    def _validated_snapshot(snapshot: MarketSnapshot) -> MarketSnapshot:
        try:
            return MarketSnapshot.model_validate(snapshot)
        except (AttributeError, ValidationError) as exc:
            raise MarketDataIntegrityError("snapshot is not a validated MarketSnapshot") from exc

    @staticmethod
    def _row_value(row: object, name: str, index: int) -> object:
        if isinstance(row, Mapping):
            return row[name]
        if isinstance(row, tuple):
            return row[index]
        raise MarketDataIntegrityError("database result has an unsupported row shape")

    def persist(self, snapshot: MarketSnapshot) -> MarketDataPersistenceOutcome:
        snapshot = self._validated_snapshot(snapshot)
        canonical_snapshot_text = snapshot.canonical_payload_bytes.decode("utf-8")
        with self._pool.connection() as connection:
            with connection.transaction():
                row = connection.execute(
                    PostgresMarketDataSql.SAVE_SNAPSHOT,
                    {"canonical_snapshot_text": canonical_snapshot_text},
                ).fetchone()
        if row is None:
            raise MarketDataIntegrityError("market-data write authority returned no result")
        inserted = self._row_value(row, "inserted", 0)
        if type(inserted) is not bool:
            raise MarketDataIntegrityError("market-data write authority returned invalid outcome")
        return MarketDataPersistenceOutcome(
            snapshot_digest=snapshot.digest,
            inserted=inserted,
        )

    @staticmethod
    def _decode_row(
        row: object,
        *,
        expected_digest: str | None = None,
        expected_identity: MarketDataSnapshotIdentity | None = None,
    ) -> MarketSnapshot:
        if row is None:
            raise MarketDataIntegrityError("stored market-data row is missing")
        canonical_text = PostgresMarketDataRepository._row_value(
            row, "canonical_snapshot_text", 0
        )
        stored_digest = PostgresMarketDataRepository._row_value(
            row, "snapshot_digest", 1
        )
        if not isinstance(canonical_text, str) or not isinstance(stored_digest, str):
            raise MarketDataIntegrityError("stored market-data row has invalid types")
        if expected_digest is not None and stored_digest != expected_digest:
            raise MarketDataIntegrityError("stored market-data digest does not match lookup")
        try:
            snapshot = MarketSnapshot.model_validate_json(canonical_text)
        except ValidationError as exc:
            raise MarketDataIntegrityError("stored canonical market-data JSON is invalid") from exc
        if snapshot.canonical_payload_bytes != canonical_text.encode("utf-8"):
            raise MarketDataIntegrityError("stored market-data JSON is not canonical")
        if snapshot.digest != stored_digest:
            raise MarketDataIntegrityError("stored market-data digest does not match canonical bytes")
        if (
            expected_identity is not None
            and MarketDataSnapshotIdentity.from_snapshot(snapshot) != expected_identity
        ):
            raise MarketDataIntegrityError(
                "stored market-data identity does not match lookup"
            )
        return snapshot

    def load_by_digest(self, snapshot_digest: str) -> MarketSnapshot | None:
        if not isinstance(snapshot_digest, str) or _DIGEST.fullmatch(snapshot_digest) is None:
            raise MarketDataIntegrityError("snapshot_digest must be a lowercase sha256 hex digest")
        with self._pool.connection() as connection:
            row = connection.execute(
                PostgresMarketDataSql.LOAD_BY_DIGEST,
                {"snapshot_digest": snapshot_digest},
            ).fetchone()
        if row is None:
            return None
        return self._decode_row(row, expected_digest=snapshot_digest)

    def load_by_identity(
        self, identity: MarketDataSnapshotIdentity
    ) -> MarketSnapshot | None:
        if not isinstance(identity, MarketDataSnapshotIdentity):
            raise MarketDataIntegrityError("market-data identity is invalid")
        with self._pool.connection() as connection:
            row = connection.execute(
                PostgresMarketDataSql.LOAD_BY_IDENTITY,
                {
                    "symbol": identity.symbol,
                    "venue": identity.venue,
                    "product_type": identity.product_type,
                    "timeframe": identity.timeframe,
                    "range_start": identity.range_start,
                    "range_end": identity.range_end,
                    "known_at": identity.known_at,
                    "observed_at": identity.observed_at,
                    "fetched_at": identity.fetched_at,
                    "provider": identity.provider,
                    "raw_evidence_sha256": identity.raw_evidence_sha256,
                    "schema_version": identity.schema_version,
                    "provenance_schema_version": identity.provenance_schema_version,
                    "normalization_version": identity.normalization_version,
                },
            ).fetchone()
        if row is None:
            return None
        return self._decode_row(row, expected_identity=identity)
