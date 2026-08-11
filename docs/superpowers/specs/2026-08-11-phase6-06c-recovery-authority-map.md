# Phase 6 06C-0 — Recovery Authority Map

**Status:** Recovery authority inventory complete; no implementation

**Date:** 2026-08-11

**Base:** `main` at `27d8d8a90ea2b4bff6b189a5dfecc5ce6dd438ca`

## 1. Scope and vocabulary

This packet inventories current source authority. It does not add a recovery
contract, checkpoint, repository, worker state, migration, or runtime behavior.
It performs no provider, account, exchange, database, service, or live action.

In this map, **durable** means that current source defines bytes or rows in an
append-only/transactional persistence boundary. A Pydantic object returned to a
caller is not itself durable. `InMemoryEventLedger` and
`InMemoryEngineEventLedger` are process-local fakes unless an external caller
exports and retains their state; their names do not make their Python objects
durable. The domain-ledger `EventLedgerRepository` is a protocol, and this
checkout contains the PostgreSQL SQL contract but no concrete Python
PostgreSQL adapter for it. Domain-ledger durability is therefore conditional on
the injected implementation. Engine-event and job durability have concrete
PostgreSQL repository boundaries.

An **exact retry** below means replaying the same canonical identity and bytes
at an idempotent persistence boundary. It never means issuing the economic
submit again.

## 2. Existing authority stores

Current source already has three non-competing persistence authorities:

1. The domain event ledger owns canonical `SubmitPermitPrepared`,
   `SubmitPermitConsumed`, and delivered `OrderEvent`/`FillEvent` bytes plus
   their outbox intents. See
   `packages/runtime_risk/submit_authority.py:prepare_submit_permit`,
   `packages/runtime_risk/submit_authority.py:consume_submit_permit`,
   `packages/event_ledger/repository.py:EventLedgerRepository`, and
   `alembic/versions/0008_trading_domain_ledger.py:append_domain_event`.
2. The engine-event ledger owns validated engine event rows, batch receipts,
   per-job result bindings, and derived run projections. See
   `services/job_store/engine_event_repository.py:PostgresEngineEventLedger`
   and migrations `0010_engine_event_ledger` and
   `0011_engine_backtest_worker_authority`.
3. The job store owns job state, attempts, claims, lease fencing, process
   identity, and terminal transitions. See
   `services/job_store/worker_repository.py:WorkerRepository` and the
   database-owned `worker_*_paper` functions promoted by migration `0011`.

The sandbox is not a fourth durable store. Its scenario, venue simulation,
delivery queue, snapshots, and executed-command inventory live in one
`SandboxExecutionClient` instance.

## 3. Recovery authority inventory

