"""Opt-in disposable PostgreSQL proof for canonical market-data persistence."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
import json
import os

import psycopg
import pytest

from packages.domain import (
    InstrumentId,
    MarketCandle,
    MarketDataProvenance,
    MarketSnapshot,
    MarketTimeframe,
    ProductType,
)
from tests.jobs._postgres import (
    _upgrade_to_revision,
    disposable_database,
    disposable_role_settings,
    upgrade_to_head,
)


pytestmark = pytest.mark.runtime_postgres

EXACT_0008_HEAD = "0008_trading_domain_ledger"
EXACT_0009_HEAD = "0009_canonical_market_data"
EMPTY_HEAD_OPERATION_ID = "market-data-empty-head-runtime-green-v1"
PERSISTENCE_OPERATION_ID = "market-data-canonical-persistence-runtime-green-v1"


def _snapshot(*, close: Decimal = Decimal("101"), evidence: str = "a" * 64) -> MarketSnapshot:
    instrument = InstrumentId(symbol="BTCUSDT", venue="BINANCE", product_type=ProductType.CRYPTO_SPOT)
    observed = datetime(2026, 1, 1, 0, 1, tzinfo=UTC)
    return MarketSnapshot(
        instrument=instrument,
        timeframe=MarketTimeframe.ONE_MINUTE,
        candles=(MarketCandle(instrument=instrument, timeframe=MarketTimeframe.ONE_MINUTE, open_time=datetime(2026, 1, 1, tzinfo=UTC), open=Decimal("100"), high=Decimal("102"), low=Decimal("99"), close=close, volume=Decimal("1")),),
        provenance=MarketDataProvenance(provider="public-feed", observed_at=observed, fetched_at=observed + timedelta(seconds=1), raw_evidence_sha256=evidence, schema_version="market-v1", normalization_version="market-normalization-v1"),
        known_at=observed + timedelta(seconds=2), schema_version="market-v1", normalization_version="market-normalization-v1",
    )


@pytest.fixture(scope="module")
def market_data_database():
    if (os.environ.get("TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES") != "YES" or os.environ.get("TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE") != "DISPOSABLE_PG_GREEN"):
        pytest.skip("exact disposable PostgreSQL GREEN authority is not present")
    with disposable_database(
        operation_id=PERSISTENCE_OPERATION_ID,
        planned=True,
    ) as owner:
        _upgrade_to_revision(owner, EXACT_0008_HEAD)
        _upgrade_to_revision(owner, EXACT_0009_HEAD)
        yield owner


@pytest.fixture(scope="module")
def empty_head_database():
    if (os.environ.get("TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES") != "YES" or os.environ.get("TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE") != "DISPOSABLE_PG_GREEN"):
        pytest.skip("exact disposable PostgreSQL GREEN authority is not present")
    with disposable_database(
        operation_id=EMPTY_HEAD_OPERATION_ID,
        planned=True,
    ) as owner:
        upgrade_to_head(owner)
        yield owner


def test_runtime_postgres_upgrades_empty_database_to_0009_head(empty_head_database) -> None:
    with psycopg.connect(empty_head_database.conninfo()) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == EXACT_0009_HEAD
        assert connection.execute("SELECT to_regclass('public.market_data_snapshots')").fetchone()[0] == "market_data_snapshots"


def test_runtime_postgres_upgrades_0008_to_0009_and_persists_atomically(market_data_database) -> None:
    with psycopg.connect(market_data_database.conninfo()) as connection:
        document = _snapshot()
        assert connection.execute("SELECT public.save_market_data_snapshot(%s)", (document.canonical_payload_bytes.decode("utf-8"),)).fetchone()[0] is True
        assert connection.execute("SELECT count(*) FROM public.market_data_snapshots").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM public.market_data_candles").fetchone()[0] == 1
        connection.commit()
        invalid = json.loads(document.canonical_payload_bytes)
        invalid["candles"][0]["high"] = "98"
        invalid_text = json.dumps(invalid, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        with pytest.raises(psycopg.Error, match="OHLCV"):
            connection.execute("SELECT public.save_market_data_snapshot(%s)", (invalid_text,))
        connection.rollback()
        assert connection.execute("SELECT count(*) FROM public.market_data_snapshots").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM public.market_data_candles").fetchone()[0] == 1

        unaligned = json.loads(document.canonical_payload_bytes)
        unaligned["candles"][0]["open_time"] = "2026-01-01T00:00:00.500000Z"
        unaligned_text = json.dumps(
            unaligned,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        with pytest.raises(psycopg.Error, match="OHLCV"):
            connection.execute(
                "SELECT public.save_market_data_snapshot(%s)",
                (unaligned_text,),
            )
        connection.rollback()

        alternative_timestamps = (
            (("candles", 0, "open_time"), "2026-01-01T00:00:00.000000Z"),
            (("known_at",), "2026-01-01T00:01:02.000000Z"),
            (("provenance", "observed_at"), "2026-01-01T00:01:00.000000Z"),
            (("provenance", "fetched_at"), "2026-01-01T00:01:01.000000Z"),
        )
        for path, replacement in alternative_timestamps:
            noncanonical = json.loads(document.canonical_payload_bytes)
            target = noncanonical
            for component in path[:-1]:
                target = target[component]
            target[path[-1]] = replacement
            noncanonical_text = json.dumps(
                noncanonical,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            with pytest.raises(psycopg.Error, match="canonical UTC encoding"):
                connection.execute(
                    "SELECT public.save_market_data_snapshot(%s)",
                    (noncanonical_text,),
                )
            connection.rollback()
            assert connection.execute(
                "SELECT count(*) FROM public.market_data_snapshots"
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT count(*) FROM public.market_data_candles"
            ).fetchone()[0] == 1

        microsecond_document = _snapshot(evidence="d" * 64)
        microsecond_observed = datetime(2026, 1, 1, 0, 1, 0, 123456, tzinfo=UTC)
        microsecond_document = microsecond_document.model_copy(
            update={
                "provenance": microsecond_document.provenance.model_copy(
                    update={
                        "observed_at": microsecond_observed,
                        "fetched_at": microsecond_observed + timedelta(seconds=1),
                    }
                ),
                "known_at": microsecond_observed + timedelta(seconds=2),
            }
        )
        assert ".123456Z" in microsecond_document.canonical_payload_bytes.decode("utf-8")
        assert connection.execute(
            "SELECT public.save_market_data_snapshot(%s)",
            (microsecond_document.canonical_payload_bytes.decode("utf-8"),),
        ).fetchone()[0] is True
        assert connection.execute(
            "SELECT count(*) FROM public.market_data_snapshots"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT count(*) FROM public.market_data_candles"
        ).fetchone()[0] == 2
        connection.commit()

        prohibited_provider = json.loads(document.canonical_payload_bytes)
        prohibited_provider["provenance"]["provider"] = "order-feed"
        prohibited_provider_text = json.dumps(
            prohibited_provider,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        with pytest.raises(psycopg.Error, match="metadata"):
            connection.execute(
                "SELECT public.save_market_data_snapshot(%s)",
                (prohibited_provider_text,),
            )
        connection.rollback()
        assert connection.execute("SELECT count(*) FROM public.market_data_snapshots").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM public.market_data_candles").fetchone()[0] == 2


def test_runtime_postgres_retries_conflicts_reloads_and_enforces_immutable_privileges(market_data_database) -> None:
    with psycopg.connect(market_data_database.conninfo()) as connection:
        document = _snapshot(evidence="b" * 64)
        assert connection.execute("SELECT public.save_market_data_snapshot(%s)", (document.canonical_payload_bytes.decode("utf-8"),)).fetchone()[0] is True
        assert connection.execute("SELECT public.save_market_data_snapshot(%s)", (document.canonical_payload_bytes.decode("utf-8"),)).fetchone()[0] is False
        stored = connection.execute("SELECT canonical_snapshot_text, snapshot_digest FROM public.market_data_snapshots WHERE snapshot_digest = %s", (document.digest,)).fetchone()
        assert stored[0] == document.canonical_payload_bytes.decode("utf-8")
        assert stored[1] == document.digest
        assert MarketSnapshot.model_validate_json(stored[0]) == document
        connection.commit()
        conflicting = _snapshot(close=Decimal("100"), evidence="b" * 64)
        with pytest.raises(psycopg.Error, match="conflicting market snapshot identity"):
            connection.execute("SELECT public.save_market_data_snapshot(%s)", (conflicting.canonical_payload_bytes.decode("utf-8"),))
        connection.rollback()
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND tablename IN ('market_data_snapshots', 'market_data_candles')"
            )
        }
        assert {"market_data_snapshots_lookup_idx", "market_data_snapshots_digest_idx", "market_data_candles_snapshot_sequence_idx"} <= indexes
        assert connection.execute("SELECT has_table_privilege('trading_jobs', 'public.market_data_snapshots', 'SELECT')").fetchone()[0] is False
        assert connection.execute("SELECT has_table_privilege('trading_jobs', 'public.market_data_candles', 'SELECT')").fetchone()[0] is False
        with pytest.raises(psycopg.Error):
            connection.execute("UPDATE public.market_data_snapshots SET provider = 'changed'")
        connection.rollback()

        connection.execute(
            "GRANT USAGE ON SCHEMA public TO trading_reader"
        )
        connection.execute(
            "GRANT SELECT ON TABLE public.market_data_snapshots, "
            "public.market_data_candles TO trading_reader"
        )
        connection.execute(
            "GRANT EXECUTE ON FUNCTION "
            "public.save_market_data_snapshot(text) TO trading_reader"
        )
        connection.commit()

        writer_settings = disposable_role_settings(
            market_data_database,
            "trading_reader",
        )
        writer_document = _snapshot(evidence="c" * 64)
        with psycopg.connect(
            writer_settings.conninfo(),
            autocommit=True,
        ) as writer:
            writer.execute("SET default_transaction_read_only = off")
            assert writer.execute("SELECT current_user").fetchone()[0] == "trading_reader"
            assert writer.execute(
                "SELECT public.save_market_data_snapshot(%s)",
                (writer_document.canonical_payload_bytes.decode("utf-8"),),
            ).fetchone()[0] is True
            assert writer.execute(
                "SELECT count(*) FROM public.market_data_snapshots "
                "WHERE snapshot_digest = %s",
                (writer_document.digest,),
            ).fetchone()[0] == 1

            for statement in (
                "INSERT INTO public.market_data_snapshots DEFAULT VALUES",
                "INSERT INTO public.market_data_candles DEFAULT VALUES",
                "UPDATE public.market_data_snapshots SET provider = provider",
                "DELETE FROM public.market_data_snapshots WHERE false",
                "TRUNCATE TABLE public.market_data_candles",
            ):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    writer.execute(statement)

        assert connection.execute(
            "SELECT has_function_privilege("
            "'trading_reader', "
            "'public.save_market_data_snapshot(text)', "
            "'EXECUTE')"
        ).fetchone()[0] is True
        for table in ("market_data_snapshots", "market_data_candles"):
            for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                assert connection.execute(
                    "SELECT has_table_privilege("
                    "'trading_reader', %s, %s)",
                    (f"public.{table}", privilege),
                ).fetchone()[0] is False
