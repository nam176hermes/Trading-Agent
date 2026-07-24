# ADR: Phase 3 Idempotent Legacy Migration

**Status:** accepted on 2026-07-11.

## Context

The legacy source contains 2,272 report files with mixed validity, one 16,653
record decision JSONL file, stable historical signals inside an actively
written SQLite database, and derived cost/capability evidence. Migration must
be repeatable, resumable, source-read-only, and explain every rejected record.
Filename and mtime are not lineage truth.

## Decision

Hash source content and identify each record using its domain, source content
hash, record index, and normalization version:

```text
sha256(domain + NUL + source_hash + NUL + source_record_index + NUL + normalization_version)
```

Store source-file and deterministic chunk checkpoints. Process report JSON in a
transaction per file and large streams in deterministic 500-record chunks.
Commit valid rows, sanitized quarantine rows, normalization audit events, and
the checkpoint atomically. Roll back an entire chunk on an unexpected database
failure and record failure state in a separate short transaction.

The CLI is dry-run unless `--apply` is explicitly supplied. Resume accepts an
existing run only when source root, inventory hash, normalization version, and
schema revision still match. Committed chunks are skipped; failed or pending
chunks are retried.

Do not update an already imported canonical row in Phase 3. The same record
rerun is skipped; changed source content creates a new source identity; equal
normalized records from distinct sources retain distinct provenance. A
collision with different canonical content fails or quarantines instead of
overwriting.

Invalid payloads are represented by reason code, sanitized message, payload
hash, source reference, and record index. Full invalid payloads are not copied
to PostgreSQL. Valid explicit normalization creates a versioned, reason-coded
audit event. Unknown assets, invalid enums/confidence, missing required fields,
and ambiguous timestamps are not silently coerced.

## Alternatives considered

1. Deduplicate by filename and mtime. Rejected because both can change without
   identifying content and are explicitly non-authoritative.
2. Deduplicate only by a canonical record fingerprint. Rejected because equal
   normalized facts from different sources can carry materially different
   provenance.
3. Use one transaction for the entire migration. Rejected because a 16,653-row
   source would create an unnecessarily large rollback and weak resume seam.
4. Commit every record separately. Rejected because it creates excessive
   overhead and can leave hard-to-explain partial file state.
5. Silently skip malformed records. Rejected because it prevents count
   reconciliation and violates the required quarantine evidence.

## Consequences

- Re-running unchanged input creates no duplicate domain rows.
- An interrupted migration resumes at deterministic committed chunks.
- Changed source content is visible as new provenance instead of overwriting
  prior history.
- Normalization behavior is attributable to a version and reason code.
- `records_updated` remains zero in Phase 3; any future quality-promotion policy
  requires a separate ADR.
- Real-data apply remains blocked until dry-run counts, 86 currently invalid
  reports, source hashes, and backup/restore evidence are reviewed.