| State | Owner | Durable? | Identity | Recovery source | Retry safe? |
|---|---|---|---|---|---|
| `SubmitPermitPrepared` event bytes | Domain event ledger; constructed by `submit_authority._prepared_event` | Conditional: durable when the injected `EventLedgerRepository` is persistent; process-local in `InMemoryEventLedger` | Event `event_id`, halt `stream_id` + contiguous `sequence`, `permit_id`, canonical event digest | `EventLedgerRepository.load_events()` followed by `halt.replay_global_halt_authority` | Exact same event/outbox is idempotent; changed identity or content must fail closed |
| `PreparedSubmitPermit` | Runtime-risk caller reference reconstructed by `halt.replay_global_halt_authority` | No as an object; its complete facts are derivable from the durable prepared event | `permit_id`, `prepared_event_id`, `prepared_event_digest`, halt generation/transition, intent and authority digests, validity interval | `GlobalHaltReplay.prepared` from the domain event stream | Permit preparation may be retried only with the exact same event ID and payload; permit consumption remains one-shot and time/current-authority bound |
| `SubmitPermitConsumed` event bytes | Domain event ledger; constructed by `submit_authority._consumed_event` | Conditional on persistent repository; process-local in `InMemoryEventLedger` | Event `event_id`, halt stream + sequence, `permit_id`, `prepared_event_digest`, canonical event digest | Domain event bytes and `replay_global_halt_authority`; replay exposes the `permit_id` in `consumed_permit_ids` | No second consumption and no economic resubmit; an exact duplicate consumption is deliberately rejected as changed/consumed authority |
| `ConsumedSubmitAuthority` | Return evidence from `submit_authority._consumed_reference` | No as a returned object; its facts exist in the consumed event, but current replay returns only consumed permit IDs rather than this full reference | `permit_id`, `consumed_event_id`, `consumed_event_digest`, prepared/halt lineage | Canonical consumed event bytes; there is no public restart accessor that reconstructs the full model | Evidence may be compared; it grants no retry or resend authority |
| `GlobalHaltReplay` permit inventories | Pure replay in `runtime_risk.halt` | No; derived from domain event bytes | Halt stream head sequence/event/digest plus prepared, consumed, and retired permit IDs | `replay_global_halt_authority` or `audit_submit_authority_stream` | Replay is read-only; malformed, reordered, duplicated, or conflicting history fails closed |
| `SandboxScenario` / `SandboxCommandPlan` | Immutable caller evidence accepted by `SandboxExecutionClient.__init__` | No; retained only on the client instance | Scenario content; each command has unique `command_id`, `kind`, `order_id`, and assigned `report_ids` | Original caller-supplied scenario only | A plan is permission to simulate, not proof of execution. Re-execution is safe only while the same client still proves the command ID unused |
| Executed command IDs | `SandboxExecutionClient._executed_command_ids` | No; private process-local tuple and absent from `SandboxSnapshot` | `SandboxCommandPlan.command_id` | Same live client instance only | No after restart. Same-process `_require_plan` rejects an executed ID, but that fact disappears with the instance |
| `SandboxOrderSnapshot` | `SandboxExecutionClient._snapshot.orders` | No; immutable process-local value | `order_id == order_intent.intent_id`, unique `client_order_id`, exact order intent, venue and observed states | `SandboxExecutionClient.snapshot()` while the instance lives | Evidence-only. It cannot authorize submit/modify/cancel retry |
| `SandboxSnapshot` | `SandboxExecutionClient._snapshot` | No; immutable process-local inspection value | Connection state, UTC logical time, unique order/client-order identities, known and queued report identities | `SandboxExecutionClient.snapshot()` while the instance lives | Safe to copy/reconcile, not to resend. Current source has no restore/checkpoint API |
| `known_reports` | Client `_retained_reports`, projected into `SandboxSnapshot.known_reports` | No; process-local canonical report inventory | Unique sandbox `report_id`; canonical event `event_id` and exact bytes; referenced order ID | Live client snapshot | Read/reconciliation safe. It does not prove durable append merely by being known |
| `queued_reports` | Client `_queued_reports`, projected into `SandboxSnapshot.queued_reports` | No; process-local undelivered subset of `known_reports` | Unique `report_id`, `deliver_at`, insertion ordinal internally, canonical event via retained report | Live client snapshot/private queue | Retrying exact report delivery can be safe against an idempotent ledger; issuing the economic command again is not safe |
| Delivered execution report (`OrderEvent` / `FillEvent`) | Domain event ledger through `SandboxExecutionClient._append_and_read_back` | Conditional on persistent domain repository; append and outbox are atomic in `append_domain_event` | Canonical envelope `event_id`, stream + sequence, canonical bytes/digest; sandbox also has separate `report_id` | `EventLedgerRepository.load_events()`; the sandbox reads back exact bytes before clearing its queue | Exact append is idempotent; conflicting bytes fail. Delivery retry is distinct from command/submit retry |
| `SandboxReconciliationRequest` / `SandboxReconciliationResult` | Caller plus pure `execution_sandbox.reconciliation.reconcile_execution_state` | No; immutable evidence/result only | Snapshot identities, canonical observed event IDs/bytes, result digest and closed status | Recompute from a retained snapshot and independently loaded report events | Pure recomputation is safe; result grants no execution authority |
| Sealed engine-result JSONL / `ValidatedEngineEventBatch` | Worker `EngineResultValidator._seal` and process-local dataclass | File bytes are fsync/rename persisted under the artifact root; the dataclass is process-local and the file is not job-result authority by itself | `job_id`, `attempt_id`, batch SHA-256, exact JSONL bytes, relative ref and validation metadata | Protected artifact path if retained; authoritative recovery uses the engine-event receipt/binding, not the file alone | Validation/sealing of identical bytes is safe; execution rerun is not implied |
| `StoredEngineEvent` rows | Engine-event ledger | Yes in PostgreSQL; process-local in the in-memory fake/export state | Globally unique `message_id`, `engine_run_id` + `stream_sequence`, canonical bytes/digest, `batch_sha256` | `PostgresEngineEventLedger.load_events`; projection replay via `project_engine_run` | Exact batch retry is idempotent; changed message bytes or sequence conflicts fail closed |
| `EngineEventBatchReceipt` | Engine-event ledger receipt table/model | Yes in `public.engine_event_batch_receipts`; process-local in the fake unless exported | `batch_sha256`, ingestion digest, job/attempt, run ID, event count/sequence range, last digest | `load_receipt(batch_sha256)` or job-bound lookup | Exact same batch and receipt authority is idempotent; any changed authority conflicts |
| `EngineJobResultBinding` | Append-only `public.engine_job_results`; in-memory `_job_results` fake | Yes in PostgreSQL; process-local in fake unless exported | One `job_id` mapped to exactly one `attempt_id` and `batch_sha256` | `load_job_receipt(job_id)` joins binding to receipt | Exact same job/attempt/batch is idempotent; a different result or attempt must fail closed |
| `EngineRunProjection` | Engine-event ledger derived projection | Yes as a PostgreSQL projection row, but authoritative/recoverable from immutable engine event rows | `engine_run_id`, event count/type counts, last sequence/digest | `load_projection`, `replay_projection`, or `recover_projections` | Rebuild is safe from exact stored events; it grants no job or submit retry |
| Job claim | PostgreSQL `jobs`, `job_attempts`, and `job_events`; returned as process-local `ClaimedJob` | Yes: claim state, attempt row, and event are one database transaction | `job_id`, new `attempt_id`/number, `worker_id`, raw lease token and expiry; event stores only token SHA-256 | Job repository rows/events; `WorkerRepository.claim_next` returns the live capability | Claim acquisition is database serialized/skip-locked. Reusing or duplicating a claim outside its exact fence is unsafe |
| Attempt identity | PostgreSQL `job_attempts`; copied into `ClaimedJob` and engine validation metadata | Yes | `attempt_<32 hex>`, job ID, attempt number, worker, outcome, process identity when started | `job_attempts` and job detail records | Never substitute another attempt. Engine result binding requires the exact current attempt |
| Lease token | PostgreSQL `jobs.lease_token` and `job_attempts.lease_token`; raw token also in `ClaimedJob` | Yes while active; cleared from the job on finalization. Job-event metadata retains only SHA-256 | Exact raw token + job/attempt/worker + unexpired timestamp | Locked job/attempt rows; no recovery from the event hash | Only exact, current, unexpired fence operations are safe. A stale/expired token cannot heartbeat, ingest, or finalize |
| Process identity | PostgreSQL `job_attempts` after `worker_start_paper` | Yes | PID, process group, start ticks, command fingerprint bound to job/attempt | `WorkerRepository.recover_expired_leases` plus `ProcessInspector` | Recovery may requeue only when database function accepts the exact locked candidate and outcome; possible result or unverifiable identity blocks |
| Final job state | PostgreSQL `jobs`, terminal `job_attempts.outcome`, and append-only `job_events` transition | Yes; `worker_finalize_paper` performs transition/result metadata and caller artifact inserts in one transaction | Job ID, attempt/fence, expected source/outcome, terminal state, reason, trace/event IDs, result hash/metadata | Job repository read model and job events | A terminal job must not be executed again. Fence loss returns false/rolls back; a response-loss question is resolved by reading durable job state |

