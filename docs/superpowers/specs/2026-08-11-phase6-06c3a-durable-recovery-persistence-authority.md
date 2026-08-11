# Phase 6 Packet 06C-3A: Durable Recovery Persistence Authority

**Status:** design complete; no persistence implementation or runtime activation

**Date:** 2026-08-11

**Base commit:** `97ac394270d6a5d773846f0874f4c3abfd0ade0d`

**Branch:** `codex/phase6-06c3a-persistence-authority-design`

## 1. Decision

Persist `SandboxRecoveryCheckpoint` as one registered event type in the existing
domain event ledger. A sandbox recovery session owns one ledger stream and may
append multiple immutable checkpoints to it. The latest checkpoint is the
highest contiguous, fully validated sequence in that stream.

The durable identifier rules are:

- `recovery_session_id` is a newly generated exact UUID for one sandbox
  execution/recovery lifetime. It is both the domain event `stream_id` and an
  explicitly repeated field in the checkpoint-record payload. It is never
  derived from a scenario digest, job ID, attempt ID, permit ID, halt ID, order
  ID, or checkpoint ID, and it is never reused for a later sandbox lifetime.
- `checkpoint_id` is the domain event `event_id`. The checkpoint-record
  payload and embedded canonical checkpoint must repeat that same value. One
  checkpoint ID therefore cannot move between streams or name different
  bytes.
- Domain event `sequence` is the only checkpoint ordering authority. A
  separate generation counter is not required. `created_at` is descriptive
  evidence and a monotonicity check, never latest-selection authority.
- No `prior_checkpoint_digest` field is required. The ledger's immutable event
  identity, exact append request digest, per-stream lock, contiguous sequence,
  and full-stream digest/read validation already provide the required chain.
  A second chain would create another value that could disagree without
  closing a safety gap.

This is the safe Packet 06C0 conclusion: it adds a recovery record inside the
existing execution-authority ledger, not a fourth execution authority. A
consumed permit with no corresponding checkpoint remains
`RECONCILIATION_REQUIRED`; persistence must never infer permission to retry an
economic command.

## 2. Scope and authority boundary

This design answers only where and how recovery checkpoints must be recorded
durably. It does not implement a repository, event payload, migration, restore
path, or service wiring. It does not activate PostgreSQL roles, publishers, or
live execution.

The Packet 06C0 authority split remains unchanged:

- the domain ledger owns submit permit custody, canonical execution reports,
  and now sandbox recovery checkpoint records;
- the engine event ledger owns validated engine result batches and projections;
- the job store owns job, attempt, lease, artifact, retry, and finalization
  lifecycle;
- a checkpoint is evidence for fail-closed restore and reconciliation, never a
  permit, approval, order instruction, or authorization to submit.

## 3. Source evidence

### 3.1 Domain event ledger

- `packages/event_ledger/repository.py:21-27` defines the general
  `EventLedgerRepository` protocol: event/outbox append, replay load, inbox
  claim, outbox acknowledgement, and aggregate snapshot save/load.
- `packages/event_ledger/repository.py:30-139` provides only the in-memory
  implementation. `packages/event_ledger/repository.py:141-162` exposes SQL
  text as `PostgresLedgerSql`; there is no concrete general PostgreSQL
  `EventLedgerRepository` adapter.
- `packages/domain/events.py:44-61` uses an explicit payload-to-event-type
  registry. `packages/domain/events.py:64-78` and `packages/domain/events.py:188-211`
  reject unregistered or mismatched payload types and rebuild the concrete
  payload before canonical egress.
- `alembic/versions/0008_trading_domain_ledger.py:169-181` makes `event_id` a
  global primary key, makes `(stream_id, sequence)` unique, stores canonical
  event text and its digest, and indexes stream order. The event-type column is
  constrained as non-empty text, not as a closed database enum.
- `alembic/versions/0008_trading_domain_ledger.py:238-332` locks event identity
  and stream identity, validates canonical envelope metadata, retains an exact
  append-request digest, requires the next contiguous sequence, and inserts
  the domain event, append-idempotency receipt, and outbox row together.
- `docs/adr/0003-event-ledger-delivery-durability.md:18-72` defines the outbox
  as delivery state coupled to event append, while retained append identity is
  independent of outbox acknowledgement.
- `packages/event_ledger/models.py:109-140` caps outbox payload JSON at 65,536
  characters and requires exact canonical JSON. The durable event itself has
  no corresponding model-level 65,536-character limit.

These contracts already provide the correct identity, ordering, immutable
history, exact retry, and delivery coupling for a recovery record.

