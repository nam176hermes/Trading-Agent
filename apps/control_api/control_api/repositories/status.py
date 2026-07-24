from __future__ import annotations

import os
import sqlite3
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Mapping

from packages.runtime_release.config import ProtectedAuthorityError, load_runtime_authority
from packages.safety_evidence import CanonicalKillSwitchState
from services.job_worker.errors import SafetyBlockedError
from services.job_worker.safety import SafetyMode, SafetySnapshot
from services.job_worker.safety_state import SafetyStateClient
from trading_control.db import DatabaseSettings, connect

from ..contracts import (
    DataFreshness,
    ExecutionCapability,
    ExecutionMode,
    FreshnessStatus,
    KillSwitchState,
    SystemStatus,
)
from ._legacy_files import LegacyFileError, read_json, read_text, validate_regular_file

TRUE_VALUES = {"1", "true", "yes", "on"}
MAX_LIVE_PRICE_BYTES = 64 * 1024
MAX_MODE_BYTES = 128
MAX_KILL_SWITCH_BYTES = 1024


class PostgresReadinessProbe:
    def __init__(self, settings: DatabaseSettings) -> None:
        self.settings = settings

    def ready(self) -> bool:
        try:
            with connect(self.settings, read_only=True) as connection:
                return connection.execute("SELECT 1").fetchone()[0] == 1
        except Exception:
            return False


def authority_bound_safety_provider() -> Callable[[], SafetySnapshot]:
    """Build a current-safety reader pinned to the protected runtime authority."""

    authority = load_runtime_authority()
    client = SafetyStateClient(
        authority.safety.snapshot_path,
        expected_exporter_commit=authority.safety.exporter_commit,
        expected_source_fingerprint=authority.safety.source_fingerprint,
    )

    def read() -> SafetySnapshot:
        authority.recheck()
        evidence = client.evidence()
        authority.recheck()
        return evidence

    return read


class PostgresOperationalStatusRepository:
    """Compose PostgreSQL operational facts with current protected safety evidence."""

    def __init__(
        self,
        settings: DatabaseSettings,
        *,
        safety_provider: Callable[[], SafetySnapshot],
        stale_after_seconds: int = 1800,
        clock: Callable[[], datetime] | None = None,
        latest_research_at: Callable[[], datetime | None] | None = None,
    ) -> None:
        self.settings = settings
        self.safety_provider = safety_provider
        self.stale_after_seconds = stale_after_seconds
        self.clock = clock or (lambda: datetime.now(UTC))
        self.latest_research_at = latest_research_at or (lambda: None)

    def get(self) -> SystemStatus:
        now = self.clock().astimezone(UTC)
        research = self._freshness(self.latest_research_at(), now)
        live_price = DataFreshness(
            status=FreshnessStatus.UNKNOWN,
            as_of=None,
            age_seconds=None,
            stale_after_seconds=self.stale_after_seconds,
        )
        requested = ExecutionMode.PAPER
        effective = ExecutionMode.PAPER
        capability = ExecutionCapability.NON_LIVE
        kill_switch = KillSwitchState.UNKNOWN
        try:
            safety = self.safety_provider()
            if safety.requested_mode is not SafetyMode.UNKNOWN:
                requested = ExecutionMode(safety.requested_mode.value)
            if safety.effective_mode is not SafetyMode.UNKNOWN:
                effective = ExecutionMode(safety.effective_mode.value)
            kill_switch = KillSwitchState(safety.kill_switch_state.value)
            live_available = (
                safety.live_execution_enabled is True
                and safety.live_trading_approved is True
                and kill_switch is KillSwitchState.INACTIVE
            )
            capability = (
                ExecutionCapability.LIVE_AVAILABLE
                if live_available
                else ExecutionCapability.LIVE_BLOCKED
                if requested is ExecutionMode.LIVE
                else ExecutionCapability.NON_LIVE
            )
        except (ProtectedAuthorityError, SafetyBlockedError, ValueError):
            # The API is read-only: unknown evidence must never be presented as
            # live authority. PAPER + UNKNOWN kill switch is the fail-closed UI state.
            pass

        return SystemStatus(
            api_liveness="UP",
            api_readiness="READY",
            backend_service_liveness="UNKNOWN",
            research_pipeline_health=(
                "HEALTHY"
                if research.status is FreshnessStatus.FRESH
                else research.status.value
            ),
            research_data_freshness=research,
            live_price_freshness=live_price,
            database_status="AVAILABLE",
            requested_mode=requested,
            effective_mode=effective,
            execution_capability=capability,
            kill_switch_state=kill_switch,
            # PostgreSQL contains historical status observations only. Until a
            # canonical current order domain exists, absence must stay unknown.
            orders_count=None,
            trades_count=None,
        )

    def _freshness(self, as_of: datetime | None, now: datetime) -> DataFreshness:
        if as_of is None:
            return DataFreshness(
                status=FreshnessStatus.NO_DATA,
                as_of=None,
                age_seconds=None,
                stale_after_seconds=self.stale_after_seconds,
            )
        aware = as_of if as_of.tzinfo else as_of.replace(tzinfo=UTC)
        age = max(0, int((now - aware.astimezone(UTC)).total_seconds()))
        return DataFreshness(
            status=(
                FreshnessStatus.STALE
                if age > self.stale_after_seconds
                else FreshnessStatus.FRESH
            ),
            as_of=aware,
            age_seconds=age,
            stale_after_seconds=self.stale_after_seconds,
        )