## 4. Ordering facts that define the crash windows

The relevant current call order is exact:

1. `SandboxExecutionClient.submit` validates the request and all planned
   reports.
2. It calls `consume_submit_permit`, which appends and exactly reads back
   `SubmitPermitConsumed` before returning.
3. Only then `_apply_venue_reports_and_enqueue` computes the simulated venue
   state and `_replace_state` installs the order, queue, known reports, and
   executed command ID.
4. A scripted lost response is raised only after `_replace_state`.
5. `drain_reports` appends and reads back each due canonical report before its
   observed-state reduction. It clears the queue only in the final
   `_replace_state` after the due loop succeeds.
6. For engine jobs, the worker validates/seals stdout, calls
   `ingest_for_job`, verifies the exact receipt, rechecks safety/lease, and
   only then calls `finalize_execution(... SUCCEEDED ...)`.

These facts come from `packages/execution_sandbox/client.py:submit`,
`drain_reports`, `_append_and_read_back`, and
`services/job_worker/worker.py:JobWorker.run_once`.

## 5. Crash-window trace

### W1 — crash before permit preparation

1. **Durable bytes/facts:** Existing approval and global-halt stream facts may
   be durable; there is no `SubmitPermitPrepared` event for this permit.
2. **Process-local state:** Preparation arguments and current observation,
   policy, safety, intended IDs, and intent objects held by the caller.