### 3.2 Aggregate replay snapshots

- `packages/event_ledger/models.py:95-126` defines `SnapshotRecord` as the
  fixed aggregate replay state, status, issues, canonical replay JSON, and
  state hash.
- `alembic/versions/0008_trading_domain_ledger.py:218-236` keys
  `aggregate_snapshots` solely by content hash and fixes its schemas to
  `event-ledger-replay-v1` and `event-ledger-reducer-v1`.
- `packages/event_ledger/repository.py:119-139` saves and loads those snapshots
  by `state_hash`, not by execution session or ordered checkpoint stream.

That surface is a content-addressed cache of whole-ledger reducer output. It
has no recovery session, per-session sequence, `created_at`, checkpoint
identity, executed-command custody, or latest-checkpoint rule. Reusing it would
weaken both meanings and is rejected.

### 3.3 Sandbox recovery and report delivery

- `packages/execution_sandbox/recovery.py:150-159` defines the approved
  `SandboxRecoveryCheckpoint`: checkpoint ID, scenario digest, snapshot,
  ordered executed command IDs, submit custodies, creation time, and fixed
  schema version.
- `packages/execution_sandbox/recovery.py:166-215` revalidates concrete tuple
  contents, uniqueness, and custody membership in executed command history.
- `packages/execution_sandbox/client.py:119-206` consumes and reads back submit
  authority before committing sandbox command effects, and may then model a
  lost response.
- `packages/runtime_risk/submit_authority.py:521-789` reloads and corroborates
  the exact prepared authority, appends consumed authority, requires a unique
  byte-identical read-back, and proves the permit is durably consumed in replay
  before returning the consumption reference.
- `packages/execution_sandbox/client.py:224-259` drains due reports in stable
  delivery order, appends each report durably before reducing observed state,
  and removes queued reports only after the complete due loop.
- `packages/execution_sandbox/client.py:543-566` requires an exact report append
  outcome and exact canonical read-back.

A crash may therefore leave a durable report prefix newer than the most recent
checkpoint. That is an expected reconcilable prefix, not proof that an
economic command should be resubmitted.

### 3.4 Engine event ledger

- `packages/engine_event_ledger/models.py:35-94` binds stored messages,
  receipts, and job results to `engine_run_id`, stream sequence, `job_id`, and
  `attempt_id`.
- `services/job_store/engine_event_repository.py:248-263` defines the
  engine-specific repository contract; `services/job_store/engine_event_repository.py:572-605`
  exposes engine-result ingestion and engine-run projection reads; the
  concrete PostgreSQL adapter follows in the same module.
- `alembic/versions/0010_engine_event_ledger.py` establishes append-only engine
  result/projection storage. `alembic/versions/0011_engine_backtest_worker_authority.py:138-258`
  binds accepted engine batches to a claimed job attempt and result receipt.

Sandbox recovery state is not engine stdout, an engine result batch, or an
engine projection. Putting it here would bypass the domain permit/report
authority and incorrectly require a job-attempt binding. It is rejected.

### 3.5 Job store

- `alembic/versions/0004_durable_research_jobs.py` defines jobs, attempts,
  leases, events, artifacts, and workers around retryable research jobs.
- `alembic/versions/0007_job_event_chain_authority.py` validates a contiguous
  job lifecycle state-transition chain, including attempt and retry epochs.
- `services/job_store/worker_repository.py:88-668` implements claim, lease,
  child-process, finalization, artifact, and orphan-recovery operations.

A sandbox recovery session is not necessarily one job attempt, and its permit
and report custody cannot become retryable job metadata or an artifact. Job
events order lifecycle transitions, not sandbox execution snapshots. The job
store is rejected.

## 4. Persistence option comparison

| Candidate | Identity and ordering fit | Authority fit | Decision |
|---|---|---|---|
| Domain event ledger | Global event identity, unique contiguous stream sequence, canonical digest, exact append retry, immutable replay | Already owns permit custody and canonical execution reports | **Selected** |
| `aggregate_snapshots` | Hash-addressed replay cache; no session stream or latest rule | Reducer cache, not execution custody | Rejected |
| Engine event ledger | Engine-run and job-attempt ordering | Validated engine stdout/results only | Rejected |
| Job store | Job/attempt/retry transition sequence | Job lifecycle and artifacts only | Rejected |
| New recovery table/store | Could be tailored, but would duplicate identity, ordering, repository, ACL, and outbox rules | Creates a new execution-state authority contrary to 06C0 | Rejected |

