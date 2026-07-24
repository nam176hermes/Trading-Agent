from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Mapping

import psycopg
import pytest

from control_api.repositories.capabilities import (
    CAPABILITIES,
    LegacyCapabilityRepository,
)
from control_api.repositories.costs import LegacyCostRepository
from control_api.repositories.decisions import LegacyDecisionRepository
from control_api.repositories.market import LegacyMarketReportRepository
from trading_control.db import DatabaseSettings
from tests.jobs._postgres import disposable_role_settings, upgrade_to_head


FIXTURE_NOW = datetime(2026, 7, 1, 1, 0, 0, tzinfo=UTC)
FIXTURE_AS_OF = datetime(2026, 7, 1, 0, 0, 0, tzinfo=UTC)
ASSET_ID = "crypto:spot:BTC/USDT"
SOURCE_HASH = "a" * 64
NORMALIZATION_VERSION = "foundation-runtime-parity-v1"


@dataclass(frozen=True, slots=True)
class DisposableRuntimeFixture:
    data_root: Path
    owner: DatabaseSettings
    reader: DatabaseSettings
    env: Mapping[str, str]
    decision_count: int
    capability_count: int
    cost_session_count: int


def require_disposable_green() -> None:
    import os

    if (
        os.environ.get("TRADING_TEST_ALLOW_DISPOSABLE_POSTGRES") != "YES"
        or os.environ.get("TRADING_TEST_DISPOSABLE_APPROVAL_SCOPE")
        != "DISPOSABLE_PG_GREEN"
    ):
        pytest.skip("exact disposable PostgreSQL GREEN authority is not present")


def database_env(settings: DatabaseSettings, data_root: Path) -> dict[str, str]:
    return {
        "TRADING_DATA_ROOT": str(data_root),
        "TRADING_STORE_BACKEND": "postgres",
        "TRADING_DATABASE_HOST": settings.host,
        "TRADING_DATABASE_PORT": str(settings.port),
        "TRADING_DATABASE_NAME": settings.database,
        "TRADING_DATABASE_USER": settings.user,
        "TRADING_DATABASE_PASSWORD": settings.password,
        "LIVE_EXECUTION_ENABLED": "false",
        "LIVE_TRADING_APPROVED": "false",
        "LIVE_TRADING_ENABLED": "false",
        "TRADING_MODE": "paper",
    }


def build_disposable_runtime_fixture(
    owner: DatabaseSettings,
    data_root: Path,
) -> DisposableRuntimeFixture:
    _write_legacy_fixture(data_root)
    upgrade_to_head(owner)
    _seed_postgres(owner, data_root)
    reader = disposable_role_settings(owner, "trading_reader")
    decisions = LegacyDecisionRepository(data_root).list(page=1, page_size=200)
    capabilities = LegacyCapabilityRepository(data_root).list()
    costs = LegacyCostRepository(data_root).get()
    return DisposableRuntimeFixture(
        data_root=data_root,
        owner=owner,
        reader=reader,
        env=database_env(reader, data_root),
        decision_count=decisions.total,
        capability_count=len(capabilities),
        cost_session_count=costs.total_sessions,
    )


def _market_asset() -> dict[str, object]:
    return {
        "symbol": "BTC",
        "current_price": 100.0,
        "price_change_24h_pct": 1.0,
        "price_change_7d_pct": 2.0,
        "rsi_14": 50.0,
        "rsi_signal": "neutral",
        "macd_signal": "neutral",
        "price_vs_sma200": "above",
        "volume_trend": "1.0x",
        "suggestion": "BUY",
        "confidence": "high",
        "signal_conflict": False,
        "reasoning": "disposable fixture",
        "atr_14": 1.0,
        "atr_pct": 1.0,
        "stop_method": "atr",
        "stop_note": "disposable fixture",
        "alerts": [],
        "risk_assessment": {
            "position_size_pct": 1.0,
            "stop_loss_pct": 2.0,
            "risk_level": "LOW",
            "rationale": "disposable fixture",
        },
    }