3. **Safe retry:** Preparation may be attempted after loading/revalidating
   current authority. There is no evidence of an economic submit to retry.
4. **Reconciliation:** No execution reconciliation is required because no
   permit was consumed and no venue effect was attempted.
5. **Current enforcement:** `prepare_submit_permit` verifies durable approval,
   current risk/safety, active halt generation, timestamps, and stream replay
   before append.
6. **Missing:** Current source does not durably record a pre-preparation
   sandbox command/checkpoint; absence of a prepared event is the only permit
   fact.

### W2 — crash after permit prepared

1. **Durable bytes/facts:** The canonical `SubmitPermitPrepared` event and
   outbox are appended together when the repository is persistent; it binds
   permit, intent digest, approval, risk/safety, halt generation, and expiry.
2. **Process-local state:** The returned `PreparedSubmitPermit` object and any
   not-yet-created sandbox client/request.
3. **Safe retry:** Exact permit preparation using the same event ID and payload
   is idempotent. Consumption may still occur once only if replay shows the
   permit prepared and all time/current-authority checks pass.
4. **Reconciliation:** No execution reconciliation is required; no consumed
   event or venue effect exists.
5. **Current enforcement:** `_exact_existing_prepared`,
   `_append_and_read_back`, and `replay_global_halt_authority`; ambiguous
   append is covered by the exact-retry path.
6. **Missing:** No durable association exists between this permit and a
   sandbox `command_id`; recovery has permit/intent authority, not command
   progress.

### W3 — crash before permit consumption

1. **Durable bytes/facts:** The prepared event remains; replay lists the permit
   in `GlobalHaltReplay.prepared`. There is no consumed event.
2. **Process-local state:** The submit request, scenario, and pristine sandbox
   snapshot/client instance.
3. **Safe retry:** One consumption attempt is safe only after exact replay and
   fresh bounded safety/current-authority checks and only within the permit
   window. Economic submit has not yet begun.
4. **Reconciliation:** Not required when durable replay proves the permit is
   still prepared and unconsumed.
5. **Current enforcement:** `_require_exact_prepared_authority` and
   `_require_current_consumption_authority` run before consumption append.
6. **Missing:** There is no durable sandbox checkpoint proving the pristine
   scenario/snapshot; this window is distinguishable only by permit history,
   not sandbox state.

### W4 — crash after permit consumption but before venue effect

1. **Durable bytes/facts:** `SubmitPermitConsumed` is durably appended and read
   back before `consume_submit_permit` returns. Replay removes the permit from
   `prepared` and records its ID in `consumed_permit_ids`.
2. **Process-local state:** The submit call stack and the not-yet-installed
   sandbox order/queue/executed ID. At the stated point the sandbox snapshot is
   still pre-effect.
3. **Safe retry:** No economic submit retry is safe. The permit is one-shot and
   current code rejects its reuse.
4. **Reconciliation:** Required for restart recovery because durable history
   proves authority consumption but contains no durable venue-outcome fact.
5. **Current enforcement:** `consume_submit_permit` and
   `_require_exact_prepared_authority` prohibit reuse; sandbox venue mutation
   occurs only after that call returns.
6. **Missing:** No atomic custody record spans consumed permit -> command ID ->
   client order ID -> venue effect. Current source cannot durably distinguish
   this window from W5/W6 after the client process is lost.

