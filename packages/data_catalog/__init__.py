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
from .security_master import (
    MaterializedSecurityMasterSnapshotV1,
    SecurityMasterSnapshotCursorV1,
    SecurityMasterSnapshotManifestV1,
    materialize_security_master_snapshot,
    verify_security_master_snapshot,
)

__all__ = [
    "CatalogManifestError",
    "CatalogMaterializationError",
    "CatalogWorkspaceV1",
    "MarketDatasetContinuityV1",
    "MarketDatasetManifestV1",
    "MaterializedMarketDatasetV1",
    "MaterializedSecurityMasterSnapshotV1",
    "SecurityMasterSnapshotCursorV1",
    "SecurityMasterSnapshotManifestV1",
    "materialize_fixture_catalog",
    "materialize_security_master_snapshot",
    "verify_materialized_catalog",
    "verify_security_master_snapshot",
]
