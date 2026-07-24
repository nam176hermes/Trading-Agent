# PostgreSQL recovery approval preparation review

## Decision boundary

The JSON-compatible YAML files in `ops/postgres` are preparation records only.
They cannot become an executable approval by editing a status value: the schema
permits only `DRAFT_NOT_AUTHORIZED`, and the validator's default mode always
returns nonzero. It has no transcript renderer or authorization command.

The executable `APPROVAL_RECORD` defined by the preservation/recovery runbook
remains a separate canonical, regular, non-symlink, mode 0600 file containing a
literal-TAB transcript. That transcript has no header, comments, blank lines,
duplicates, extras, placeholders, shell quoting, password, or DSN. YAML must
never be supplied as that executable record.

The preparation envelope records exactly 50 ordered transcript fields, copied
without additions from runbook Section 4. The schema exposes the order in
`x-runbook-transcript-order`, requires exactly those 50 names, and forbids
additional transcript properties. The validator independently compares that
list with both the Section 4 fenced transcript and the Section 5.1
`required_keys` array, then enforces the raw JSON member order. This closes the
ordering gap created by the runbook shell parser's associative array without
changing the reviewed runbook.

Evidence age, safety baseline, expected pre-recovery values, stale-PID
classification, recovery-review outcomes, procedure references,
backup/restore controls, target, preflight, and integrity information all sit
outside the transcript. Every object has a closed property set, so none of
that metadata silently becomes a 51st transcript field.

## Evidence age and current-review boundary

The populated safety facts are explicitly historical. Paper requested/effective
mode, inactive kill switch, and the 30-order/0-trade counts were observed at
`2026-07-11T23:46:34Z` in the Phase 3B pre-change checkpoint. Inactive Job API,
worker, and scheduler states, disabled scheduler timer, and closed port 8401
were observed at `2026-07-16T15:12:29Z` in the Job Plane source verification.
The database incident classification is dated `2026-07-16` and references the
runbook's known-incident and zero-write sections. The envelope labels these
facts `HISTORICAL_READ_ONLY_NOT_CURRENT`; it does not present them as current
service, process, port, or database observations.

The expected pre-recovery group records gates to be checked, not observed
database state: Alembic head 0003 or 0004, 43,055 canonical rows, 222 quarantine
rows, and zero rows in each of the six Phase-4 job tables. Current stale-PID
revalidation; postmaster, process identity, port 55432, data-directory,
independent-disk, and recovery-log outcomes; and backup-permission, isolated
restore, and stop-condition review outcomes remain
`REQUIRES_REVIEWER_INPUT` in both committed records.

Procedure and control fields contain stable runbook section references only.
They contain no copied command text, credentials, claimed execution, simulated
review, or authority. `execution_status` is fixed to `NOT_EXECUTED`, the PID
file action is `PRESERVE_UNCHANGED`, and rollback remains a reference rather
than an action.

## Review and identity model

Dual review means the named OPERATOR plus one different named REVIEWER. There
is no second reviewer field and no signature field. The parser validates
declared content, ordering, time, integrity, and target bindings. The
authenticated change control system establishes who entered and reviewed the record; it is the
identity authority. A SHA-256 digest detects byte changes but is not a
cryptographic signature and does not authenticate a person.

The committed example and DATA-001 draft do not simulate approval. Decision,
operator/reviewer identities, both attestations, all approval gates, hashes,
source commit/tree, original and isolated identities, destination paths, the
current approval window, current revalidation, and every current recovery
outcome remain exactly `REQUIRES_REVIEWER_INPUT`. Only stable runbook
constants, dated historical safety facts, and expected pre-recovery gates are
populated.

## Deterministic format and use

The `.yaml` files use JSON syntax, which is a restricted deterministic subset
of YAML 1.2. The validator is stdlib-only. It rejects duplicate mapping keys,
NaN/Infinity, ordinary YAML, tags, anchors, aliases, merge keys, multi-document
input, missing/unknown fields, non-string values, and reordered transcript
members before any readiness checks.

Schema-only preparation review is explicit:

```bash
python3 scripts/validate_postgres_recovery_approval.py \
  --schema-only ops/postgres/postgres-recovery-approval-record.example.yaml
```

A successful schema-only check prints `NON-AUTHORIZING` and reminds the caller
that YAML is never an executable `APPROVAL_RECORD`. Omitting `--schema-only`
runs the additional fail-closed readiness checks, but still ends with
`YAML_PREPARATION_ONLY` and a nonzero exit even when every supplied fact is
internally consistent.

There is deliberately no caller-controlled clock. The validator reads only the
record, schema, canonical source-repository runbook, and explicitly declared
review artifacts needed for hash/Git/identity comparisons. Operational
completeness checks require the caller to supply `--trusted-evidence-root`
independently; it must exactly match `EVIDENCE_PARENT`, be owned by the current
user, and have mode 0700. The validator opens that root once without following
symlinks and reads all three evidence files relative to the pinned descriptor.
Each must be an owned, single-link regular file, and the change-control export
must also have mode 0600.

Record, schema, and runbook bytes are loaded through no-follow file descriptors
and their pathname component/file identities are re-traversed after each read.
The source repository is likewise pinned by descriptor. The migration and
runbook worktree bytes must match regular blobs in the declared Git tree, so
ignored, `skip-worktree`, alternate-path, and caller-selected runbook copies do
not satisfy the binding. Path fields permit printable ASCII only. The validator
never reads `ISO_ADMIN_ENV`, never inspects PGDATA, never reads a password or
DSN, never probes a service or port, never opens a network connection, never
changes the input, and never starts, recovers, stops, or configures PostgreSQL.

## Fail-closed checks

Preparation shape checks cover exact count/order, missing or unknown members,
duplicates, deterministic syntax, string-only leaves, stable envelope values,
secret/DSN patterns, and destructive or prohibited command text. Readiness
checks additionally cover:

- all placeholders and exact decision, attestation, catalog, policy, and `YES`
  constants;
- strict UTC timestamps, future or expired approval, positive windows no
  longer than four hours, review inside the same window, and no future-dated
  review/current revalidation;
- distinct operator/reviewer and original/isolated system identities;
- fixed PostgreSQL 16 cluster, loopback host, port 55432, database, and PGDATA;
- paper-only mode, both live gates false, complete baseline, all listed units
  inactive, and the original port baseline safe;
- explicit evidence age, fixed historical safety facts, expected Alembic/count
  gates, stale-PID classification and runbook references, current recovery
  outcomes, exact procedure references, and backup/restore controls;
- canonical transcript digest over exactly `key<TAB>value<LF>` for all 50
  ordered fields;
- canonical repository runbook, source commit/tree, committed migration and
  runbook blobs, expected catalog, authenticated change artifact, and reviewed
  original-identity evidence bindings; and
- original link counts and PGDATA fingerprint, isolated port/database/socket,
  and the other Section 5.1 constraints.

Tests create only disposable directories, Git repositories, and evidence files
under pytest temporary roots. They do not use the runtime approval path,
credentials, network, services, ports, or either real PGDATA location.

## Remaining human work

An authenticated change-control review must supply and independently verify all
sentinel values. The resulting human process must create the separate protected
literal-TAB record exactly as the runbook requires; this repository provides no
automatic conversion. The dirty candidate worktree and its hashes are not
review evidence and were not copied into the draft.

RECOVERY APPROVAL STATUS: DRAFT — NOT AUTHORIZED