### W5 — crash after venue effect but before response

1. **Durable bytes/facts:** The consumed event is durable. No execution report
   is durable until `drain_reports` appends it.
2. **Process-local state:** The updated `SandboxSnapshot` with venue state,
   order/client-order identity, known/queued reports, and the executed command
   ID.
3. **Safe retry:** No submit retry is safe. Same-process command duplication is
   rejected, but that guard is not restart-durable.
4. **Reconciliation:** Required; the economic effect may exist while the only
   durable execution fact is permit consumption.
5. **Current enforcement:** `_replace_state` installs the effect and executed
   ID before returning; `_require_plan` rejects the ID in the live instance;
   consumed-permit replay rejects permit reuse.
6. **Missing:** The snapshot, command-consumption fact, scenario identity, and
   queued reports are not persisted. No restore boundary exists.

### W6 — lost response after venue effect

1. **Durable bytes/facts:** The consumed event is durable. Reports become
   durable only if separately drained; the lost-response exception itself is
   not durable.
2. **Process-local state:** The full post-effect sandbox state remains installed
   before `SandboxLostResponse` is raised.
3. **Safe retry:** Resubmission is unsafe. In the same process, the executed ID
   and consumed permit both reject it.
4. **Reconciliation:** Required for ambiguous command outcome, using retained
   snapshot plus independent reports while the instance lives; after restart,
   current source lacks the snapshot needed by 06B.
5. **Current enforcement:** `submit` calls `_replace_state` before raising
   `SandboxLostResponse`; tests prove a second submit fails and explicit report
   delivery remains possible.
6. **Missing:** Lost-response/reconciliation-required status and the snapshot
   needed to evaluate it are not durable.

### W7 — crash before execution report durable append

1. **Durable bytes/facts:** Permit consumption may be durable; the report event
   and its sandbox outbox are not yet durable. Earlier reports from the same
   drain loop may already be durable because delivery is per report.
2. **Process-local state:** The queued report, retained canonical event, venue
   state, due selection, and any temporary `next_orders` reductions.
3. **Safe retry:** Exact report delivery is safe against an idempotent ledger;
   economic command retry is not safe.
4. **Reconciliation:** Required after restart to determine venue/delivery
   status, especially when a multi-report drain may have a durable prefix.
5. **Current enforcement:** `drain_reports` invokes `_append_and_read_back`
   before reducing/clearing each report; a failure leaves the public snapshot
   unchanged even when an earlier append in the due loop committed.
6. **Missing:** Queue progress is not transactionally coupled to the domain
   append and is not durable. There is no recovery loader that combines the
   durable report prefix with sandbox state.

### W8 — crash after report append but before read-back

1. **Durable bytes/facts:** The canonical report, append-idempotency identity,
   and outbox may already be committed atomically. Commit status must be
   resolved by reading the ledger.
2. **Process-local state:** The queue still contains the report and observed
   state has not been committed to `_snapshot` for that drain completion.
3. **Safe retry:** Retrying the exact report append/read-back is safe; the
   ledger returns an idempotent non-insert or rejects conflicting bytes. Submit
   retry remains unsafe.
4. **Reconciliation:** Required after process loss to rebuild delivery and
   economic outcome from ledger evidence; same-process read-back retry can
   settle delivery without resubmission.
5. **Current enforcement:** Domain `append_domain_event` stores event,
   idempotency row, and outbox in one function; `_append_and_read_back` demands
   exactly one byte-identical loaded event before `drain_reports` clears queue.
6. **Missing:** No durable acknowledgement connects the domain event append to
   a sandbox queued `report_id`, and current sandbox state cannot be restored.

### W9 — crash after durable engine-event ingestion

1. **Durable bytes/facts:** `ingest_engine_job_result` atomically has the
   engine events, `EngineEventBatchReceipt`, updated `EngineRunProjection`, and
   one append-only `EngineJobResultBinding` for the current job/attempt. The
   job itself can still be `RUNNING`.
2. **Process-local state:** The validated batch object, verified receipt added
   to result metadata, worker outcome, latest safety evidence, and active call
   stack before finalization.
