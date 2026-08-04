"""Canonical market-data persistence boundary."""

from .fixture import (
    DeterministicProviderFreeFixture,
    MarketDataProvider,
    MarketDataProviderError,
    ProviderObservation,
)
from .ingestion import (
    MarketDataIngestionError,
    MarketDataIngestor,
    MarketDataSnapshotRepository,
)
from .repository import (
    MarketDataIntegrityError,
    MarketDataPersistenceOutcome,
    MarketDataSnapshotIdentity,
    PostgresMarketDataRepository,
    PostgresMarketDataSql,
)

__all__ = [
    "DeterministicProviderFreeFixture",
    "MarketDataIngestionError",
    "MarketDataIngestor",
    "MarketDataProvider",
    "MarketDataProviderError",
    "MarketDataIntegrityError",
    "MarketDataPersistenceOutcome",
    "MarketDataSnapshotIdentity",
    "MarketDataSnapshotRepository",
    "PostgresMarketDataRepository",
    "PostgresMarketDataSql",
    "ProviderObservation",
]
