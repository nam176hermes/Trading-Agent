"""Read-only catalog and event-chain authority verification."""

from .verifier import (
    AuthorityEvidence,
    CatalogEvidence,
    FrozenAuthorityContract,
    UnsafeCatalogSettingError,
    Violation,
    capture_catalog,
    find_event_chain_violations,
    load_frozen_contract,
    load_migration_literals,
    verify_authority,
)

__all__ = (
    "AuthorityEvidence",
    "CatalogEvidence",
    "FrozenAuthorityContract",
    "UnsafeCatalogSettingError",
    "Violation",
    "capture_catalog",
    "find_event_chain_violations",
    "load_frozen_contract",
    "load_migration_literals",
    "verify_authority",
)