The selected design is based on authority and failure safety, not code size.
The absence of a concrete domain-ledger PostgreSQL adapter is an implementation
gap to close, not a reason to choose an authority with the wrong semantics.

## 5. Durable checkpoint record

Packet 06C-3B may introduce exactly one registered domain payload type,
`SandboxRecoveryCheckpointRecorded`, subject to its own review. The record must
contain:

- the exact `recovery_session_id` UUID;
- the exact `checkpoint_id` UUID;
- the canonical checkpoint JSON produced from a freshly revalidated
  `SandboxRecoveryCheckpoint`;
- the SHA-256 digest of that canonical checkpoint JSON;
- the embedded checkpoint schema version.

The corresponding event envelope must satisfy all of these cross-checks:

- `stream_id == recovery_session_id` in the payload;
- `event_id == checkpoint_id` in the payload;
- `event_id == checkpoint_id` inside the decoded checkpoint;
- the decoded checkpoint is an exact strict
  `SandboxRecoveryCheckpoint`, its schema is
  `sandbox-recovery-checkpoint-v1`, reserialization reproduces the stored
  canonical checkpoint JSON byte for byte, and its recomputed digest matches;
- the event type is the one registered type, with a fixed recovery source and
  event schema version.

The wrapper keeps the domain event registry independent of the sandbox
implementation module while preserving the exact approved checkpoint bytes.
Packet 06C-3B must choose a dependency direction that does not make
`packages.domain` import `packages.execution_sandbox`; a circular import or a
second looser checkpoint model is not acceptable.

The domain event row is checkpoint truth. The aggregate replay snapshot, job
metadata, artifact references, logs, outbox rows, and consumer inbox rows are
not alternate checkpoint copies or read authorities.

## 6. Session creation and discovery

The component that creates a new sandbox execution must generate one exact
UUID as `recovery_session_id` before its first checkpoint append and retain it
as the explicit recovery handle for that lifetime. The first successful
checkpoint event durably persists the handle both in `stream_id` and the
record payload.

Recovery must be invoked with the expected session ID. It must never search
for a globally newest checkpoint, guess from a scenario digest, or substitute
a job/attempt ID. If a crash occurs before the first checkpoint is durable, or
the expected session ID is unavailable, there is no restorable checkpoint;
the result is fail-closed reconciliation or operator intervention.

A scenario digest can repeat across independent runs and identifies scenario
content, not execution custody. A job can be retried across attempts, while a
sandbox execution's economic history cannot be treated as retryable. Those
identities must remain separate.

## 7. Append contract

Before append, the persistence layer must freshly revalidate the checkpoint,
canonicalize it, recompute its digest, validate the exact session UUID, and
load and validate the current session stream. If the checkpoint ID already
exists, only an exact event-and-outbox retry may return it. Otherwise the layer
proposes the next contiguous domain sequence and delegates the write to the
general `EventLedgerRepository`.

For the first checkpoint, the stream must be absent and the sequence must be
one. For every successor, persistence must validate at least these monotonic
conditions against the current validated checkpoint:

- the recovery session and scenario digest are unchanged;
- checkpoint IDs differ;
- `snapshot.current_time` and `created_at` do not move backwards;
- prior `executed_command_ids` are an exact ordered prefix of the successor;
- prior `submit_custodies` are an exact ordered prefix of the successor and
  each retained custody is byte-identical;
- identities already represented in the prior snapshot do not change their
  immutable meaning.

The persistence layer must not attempt to prove that a snapshot authorizes a
submit. Semantic reconciliation remains the restore planner's job.

Append is successful only after exact durable read-back validates the stored
event envelope, wrapper, canonical checkpoint bytes, and digest. A returned
row count or outbox presence alone is insufficient.

### 7.1 Idempotency and concurrency

- Retrying the same checkpoint ID with exactly the same event and outbox bytes
  returns the existing record as an exact retry.
- Reusing the same checkpoint ID with any changed session, sequence, envelope,
  checkpoint, digest, topic, or outbox payload is a hard conflict.
- Concurrent writers must not silently resequence a stale checkpoint. The
  domain ledger stream lock allows only one proposed next sequence. A loser
  with the same checkpoint ID and request bytes converges through the ledger's
  exact-retry result. A loser with a different checkpoint ID reports a
  stale/conflicting append and requires the caller to rebuild from durable
  state; it does not append the stale candidate at the next sequence.
- A retry must retain its original checkpoint ID. Generating a fresh ID is a
  new record proposal, not idempotency, even when the proposed state otherwise
  describes the same moment.