3. **Safe retry:** Exact engine ingestion is idempotent. Re-executing the engine
   job is not safe and a new worker attempt checks the job receipt before
   spawn.
4. **Reconciliation:** Required between the durable engine result and unfinished
   job state. The presence of a bound receipt is not current authority to run
   the engine again.
5. **Current enforcement:** `JobWorker.run_once` loads a prior job receipt
   before engine execution and blocks with
   `ENGINE_EVENT_RECONCILIATION_REQUIRED`; ingest errors are followed by
   `load_job_receipt`; identity conflicts fail closed.
6. **Missing:** Current worker recovery does not promote the already-bound
   receipt into the final job success transition. It prevents rerun by
   blocking, leaving finalization reconciliation for a later packet.

### W10 — crash before final job success transition

1. **Durable bytes/facts:** The W9 engine rows/receipt/binding are durable. At
   the stated pre-transition point, job/attempt rows remain nonterminal and no
   `SUCCEEDED` job event/result metadata is durable. If a finalize transaction
   instead committed before response loss, the job row/event are the recovery
   source because finalization is atomic.
2. **Process-local state:** The `ValidatedEngineEventBatch` decorated with the
   receipt, `ProcessOutcome`, result metadata, and the boolean finalization
   response not yet obtained.
3. **Safe retry:** It is safe to read/reconcile the exact bound receipt and
   durable job state. It is not safe to rerun engine execution. A finalization
   call is safe only under the exact still-current job/attempt/worker/lease
   fence and expected source state.
4. **Reconciliation:** Required when the receipt exists but success does not;
   read-back alone resolves a commit/response ambiguity for an already-run
   finalization transaction.
5. **Current enforcement:** Success is called only after exact receipt
   verification plus final safety/lease progress. Database-owned
   `worker_finalize_paper` validates the full fence and writes the terminal job
   state, attempt outcome, and job event. `WorkerRepository.finalize` calls that
   function and then inserts the caller-owned `job_artifacts` rows inside the
   same enclosing `connection.transaction()`, so fence loss or an artifact
   insert failure rolls back the combined repository operation.
6. **Missing:** No explicit restart path owns receipt-to-final-state completion.
   The pre-spawn receipt guard blocks a new attempt rather than completing or
   canonically classifying the prior result.

## 6. Explicit unknowns and missing custody facts

- No current source contract durably binds `permit_id`, sandbox `command_id`,
  `order_id`/`client_order_id`, scenario identity, and executed status into one
  recovery fact.
- No current source persists `SandboxSnapshot`, `known_reports`,
  `queued_reports`, logical time, or executed command IDs, and there is no
  `SandboxExecutionClient.recover` boundary.
- `ConsumedSubmitAuthority` is returned after exact read-back, but current
  replay exposes only consumed permit IDs. The canonical consumed event remains
  the durable fact.
- Domain report append and sandbox queue advancement are not one atomic unit.
  A durable prefix with an unchanged process-local snapshot is possible.
- 06B reconciliation needs both a snapshot and observed report bytes. Only the
  report bytes can currently survive a real process restart through an
  injected persistent domain ledger.
- Current source has no provider/venue/account query authority. Therefore W4,
  W5, and W6 cannot be distinguished by an external venue fact in this packet.
- Engine receipt/job binding authority is durable and already prevents rerun,
  but current source has no restart owner that converts an accepted prior
  receipt into final job success.
- The domain repository protocol does not itself prove persistence. Any 06C
  recovery claim must identify the actual persistent implementation rather
  than treating `InMemoryEventLedger` as restart authority.

## 7. 06C ownership conclusion

The existing durable stores are sufficient as authorities and must not be
duplicated: permit and canonical execution-report facts belong in the domain
event ledger; validated engine result facts belong in the engine-event ledger;
job lifecycle/fencing belongs in the job store. The unresolved object is not a
new authority database. It is the missing durable custody linkage for the
currently process-local sandbox state and the missing reconciliation path from
an existing engine job receipt to final job state.

This map does not select the 06C-1 contract shape or implement later packets.
It establishes the fail-closed rule current evidence already requires:

```text
consumed permit + unknown economic outcome
= reconciliation required
!= automatic submit retry
```
