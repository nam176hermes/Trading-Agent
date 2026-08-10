"""Pure exact portfolio execution-accounting reducer."""

from .models import (
    PORTFOLIO_REDUCER_VERSION,
    PORTFOLIO_REPLAY_SCHEMA_VERSION,
    PortfolioAppliedEvent,
    PortfolioBusinessIdentity,
    PortfolioExecutionEffect,
    PortfolioPositionState,
    PortfolioReplayError,
    PortfolioReplayResult,
    PortfolioReplayState,
    PortfolioSnapshotAuthority,
    PortfolioSnapshotRecord,
    PortfolioStreamCursor,
    PortfolioWorkingSnapshot,
)
from .replay import (
    replay_portfolio,
    snapshot_authority_from_result,
    snapshot_from_portfolio_result,
)
from .reducer import apply_portfolio_event, derive_account_snapshot, reduce_portfolio_events

__all__ = [
    "PortfolioAppliedEvent",
    "PortfolioBusinessIdentity",
    "PORTFOLIO_REDUCER_VERSION",
    "PORTFOLIO_REPLAY_SCHEMA_VERSION",
    "PortfolioExecutionEffect",
    "PortfolioPositionState",
    "PortfolioReplayError",
    "PortfolioReplayResult",
    "PortfolioReplayState",
    "PortfolioSnapshotAuthority",
    "PortfolioSnapshotRecord",
    "PortfolioStreamCursor",
    "PortfolioWorkingSnapshot",
    "apply_portfolio_event",
    "derive_account_snapshot",
    "reduce_portfolio_events",
    "replay_portfolio",
    "snapshot_authority_from_result",
    "snapshot_from_portfolio_result",
]
