"""Read-only canonical P10 market-data adapter with no legacy fallback."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable, Mapping, Protocol

import psycopg
from pydantic import ValidationError

from packages.domain import MarketSnapshot
from services.market_data.fixture import P10_INSTRUMENT, P10_PROVIDER, P10_TIMEFRAME
from trading_control.db import DatabaseSettings, DatabaseUnavailable, connect

from ..contracts import DataFreshness, FreshnessStatus


_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_P10_SYMBOL = "BTC"
_P10_VENUE = "FIXTURE"
_P10_PRODUCT_TYPE = "crypto_spot"


class CanonicalMarketDataReadError(RuntimeError):
    """Canonical market-data storage is unavailable or violates its contract."""


class CanonicalMarketDataUnavailable(CanonicalMarketDataReadError):
    """The selected API source has no canonical market-data read authority."""


@dataclass(frozen=True, slots=True)
class CanonicalMarketDataResult:
    snapshot: MarketSnapshot | None
    snapshot_digest: str | None
    freshness: DataFreshness


class CanonicalMarketDataRepository(Protocol):
    def latest(self, *, instrument: str, timeframe: str) -> CanonicalMarketDataResult: ...

    def get(self, snapshot_digest: str) -> CanonicalMarketDataResult | None: ...


class UnavailableCanonicalMarketDataRepository:
    """Fail closed instead of scanning legacy reports for canonical candles."""

    def latest(self, *, instrument: str, timeframe: str) -> CanonicalMarketDataResult:
        raise CanonicalMarketDataUnavailable("canonical market-data store is unavailable")

    def get(self, snapshot_digest: str) -> CanonicalMarketDataResult | None:
        raise CanonicalMarketDataUnavailable("canonical market-data store is unavailable")


class PostgresCanonicalMarketDataRepository:
    """Read one bounded P10 snapshot through a PostgreSQL read-only connection."""

    def __init__(
        self,
        settings: DatabaseSettings,
        *,
        stale_after_seconds: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._settings = settings
        self._stale_after_seconds = stale_after_seconds
        self._clock = clock or (lambda: datetime.now(UTC))

    def latest(self, *, instrument: str, timeframe: str) -> CanonicalMarketDataResult:
        self._validate_vocabulary(instrument, timeframe)
        try:
            with connect(self._settings, read_only=True) as connection:
                row = connection.execute(
                    """
                    SELECT canonical_snapshot_text, snapshot_digest
                    FROM public.market_data_snapshots
                    WHERE symbol = %s
                      AND venue = %s
                      AND product_type = %s
                      AND timeframe = %s
                    ORDER BY range_end DESC, known_at DESC, snapshot_digest DESC
                    LIMIT 1
                    """,
                    (_P10_SYMBOL, _P10_VENUE, _P10_PRODUCT_TYPE, P10_TIMEFRAME),
                ).fetchone()
        except (DatabaseUnavailable, psycopg.Error) as exc:
            raise CanonicalMarketDataUnavailable("canonical market-data store is unavailable") from exc
        if row is None:
            return self._no_data()
        snapshot, snapshot_digest = self._decode(row)
        return self._result(snapshot, snapshot_digest)

    def get(self, snapshot_digest: str) -> CanonicalMarketDataResult | None:
        if not isinstance(snapshot_digest, str) or _SHA256.fullmatch(snapshot_digest) is None:
            raise CanonicalMarketDataReadError("snapshot digest is invalid")
        try:
            with connect(self._settings, read_only=True) as connection:
                row = connection.execute(
                    """
                    SELECT canonical_snapshot_text, snapshot_digest
                    FROM public.market_data_snapshots
                    WHERE snapshot_digest = %s
                    LIMIT 1
                    """,
                    (snapshot_digest,),
                ).fetchone()
        except (DatabaseUnavailable, psycopg.Error) as exc:
            raise CanonicalMarketDataUnavailable("canonical market-data store is unavailable") from exc
        if row is None:
            return None
        snapshot, stored_digest = self._decode(row)
        return self._result(snapshot, stored_digest)

    @staticmethod
    def _validate_vocabulary(instrument: str, timeframe: str) -> None:
        if instrument != P10_INSTRUMENT or timeframe != P10_TIMEFRAME:
            raise CanonicalMarketDataReadError("query is outside closed P10 vocabulary")

    @staticmethod
    def _row_value(row: object, name: str, index: int) -> object:
        if isinstance(row, Mapping):
            return row.get(name)
        if isinstance(row, tuple):
            return row[index]
        raise CanonicalMarketDataReadError("canonical market-data row shape is invalid")

    @classmethod
    def _decode(cls, row: object) -> tuple[MarketSnapshot, str]:
        canonical_text = cls._row_value(row, "canonical_snapshot_text", 0)
        snapshot_digest = cls._row_value(row, "snapshot_digest", 1)
        if not isinstance(canonical_text, str) or not isinstance(snapshot_digest, str):
            raise CanonicalMarketDataReadError("canonical market-data row is invalid")
        try:
            snapshot = MarketSnapshot.model_validate_json(canonical_text)
        except ValidationError as exc:
            raise CanonicalMarketDataReadError("canonical market-data payload is invalid") from exc
        if (
            snapshot.canonical_payload_bytes != canonical_text.encode("utf-8")
            or snapshot.digest != snapshot_digest
            or snapshot.instrument.canonical != P10_INSTRUMENT
            or snapshot.timeframe.value != P10_TIMEFRAME
            or snapshot.provenance.provider != P10_PROVIDER
        ):
            raise CanonicalMarketDataReadError("canonical market-data digest or vocabulary is invalid")
        return snapshot, snapshot_digest

    def _result(
        self, snapshot: MarketSnapshot, snapshot_digest: str
    ) -> CanonicalMarketDataResult:
        age_seconds = max(
            0,
            int((self._clock().astimezone(UTC) - snapshot.known_at).total_seconds()),
        )
        status = (
            FreshnessStatus.STALE
            if age_seconds > self._stale_after_seconds
            else FreshnessStatus.FRESH
        )
        return CanonicalMarketDataResult(
            snapshot=snapshot,
            snapshot_digest=snapshot_digest,
            freshness=DataFreshness(
                status=status,
                as_of=snapshot.known_at,
                age_seconds=age_seconds,
                stale_after_seconds=self._stale_after_seconds,
            ),
        )

    def _no_data(self) -> CanonicalMarketDataResult:
        return CanonicalMarketDataResult(
            snapshot=None,
            snapshot_digest=None,
            freshness=DataFreshness(
                status=FreshnessStatus.NO_DATA,
                as_of=None,
                age_seconds=None,
                stale_after_seconds=self._stale_after_seconds,
            ),
        )


__all__ = [
    "CanonicalMarketDataReadError",
    "CanonicalMarketDataRepository",
    "CanonicalMarketDataResult",
    "CanonicalMarketDataUnavailable",
    "PostgresCanonicalMarketDataRepository",
    "UnavailableCanonicalMarketDataRepository",
]
