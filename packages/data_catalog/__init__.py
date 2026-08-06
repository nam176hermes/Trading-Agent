"""Offline, hash-bound catalog contracts for normalized market datasets."""

from .manifests import CatalogManifestError, MarketDatasetManifestV1
from .parquet import (
    CatalogMaterializationError,
    MaterializedMarketDatasetV1,
    materialize_fixture_catalog,
    verify_materialized_catalog,
)

__all__ = [
    "CatalogManifestError",
    "CatalogMaterializationError",
    "MarketDatasetManifestV1",
    "MaterializedMarketDatasetV1",
    "materialize_fixture_catalog",
    "verify_materialized_catalog",
]
