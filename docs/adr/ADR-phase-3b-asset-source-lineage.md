# ADR: Phase 3B Asset Source Lineage

## Decision

Keep `assets` canonical and create append-only many-to-many
`asset_source_lineage`. Preserve structured occurrences from the registry,
canonical decisions, valid report assets, SQLite signals, and approved cost
session metadata. Current plan is 41,039 lineage rows for all 17 stored assets.

## Precedence and quality

Direct structured symbol fields are `EXACT`. Registry-seeded lineage references
the registry file hash/version. Derived or estimated asset attribution is not
approved. Missing evidence remains absent/unknown rather than fabricated.

## Forbidden inference

Do not select one source as the asset's sole origin, infer symbols from prose or
filenames, create unknown assets, or add lineage for configured SPY/QQQ because
they are not canonical PostgreSQL rows.

## Identity and collision

Identity includes asset ID, source type/path/hash, optional record index,
source field, normalization version, and canonical fingerprint. An identical
rerun skips; a changed source hash creates a new row. A fingerprint collision
records evidence conflict and never mutates the canonical asset.

## Contract and rollback

Lineage remains internal; the Asset public schema is unchanged. The table is
append-only through application policy. Rollback restores the verified dump;
Phase 3 runs and canonical asset identities are unchanged.
