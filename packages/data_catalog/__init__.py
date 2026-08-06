"""Offline, hash-bound catalog contracts for normalized market datasets."""

from .manifests import (
    CatalogManifestError,
    MarketDatasetContinuityV1,
    MarketDatasetManifestV1,
)
from .parquet import (
    CatalogMaterializationError,
    CatalogWorkspaceV1,
    MaterializedMarketDatasetV1,
    materialize_fixture_catalog,
    verify_materialized_catalog,
)

__all__ = [
    "CatalogManifestError",
    "CatalogMaterializationError",
    "CatalogWorkspaceV1",
    "MarketDatasetContinuityV1",
    "MarketDatasetManifestV1",
    "MaterializedMarketDatasetV1",
    "materialize_fixture_catalog",
    "verify_materialized_catalog",
]