## 8. Read and latest-checkpoint contract

The repository must read one explicitly named stream in ascending sequence.
The persistence layer must validate the complete returned stream before
selecting a checkpoint. It must reject, rather than skip:

- a missing, duplicate, regressing, or out-of-order sequence;
- an unexpected event type in the recovery session stream;
- an unregistered payload, non-canonical event, or event digest mismatch;
- any mismatch among stream/session ID, event/checkpoint ID, wrapper fields,
  embedded checkpoint, schema versions, canonical bytes, and checkpoint digest;
- a scenario change or successor monotonicity violation;
- a read that cannot prove it saw the complete ordered stream requested.

Only after that validation is the event with the highest sequence the latest
checkpoint. `created_at`, checkpoint UUID lexical order, scenario digest, job
time, engine time, database transaction time, and outbox publication time must
never choose latest.

Multiple checkpoints are required behavior, not an edge case. The stream is
an immutable progression; older checkpoints remain audit evidence and are not
updated or deleted. Restore uses the validated latest checkpoint plus durable
permit/report evidence. It may not fall back to an older checkpoint merely
because the latest fails validation.

## 9. Repository decision

A concrete, general PostgreSQL implementation of `EventLedgerRepository` is
required before checkpoint persistence can be operational. A
checkpoint-specific SQL repository is rejected.

The general adapter must preserve protocol parity with the in-memory ledger
and migration contract, including:

- canonical event/outbox append with exact retry versus conflict semantics;
- ordered event or stream reads that reconstruct registered concrete payloads
  and revalidate stored canonical bytes and digests;
- inbox claim and publication acknowledgement behavior;
- aggregate replay snapshot operations for their existing purpose;
- transaction/error translation that never converts a sequence or identity
  conflict into success.

The existing protocol exposes only an all-events `load_events` operation. A
bounded stream-read capability may be added to the general protocol/adapter if
06C-3B proves it necessary for complete, efficient latest selection. It must
retain the same strict reconstruction rules and must not become a
checkpoint-only database API.

Permit, execution-report, and checkpoint paths must use the same durable
adapter once activated. Continuing to accept `InMemoryEventLedger` in tests is
appropriate; treating it as restart authority is not.

## 10. Migration and activation decision

No checkpoint table or checkpoint-schema migration is required. Migration
`0008_trading_domain_ledger` already stores canonical registered domain event
types as text, exact event identities, per-stream sequence, retained append
idempotency, and atomic outbox work. Adding the registered payload and general
adapter is application behavior, not a new storage authority.

Operational activation is a separate issue. Lines 719-736 of migration `0008`
revoke public access to all ledger tables and functions, and the migration
does not grant a runtime role. The ADR also states that runtime authority is
not activated. If a non-owner service must use the ledger, a separately
reviewed, forward-only ACL activation migration is required, with disposable
PostgreSQL proof and least-privilege grants. That migration must not be hidden
inside checkpoint behavior work.

Because the current canonical revision also includes the later job and engine
authority migrations, any ACL activation must be based on and reviewed against
the then-current revision. Packet 06C-3A neither creates nor authorizes it.

## 11. Outbox decision

Every checkpoint event append should include one compact canonical outbox
intent containing only recovery session ID, checkpoint ID, checkpoint digest,
and schema/event identity needed for audit delivery. This preserves the
ledger's event-plus-delivery contract.

The outbox must not contain a second full copy of the checkpoint. Duplicating
checkpoint bytes would create two values to compare, consume the bounded
65,536-character outbox surface, and confuse delivery data with recovery
authority. Outbox publication or acknowledgement is not required to read or
restore a checkpoint; a pending outbox row means audit delivery remains due.

The append request digest intentionally covers both event and outbox bytes.
Therefore changing the audit intent while reusing a checkpoint ID is a
conflicting retry, not a repair path.

## 12. Atomicity and crash-window decisions

### 12.1 Permit consumption and checkpoint

Permit consumption and checkpoint append are deliberately not one atomic
transaction. The submit path must consume and exactly read back durable permit
authority before an economic effect can be applied. Moving checkpoint creation
into that transaction would falsely imply the checkpoint proves the external
effect's outcome and would couple recovery to a larger cross-component write.

If the process crashes after permit consumption but before a checkpoint is
durable, the consumed permit remains authoritative. The outcome is
`RECONCILIATION_REQUIRED`; the command is never auto-retried, the permit is
never recreated, and an older checkpoint is not submit authority.