class LegacyOperationalStatusRepository:
    def __init__(
        self,
        data_root: Path,
        *,
        stale_after_seconds: int = 1800,
        env: Mapping[str, str] | None = None,
        clock: Callable[[], datetime] | None = None,
        latest_research_at: Callable[[], datetime | None] | None = None,
    ) -> None:
        self.data_root = data_root
        self.stale_after_seconds = stale_after_seconds
        self.env = os.environ if env is None else env
        self.clock = clock or (lambda: datetime.now(UTC))
        self.latest_research_at = latest_research_at or (lambda: None)

    def get(self) -> SystemStatus:
        now = self.clock().astimezone(UTC)
        research = self._freshness(self.latest_research_at(), now)
        live_price = self._freshness(self._live_price_timestamp(), now)
        requested = self._requested_mode()
        enabled = self.env.get("LIVE_EXECUTION_ENABLED", "false").lower() in TRUE_VALUES
        approved = self.env.get("LIVE_TRADING_APPROVED", "false").lower() in TRUE_VALUES
        kill_switch = self._kill_switch_state()
        live_available = enabled and approved and kill_switch is KillSwitchState.INACTIVE
        effective = requested if requested is not ExecutionMode.LIVE or live_available else ExecutionMode.PAPER
        capability = ExecutionCapability.LIVE_AVAILABLE if live_available else (
            ExecutionCapability.LIVE_BLOCKED
            if requested is ExecutionMode.LIVE
            else ExecutionCapability.NON_LIVE
        )
        database_status, orders, trades = self._database_counts()
        return SystemStatus(
            api_liveness="UP",
            api_readiness="READY" if self.data_root.is_dir() else "NOT_READY",
            backend_service_liveness="ALIVE" if live_price.status is FreshnessStatus.FRESH else "STALE" if live_price.status is FreshnessStatus.STALE else "UNKNOWN",
            research_pipeline_health="HEALTHY" if research.status is FreshnessStatus.FRESH else research.status.value,
            research_data_freshness=research,
            live_price_freshness=live_price,
            database_status=database_status,
            requested_mode=requested,
            effective_mode=effective,
            execution_capability=capability,
            kill_switch_state=kill_switch,
            orders_count=orders,
            trades_count=trades,
        )

    def _freshness(self, as_of: datetime | None, now: datetime) -> DataFreshness:
        if as_of is None:
            return DataFreshness(status=FreshnessStatus.NO_DATA, as_of=None, age_seconds=None, stale_after_seconds=self.stale_after_seconds)
        aware = as_of if as_of.tzinfo else as_of.replace(tzinfo=UTC)
        age = max(0, int((now - aware.astimezone(UTC)).total_seconds()))
        status = FreshnessStatus.STALE if age > self.stale_after_seconds else FreshnessStatus.FRESH
        return DataFreshness(status=status, as_of=aware, age_seconds=age, stale_after_seconds=self.stale_after_seconds)

    def _live_price_timestamp(self) -> datetime | None:
        try:
            value = read_json(self.data_root / "live_prices.json", max_bytes=MAX_LIVE_PRICE_BYTES)
            health = value.get("_health", {}) if isinstance(value, dict) else {}
            raw = health.get("last_health_check") if isinstance(health, dict) else None
            if raw is None and isinstance(value, dict):
                raw = value.get("updated_at")
            if not isinstance(raw, str):
                return None
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except (LegacyFileError, ValueError):
            return None

    def _requested_mode(self) -> ExecutionMode:
        try:
            raw = read_text(self.data_root / ".mode", max_bytes=MAX_MODE_BYTES).strip().upper()
            return ExecutionMode(raw)
        except (LegacyFileError, ValueError):
            return ExecutionMode.PAPER

    def _kill_switch_state(self) -> KillSwitchState:
        path = self.data_root / ".kill_switch"
        try:
            info = path.lstat()
        except FileNotFoundError:
            return KillSwitchState.INACTIVE
        except OSError:
            return KillSwitchState.UNKNOWN
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_mode & 0o077
        ):
            return KillSwitchState.UNKNOWN
        try:
            lines = read_text(path, max_bytes=MAX_KILL_SWITCH_BYTES).strip().splitlines()
            if len(lines) != 1:
                return KillSwitchState.UNKNOWN
            activated_at, separator, reason = lines[0].partition(": ")
            if not separator or not reason.strip():
                return KillSwitchState.UNKNOWN
            datetime.fromisoformat(activated_at.replace("Z", "+00:00"))
            return KillSwitchState(CanonicalKillSwitchState.ACTIVE.value)
        except (LegacyFileError, ValueError):
            return KillSwitchState.UNKNOWN

    def _database_counts(self) -> tuple[str, int, int]:
        database = self.data_root / "memory" / "trading.db"
        try:
            validate_regular_file(database)
            connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
            try:
                orders = int(connection.execute("SELECT COUNT(*) FROM orders").fetchone()[0])
                trades = int(connection.execute("SELECT COUNT(*) FROM trades").fetchone()[0])
            finally:
                connection.close()
            return "AVAILABLE", orders, trades
        except (LegacyFileError, OSError, sqlite3.Error, TypeError):
            return "UNAVAILABLE", 0, 0