def _legacy_decision(index: int, action: str) -> dict[str, object]:
    timestamp = f"2026-07-01T00:0{index}:00Z"
    return {
        "ticker": "BTC",
        "suggestion": action,
        "confidence": 0.5 + index / 10,
        "stored_at": timestamp,
        "date": "2026-07-01",
        "price_at_decision": 100.0 + index,
        "signals": {
            "symbol": "BTC",
            "close": 100.0 + index,
            "rsi_14": 50.0 + index,
            "macd_line": 1.0,
            "macd_signal_line": 0.5,
            "macd_histogram": 0.5,
            "sma_200": 90.0,
            "price_vs_sma200": "above",
            "volume_24h": 1000.0,
            "volume_30d_avg": 900.0,
            "volume_trend_ratio": 1.1,
            "signal": action,
            "calculated_at": timestamp,
        },
        "report_snippet": f"disposable fixture {index}",
        "reflected": False,
    }


def _write_legacy_fixture(data_root: Path) -> None:
    reports = data_root / "reports"
    memory = data_root / "memory"
    scratchpad = data_root / ".dexter" / "scratchpad"
    reports.mkdir(parents=True)
    memory.mkdir(parents=True)
    scratchpad.mkdir(parents=True)
    (reports / "report_fixture.json").write_text(
        json.dumps(
            {
                "timestamp": FIXTURE_AS_OF.isoformat().replace("+00:00", "Z"),
                "assets": [_market_asset()],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    decisions = [
        _legacy_decision(1, "BUY"),
        _legacy_decision(2, "HOLD"),
        _legacy_decision(3, "SELL"),
    ]
    (memory / "decisions.jsonl").write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in decisions),
        encoding="utf-8",
    )
    events = (
        {"type": "llm_call"},
        {"type": "tool_result"},
        {"type": "final_decision"},
    )
    (scratchpad / "session-fixture.jsonl").write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in events),
        encoding="utf-8",
    )


