# Phase 3 Operational Schema

Alembic revision `0001_phase3_operational_store` creates the approved fifteen
operational, lineage, migration, quarantine, and audit tables. It uses
`TIMESTAMPTZ`, text plus named `CHECK` constraints, explicit foreign keys,
stable unique source identities, and the approved query indexes. It creates no
order, fill, credential, model, raw report, reflection, scratchpad, or log
table.

Revision `0002_quarantine_lineage` adds source content hash, sanitized legacy
value, and normalization version to `migration_errors`. This preserves complete
quarantine lineage without storing full invalid payloads.

The revision grants read/write operational privileges to `trading_migrator`
and SELECT-only privileges to `trading_reader`. `trading_owner` runs Alembic;
the Control API does not use it.

Downgrade is intentionally unsupported because destructive table drops would
violate the preservation policy. Schema/data rollback uses a tested dump and
restore path instead.
