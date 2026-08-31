"""Build and dispose the fixed paper-only P1 Nautilus session."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Protocol

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FeeModel, FillModel
from nautilus_trader.common.config import LoggingConfig
from nautilus_trader.config import BacktestEngineConfig
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.instruments import CurrencyPair
from nautilus_trader.model.objects import Money

from .input_loader import RuntimeInputs
from .market_data_loader import MarketDataBatch
from .target_strategy import TargetStrategy, TargetStrategyConfig


_CONFIGURATION = {
    "account_type": "CASH",
    "allow_leverage": False,
    "allow_short": False,
    "bar_execution": False,
    "fee_model": "fixed-rate",
    "fill_model": "deterministic",
    "load_state": False,
    "logging_bypass": True,
    "network_access": False,
    "oms_type": "NETTING",
    "run_analysis": False,
    "save_state": False,
    "schema_version": "nautilus-p1-engine-configuration-v1",
    "starting_currency": "USDT",
    "venue": "BINANCE",
}


class SessionError(ValueError):
    """The fixed P1 native session could not be built safely."""


class BacktestRunError(RuntimeError):
    """The native run or its terminal proof failed closed."""


class BacktestSession(Protocol):
    engine: BacktestEngine
    strategy: TargetStrategy
    batch: MarketDataBatch

    def run(self) -> None: ...

    def dispose(self, primary: BaseException | None = None) -> None: ...


class BacktestSessionFactory(Protocol):
    def __call__(
        self,
        inputs: RuntimeInputs,
        instrument: CurrencyPair,
        batch: MarketDataBatch,
    ) -> BacktestSession: ...


@dataclass(slots=True)
class BacktestEngineSession:
    """The only P1 native session implementation."""

    engine: BacktestEngine
    strategy: TargetStrategy
    batch: MarketDataBatch

    def run(self) -> None:
        self.engine.run()

    def dispose(self, primary: BaseException | None = None) -> None:
        dispose_session(self.engine, primary)


class FixedRateFeeModel(FeeModel):
    """Charge the configured quote-currency rate on each fill."""

    def __init__(self, rate: Decimal) -> None:
        if not rate.is_finite() or rate < 0:
            raise SessionError("fee rate is invalid")
        self._rate = rate

    def get_commission(self, order, fill_qty, fill_px, instrument):
        del order
        commission = fill_qty.as_decimal() * fill_px.as_decimal() * self._rate
        return Money(commission, instrument.quote_currency)


def dispose_session(
    engine: object, primary: BaseException | None = None
) -> None:
    """Dispose once, preserving a primary failure ahead of cleanup failure."""

    try:
        engine.dispose()
    except BaseException as cleanup:
        if primary is not None:
            failures = [primary, cleanup]
            if all(isinstance(item, Exception) for item in failures):
                raise ExceptionGroup("native run and disposal failed", failures)
            raise BaseExceptionGroup("native run and disposal failed", failures)
        raise BacktestRunError("native session disposal failed") from cleanup
    if primary is not None:
        raise primary


def _decimal(value: object, label: str) -> Decimal:
    if type(value) is not str:
        raise SessionError(f"{label} is invalid")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise SessionError(f"{label} is invalid") from exc
    if not result.is_finite():
        raise SessionError(f"{label} is invalid")
    return result


def _schedule(
    inputs: RuntimeInputs, instrument: CurrencyPair
) -> tuple[tuple[str, tuple[str, ...], str, str], ...]:
    values = dict(inputs.target_schedule)
    targets = values.get("targets")
    if (
        values.get("schema_version") != "nautilus-p1-target-schedule-v1"
        or type(targets) is not tuple
        or not targets
    ):
        raise SessionError("target schedule is invalid")
    result: list[tuple[str, tuple[str, ...], str, str]] = []
    for frozen_target in targets:
        if type(frozen_target) is not tuple:
            raise SessionError("target schedule is invalid")
        target = dict(frozen_target)
        positions = target.get("positions")
        signals = target.get("source_signal_ids")
        if (
            type(positions) is not tuple
            or len(positions) != 1
            or type(positions[0]) is not tuple
            or type(signals) is not tuple
            or any(type(signal) is not str for signal in signals)
        ):
            raise SessionError("target schedule is invalid")
        position = dict(positions[0])
        frozen_identity = position.get("instrument")
        if type(frozen_identity) is not tuple:
            raise SessionError("target schedule is invalid")
        identity = dict(frozen_identity)
        if identity != {
            "product_type": "crypto_spot",
            "symbol": str(instrument.raw_symbol),
            "venue": str(instrument.id.venue),
        }:
            raise SessionError("target instrument identity is invalid")
        target_id = target.get("target_id")
        effective_at = target.get("effective_at")
        weight = position.get("target_weight")
        if not all(type(value) is str for value in (target_id, effective_at, weight)):
            raise SessionError("target schedule is invalid")
        result.append((target_id, signals, effective_at, weight))
    return tuple(result)


def create_session(
    inputs: RuntimeInputs,
    instrument: CurrencyPair,
    batch: MarketDataBatch,
) -> BacktestEngineSession:
    """Create the sole fixed-profile engine and register all run inputs."""

    if (
        type(inputs) is not RuntimeInputs
        or type(instrument) is not CurrencyPair
        or type(batch) is not MarketDataBatch
    ):
        raise SessionError("exact native session inputs are required")
    configuration = dict(inputs.engine_configuration)
    if (
        {key: configuration.get(key) for key in _CONFIGURATION} != _CONFIGURATION
        or set(configuration) != set(_CONFIGURATION) | {"fee_rate", "starting_balance"}
    ):
        raise SessionError("engine configuration is unsupported")
    fee_rate = _decimal(configuration["fee_rate"], "fee rate")
    starting_balance = _decimal(
        configuration["starting_balance"], "starting balance"
    )
    if fee_rate < 0 or starting_balance <= 0:
        raise SessionError("engine configuration is unsupported")

    engine = BacktestEngine(
        BacktestEngineConfig(
            load_state=False,
            logging=LoggingConfig(bypass_logging=True),
            run_analysis=False,
            save_state=False,
        )
    )
    try:
        engine.add_venue(
            venue=Venue("BINANCE"),
            oms_type=OmsType.NETTING,
            account_type=AccountType.CASH,
            starting_balances=[Money(starting_balance, instrument.quote_currency)],
            fill_model=FillModel(),
            fee_model=FixedRateFeeModel(fee_rate),
            use_position_ids=True,
            use_random_ids=False,
            bar_execution=False,
            allow_cash_borrowing=False,
        )
        engine.add_instrument(instrument)
        strategy = TargetStrategy(
            TargetStrategyConfig(
                instrument_id=instrument.id,
                bar_type=batch.data[1].bar_type,
                target_schedule=_schedule(inputs, instrument),
                fee_rate=str(configuration["fee_rate"]),
                leverage="1",
                min_notional=str(instrument.min_notional.as_decimal()),
                min_quantity=str(instrument.min_quantity.as_decimal()),
                step_size=str(instrument.size_increment.as_decimal()),
            )
        )
        engine.add_strategy(strategy)
        engine.add_data(list(batch.data), sort=True)
    except BaseException as primary:
        dispose_session(engine, primary)
        raise AssertionError("unreachable")
    return BacktestEngineSession(engine, strategy, batch)


__all__ = [
    "BacktestRunError",
    "BacktestEngineSession",
    "BacktestSession",
    "BacktestSessionFactory",
    "FixedRateFeeModel",
    "SessionError",
    "create_session",
    "dispose_session",
]