def _seed_postgres(owner: DatabaseSettings, data_root: Path) -> None:
    market = LegacyMarketReportRepository(
        data_root,
        stale_after_seconds=1800,
        clock=lambda: FIXTURE_NOW,
    ).latest()
    assert market.report is not None
    decisions = LegacyDecisionRepository(data_root).list(page=1, page_size=200).items
    costs = LegacyCostRepository(data_root).get()
    with psycopg.connect(owner.conninfo()) as connection:
        connection.execute(
            """
            INSERT INTO assets (
              asset_id,symbol,asset_class,instrument_type,base_currency,
              quote_currency,status,schema_version
            ) VALUES (%s,%s,'CRYPTO','SPOT','BTC','USDT','ACTIVE','1.0.0')
            """,
            (ASSET_ID, "BTC"),
        )
        report = market.report
        connection.execute(
            """
            INSERT INTO market_reports (
              report_id,as_of,generated_at,freshness_status,schema_version,
              normalization_version,provenance_quality,source_type,source_path,
              source_hash,source_record_index,source_record_fingerprint,
              event_time,known_at,ingested_at
            ) VALUES (
              %s,%s,%s,'STALE','1.0.0',%s,'EXACT','FIXTURE',%s,
              %s,1,%s,%s,%s,%s
            )
            """,
            (
                report.report_id,
                report.as_of,
                report.as_of,
                NORMALIZATION_VERSION,
                f"reports/{report.source_file}",
                SOURCE_HASH,
                "b" * 64,
                report.as_of,
                report.as_of,
                report.as_of,
            ),
        )
        for index, asset in enumerate(report.assets, start=1):
            connection.execute(
                """
                INSERT INTO market_asset_snapshots (
                  snapshot_id,report_id,asset_id,price,action,confidence,
                  risk_level,raw_evidence_ref,schema_version,
                  normalization_version,provenance_quality,source_hash,
                  source_record_index,ingested_at
                ) VALUES (%s,%s,%s,%s,%s,0.9,'LOW',%s,'1.0.0',%s,
                          'EXACT',%s,%s,%s)
                """,
                (
                    f"snapshot-{index}",
                    report.report_id,
                    ASSET_ID,
                    asset.current_price,
                    asset.suggestion.value,
                    "canonical-json:" + asset.model_dump_json(),
                    NORMALIZATION_VERSION,
                    SOURCE_HASH,
                    index,
                    report.as_of,
                ),
            )
        for index, decision in enumerate(reversed(decisions), start=1):
            connection.execute(
                """
                INSERT INTO decisions (
                  decision_id,asset_id,action,confidence,as_of,report_id,
                  schema_version,normalization_version,provenance_quality,
                  source_type,source_path,source_hash,source_record_index,
                  source_record_fingerprint,event_time,known_at,ingested_at,
                  price_at_decision,price_provenance_quality,report_snippet,
                  snippet_provenance_quality
                ) VALUES (
                  %s,%s,%s,%s,%s,%s,'1.0.0',%s,'EXACT','FIXTURE',
                  'memory/decisions.jsonl',%s,%s,%s,%s,%s,%s,%s,'EXACT',%s,'EXACT'
                )
                """,
                (
                    decision.decision_id,
                    ASSET_ID,
                    decision.action.value,
                    decision.confidence,
                    decision.decision_at,
                    report.report_id,
                    NORMALIZATION_VERSION,
                    SOURCE_HASH,
                    index,
                    f"{index:064x}",
                    decision.decision_at,
                    decision.decision_at,
                    decision.decision_at,
                    decision.price_at_decision,
                    decision.report_snippet,
                ),
            )
            signals = decision.signals
            connection.execute(
                """
                INSERT INTO decision_signal_snapshots (
                  decision_id,symbol,close,rsi_14,macd_line,macd_signal_line,
                  macd_histogram,sma_200,price_vs_sma200,volume_24h,
                  volume_30d_avg,volume_trend_ratio,signal,calculated_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    decision.decision_id,
                    signals.symbol,
                    signals.close,
                    signals.rsi_14,
                    signals.macd_line,
                    signals.macd_signal_line,
                    signals.macd_histogram,
                    signals.sma_200,
                    signals.price_vs_sma200,
                    signals.volume_24h,
                    signals.volume_30d_avg,
                    signals.volume_trend_ratio,
                    signals.signal,
                    signals.calculated_at,
                ),
            )
        for index, (capability_id, _name) in enumerate(CAPABILITIES, start=1):
            connection.execute(
                """
                INSERT INTO capability_evidence (
                  evidence_id,capability_id,status,source_hash,schema_version,
                  normalization_version,provenance_quality,ingested_at
                ) VALUES (%s,%s,'UNKNOWN',%s,'1.0.0',%s,'EXACT',%s)
                """,
                (
                    f"capability-{index}",
                    capability_id,
                    SOURCE_HASH,
                    NORMALIZATION_VERSION,
                    FIXTURE_AS_OF,
                ),
            )
        connection.execute(
            """
            INSERT INTO cost_summaries (
              cost_summary_id,evidence_quality,currency,total_sessions,
              total_llm_calls,total_tool_calls,amount,as_of,source_hash,
              schema_version,normalization_version,provenance_quality,ingested_at
            ) VALUES (
              'cost-summary-fixture',%s,'USD',%s,%s,%s,%s,%s,%s,
              '1.0.0',%s,'EXACT',%s
            )
            """,
            (
                costs.evidence_quality.value,
                costs.total_sessions,
                costs.total_llm_calls,
                costs.total_tool_calls,
                costs.amount,
                FIXTURE_AS_OF,
                SOURCE_HASH,
                NORMALIZATION_VERSION,
                FIXTURE_AS_OF,
            ),
        )
        for index, session in enumerate(costs.sessions, start=1):
            connection.execute(
                """
                INSERT INTO cost_sessions (
                  cost_session_id,cost_summary_id,session,as_of,steps,llm_calls,
                  tool_calls,decisions,estimated_cost,source_path,source_hash,
                  source_record_index,symbols_provenance_quality,
                  symbols_evidence_state
                ) VALUES (
                  %s,'cost-summary-fixture',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                  'UNKNOWN','UNKNOWN'
                )
                """,
                (
                    f"cost-session-{index}",
                    session.session,
                    FIXTURE_AS_OF,
                    session.steps,
                    session.llm_calls,
                    session.tool_calls,
                    session.decisions,
                    session.estimated_cost,
                    f".dexter/scratchpad/{session.session}.jsonl",
                    SOURCE_HASH,
                    index,
                ),
            )
        connection.commit()
