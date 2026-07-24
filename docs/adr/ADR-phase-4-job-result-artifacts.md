# ADR: Phase 4 Job Result and Output Artifacts

## Decision

Store child stdout/stderr and validated research results as protected files,
not raw database text. The Phase 4 artifact root is mode `0700`; files are
created `0600`. Output streams are capped at 1 MiB each, hashed over observed
bytes, marked when truncated, and represented in PostgreSQL by relative ref,
hash, size, media type, and validation metadata. No client supplies a path.

Each `CommandSpec` owns a result validator. Success requires exit code zero,
validator success, and atomic result-metadata persistence. Snapshot, debate,
and backtest require a new attributable report after attempt start and schema
validation. Replay requires a non-empty protected replay artifact tied to the
validated session. Missing, stale, ambiguous, or invalid output produces
`RESULT_VALIDATION_FAILED` even when exit code is zero.

Attribution is exact across the command/result boundary. Reports must match the
canonical job ID, attempt ID, attested backend commit, and
`research_only: true`. Report bytes remain capped at 4 MiB and the asset list is
bounded and minimally schema checked against the reviewed backend output.
Replay accepts only the exact reviewed sidecar keys: `job_id`, `attempt_id`,
`backend_commit`, `session_id`, `event_count`, and `events`. Event count is
bounded at 10,000; each event permits only sanitized type, timestamp, status,
and size metadata. Raw query, tool, prompt, response, or result content is
rejected.

The reviewed backend sidecar intentionally does not duplicate a
`research_only` field. Its writer is reachable only after the backend resolves
an attributed research-only invocation, and the exact backend fixture pins the
six-key schema above. Reports do carry and are required to prove
`research_only: true`.

## Alternatives

- Raw stdout/stderr in PostgreSQL was rejected because output is unbounded and
  may contain sensitive provider diagnostics.
- Exit-code-only success was rejected because a pipeline can exit zero without
  its required artifact.
- Client-selected output paths were rejected because they permit traversal and
  overwrite.
- Treating a discovered artifact as retry-safe was rejected when database
  finalization is uncertain.

## Safety impact

Protected permissions, fixed paths, bounds, and sanitized metadata reduce
secret exposure and storage denial of service. Freshness, schema, and hash
checks prevent stale files from proving a new job successful.

## Failure behavior

Stream overflow truncates the stored artifact without blocking pipe reads and
sets explicit metadata. Artifact write or validation failure produces a
sanitized job failure. Possible completed output with failed finalization is
`BLOCKED/RESULT_RECONCILIATION_REQUIRED`, not automatically retried.

## Rollback

Stop services and retain protected artifacts plus database references for
audit. Rollback does not ingest artifacts into PostgreSQL, delete legacy
reports, or restore `run_status.json`.
