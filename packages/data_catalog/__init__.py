"""Offline, hash-bound catalog contracts for normalized market datasets."""

from .manifests import (
    CatalogManifestError,
    MarketDatasetContinuityV1,
    MarketDatasetManifestV1,
)
from .parquet import (
    CatalogMaterializationError,
    MaterializedMarketDatasetV1,
    materialize_fixture_catalog,
    verify_materialized_catalog,
)

__all__ = [
    "CatalogManifestError",
    "CatalogMaterializationError",
    "MarketDatasetContinuityV1",
    "MarketDatasetManifestV1",
    "MaterializedMarketDatasetV1",
    "materialize_fixture_catalog",
    "verify_materialized_catalog",
]
