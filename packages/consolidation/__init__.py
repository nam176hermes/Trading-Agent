"""Fail-closed source snapshot consolidation primitives."""

from .authority import (
    AuthorityError,
    ComponentAuthority,
    SourceAuthority,
    load_source_authority,
)
from .manifest import (
    ComponentManifest,
    ImportPolicy,
    ManifestEntry,
    ManifestError,
    canonical_manifest_bytes,
    propose_manifest,
    verify_manifest_source,
)

__all__ = (
    "AuthorityError",
    "ComponentAuthority",
    "ComponentManifest",
    "ImportPolicy",
    "ManifestEntry",
    "ManifestError",
    "SourceAuthority",
    "canonical_manifest_bytes",
    "load_source_authority",
    "propose_manifest",
    "verify_manifest_source",
)
