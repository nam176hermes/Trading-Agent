# Phase 3B Schema

Revision `0003_contract_lineage_repair` follows
`0002_quarantine_lineage` without modifying either earlier revision.

## Canonical contract columns

`decisions` adds nullable `NUMERIC(30,12) price_at_decision`, nullable text
`report_snippet`, and non-null price/snippet provenance quality columns. Existing
rows start as `NULL/UNKNOWN`; the migration does not fabricate a value.

`cost_sessions` adds non-null symbol provenance quality and evidence state.
`UNKNOWN` is distinct from `EVIDENCED`, including a possible evidenced empty
set. Symbols themselves are normalized through child links rather than a
comma-separated field.

## Append-only lineage

- `decision_field_lineage` retains exact/unknown evidence per decision field,
  source hash/index, and normalization version.
- `cost_session_assets` links sessions to registered assets with direct source
  evidence.
- `asset_source_lineage` retains many sources for each canonical asset and
  permits a nullable record index for config/file-level evidence.
- Stable primary IDs and source-identity unique constraints make reruns
  idempotent. A changed source hash produces a new lineage identity.

## Backfill tracking

`phase3b_backfill_runs` is independent of Phase 3 `migration_runs`. Each row is
scoped to one of the four domains and records status, source inventory, commit,
normalization version, and seen/updated/unchanged/unknown/conflicted counters.

`phase3b_backfill_events` stores reason-coded missing, ignored, ambiguous, or
conflicting evidence. Its check constraint contains all nine approved reason
codes. Domain values, run status, evidence state, lineage fields, and
provenance qualities are constrained in PostgreSQL.

## Permissions and rollback

The existing migrator receives DML access and the reader receives SELECT only
on new tables. No trading/runtime role, gate, route, or process is changed.
Downgrade is intentionally restore-based using the verified Phase 3B dump.

Schema tests upgrade the isolated test database twice and confirm the head,
tables, columns, foreign keys, checks, indexes, unique identities, and
least-privilege roles.
