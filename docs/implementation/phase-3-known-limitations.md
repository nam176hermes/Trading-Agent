# Phase 3 Known Limitations and Acceptance

Counts, source integrity, idempotency, backup/restore, safety, API smoke,
candidate smoke, rollback, and current-dataset performance passed. Phase 3 is
nevertheless blocked by data-contract and lineage defects:

1. Decision `price_at_decision` is not stored independently; 12,783 canonical
   records differ when reconstructed from signal close.
2. Decision `report_snippet` is absent from the PostgreSQL schema; 16,516
   canonical source records contain it.
3. Cost-session `symbols` is absent; all 20 reviewed sessions contain symbols.
4. Assets have no direct source-lineage columns, contrary to the final
   every-canonical-row lineage requirement.

These are `MIGRATION_BUG` / `CONTRACT_BUG` classes and therefore block Phase 3.
They must be resolved through a reviewed Alembic revision and controlled
backfill/reconciliation; direct edits, expected-count changes, legacy fallback,
and WATCH-to-NO_SIGNAL mapping are not acceptable remedies.

NO-GO — PHASE 3 DATA INTEGRITY, CONTRACT, OR SAFETY BLOCKERS REMAIN
