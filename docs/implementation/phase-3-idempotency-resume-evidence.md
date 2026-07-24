# Phase 3 Idempotency and Resume Evidence

## Rerun

An unchanged synthetic plan created a new migration run but inserted zero new
canonical decision rows. All 503 existing canonical records were counted as
skipped and `records_updated` remained zero.

## Resume

The first 500-record chunk committed. An injected failure marked the second
chunk and run `FAILED` in a separate transaction. Before resume, the database
contained exactly 500 decisions, zero quarantine rows from the failed chunk,
one audit event from the committed chunk, and no committed checkpoint for the
failed chunk.

Resume verified source root, inventory hash, normalization version, and Alembic
revision. It skipped chunk one, retried chunk two, inserted the remaining three
valid decisions, and finished with 503 unique decisions. Changed inventory,
normalization version, or schema revision was rejected.

## Rollback and collision

A mid-chunk failure after five attempted records rolled back all domain rows,
audit rows, quarantine rows, and the committed checkpoint from that chunk. A
separate `FAILED` checkpoint remained; retry without injection passed.

When the same source identity carried a different canonical fingerprint, the
existing decision remained unchanged, `records_updated` stayed zero, and a
`DUPLICATE_SOURCE_RECORD` quarantine row was written. Changed source content
with a new source hash/inventory required a new run and retained both provenance
records without overwrite.