### 12.2 Report append and checkpoint

Each canonical report append and checkpoint append is also deliberately
separate. The sandbox already appends and exactly reads back every report
before advancing local observed state. A crash can leave one or more durable
reports beyond the latest checkpoint, but those reports form an immutable,
ordered, idempotent prefix with stable report/event and order identities.

Restore must compare that durable prefix with checkpoint queued/known report
identity and the 06C0/06C2 planner evidence. If the prefix can be proved exact,
later restore work may deterministically advance/replay it. If any identity,
ordering, payload, custody, or coverage fact is missing or conflicts, restore
returns reconciliation required. It must never compensate by resubmitting an
economic command.

This asymmetry is intentional: report/checkpoint non-atomicity is acceptable
because a durable report prefix is safely reconcilable; permit/economic-effect
uncertainty is never safely converted into a retry.

### 12.3 Other crash windows

| Crash window | Required result |
|---|---|
| Before first checkpoint append | No restorable state; fail closed |
| Event/outbox transaction commits but response is lost | Exact same-ID retry or validated stream read returns the committed checkpoint |
| Checkpoint append fails or conflicts | No local success; reload durable stream or reconcile |
| Checkpoint commits while outbox delivery is pending | Checkpoint remains readable; audit delivery remains pending |
| Latest checkpoint is corrupt, incomplete, or semantically regressive | Reject the session; do not fall back or guess |
| Consumed permit is newer than checkpoint | `RECONCILIATION_REQUIRED`; never resubmit |
| Durable report prefix is newer than checkpoint | Prove exact replay/reconciliation or fail closed |

## 13. Required proof before operational use

The implementation packets must prove, without live-provider access:

- registered record construction rejects hostile model copies, forged generic
  envelopes, non-canonical checkpoint JSON, altered schema, and digest or
  identity mismatches;
- same-ID exact retry succeeds and every changed-byte same-ID retry conflicts;
- per-session sequence starts at one, has no gaps, and handles concurrent next
  appends without silent resequencing;
- multiple checkpoints select latest only after full-stream validation;
- scenario, executed-command, custody, and time regressions fail closed;
- a durable report prefix after an older checkpoint is either exactly
  reconciled or rejected;
- consumed permit plus missing/stale checkpoint never reaches automatic submit;
- the general in-memory and PostgreSQL adapters have equivalent observable
  behavior;
- any ACL migration is proven only against a disposable PostgreSQL fixture and
  does not grant broad table mutation authority.

No provider, broker, exchange, production database, protected runtime path, or
live mutation route is part of that proof.

## 14. Bounded follow-on packets

Packet 06C-3B may implement and test the registered checkpoint record,
canonical mapping, persistence service, and concrete general PostgreSQL event
ledger adapter. It must not restore runtime state or activate database roles.

If operational role access is required, a separately scoped and reviewed
migration packet must follow, with disposable PostgreSQL integration proof.
It must not be bundled into 06C-3B for convenience.

Only after persistence and any activation boundary are independently reviewed
may Packet 06C-4 implement restore wiring around the already approved pure
planner. Restore must preserve every fail-closed rule in this document.

## 15. Known concerns retained for review

- The concrete general PostgreSQL domain-ledger adapter does not exist yet.
- Migration `0008` intentionally has no runtime grants; source-level storage
  capability is not operational authority.
- The event registry dependency direction must be designed without a domain to
  execution-sandbox import cycle or a second permissive checkpoint schema.
- No globally searchable "latest recovery" shortcut is allowed; the caller
  must retain and present the exact recovery session ID.
- The existing outbox contract can retain pending audit delivery, but an
  operational publisher/role is outside this packet and must not block
  recovery reads.
- Packet 06C-4 must prove deterministic advancement of an exact durable report
  prefix. Until then, that crash window remains safely reconciliation-required,
  not automatically restorable.

## 16. Final safety conclusion

The existing domain event ledger is the sole correct durable owner for sandbox
recovery checkpoints because it already owns the evidence whose uncertainty
governs safe restore: permit custody and canonical execution reports. One
stable session stream, immutable multi-checkpoint history, exact event/checkpoint
identity, ledger sequence ordering, strict full-stream validation, and compact
outbox audit delivery are sufficient. A new table, aggregate replay snapshot,
engine record, or job artifact would create the wrong authority boundary.

Persistence records evidence; it does not erase uncertainty. Consumed permit
plus missing checkpoint always fails closed, and report/checkpoint separation
is acceptable only where the newer durable report prefix can be proved and
reconciled exactly.
