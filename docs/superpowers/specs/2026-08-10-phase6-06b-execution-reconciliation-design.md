# Phase 6 06B — Deterministic Execution-State Reconciliation

## 1. Purpose

06B adds one pure, deterministic reconciliation boundary for the Phase 6
execution sandbox.  It decides whether the sandbox's scripted venue state,
the report-delivery state, and the caller-supplied canonical execution evidence
describe the same finite history.

The result distinguishes a settled match, an explicitly explainable pending
delivery, and an unreconcilable mismatch.  It is evidence only: it does not
change the sandbox, ledger, halt state, or any external system.

## 2. Scope Decision

06B consumes an immutable `SandboxSnapshot` and an immutable tuple of observed
execution-report envelopes.  06B extends the existing snapshot with its exact
canonical known-report inventory.  The queue remains the subset of retained
reports that has not yet been delivered.  The caller may obtain the observed
tuple by filtering an existing event-ledger read, but the 06B package receives
values only.  It never accepts a repository, datasource, client, provider, or
callback.

This permits the reconciliation rules to be source-tested with the same
in-memory fixtures as 06A.  It also keeps recovery, persistence, and external
account authority out of this packet.

06B does not implement:

- a venue, broker, account, drop-copy, clearing, or balance query;
- a repository read/write, outbox write, database migration, or snapshot save;
- a global-halt transition, alert delivery, retry, resend, cancel, repair, or
  state mutation;
- crash/restart recovery (06C);
- a paper or live adapter (06D); or
- legacy execution quarantine/deletion (06E).

Both live approvals remain false.  The package has no network, process,
thread, filesystem, service, database, provider, Nautilus, paper-adapter, or
live-provider dependency or import.

## 3. Design Choice

### 3.1 Chosen boundary: snapshot plus explicit canonical evidence

The reconciler is a pure function:

```text
SandboxSnapshot + observed EventEnvelope[OrderEvent | FillEvent] tuple
    -> SandboxReconciliationResult
```

`SandboxSnapshot` deliberately exposes separate `venue_state`,
`observed_state`, and `queued_reports` values.  It is extended with
`known_reports`: one retained, canonical envelope for every report generated
by the sandbox, including every duplicate delivery identity.  The queued
report IDs are its not-yet-delivered subset.  This lets a snapshot prove the
identity of a previously delivered original even when a later duplicate is
still queued, and lets 06B reconcile `FillEvent` values that do not change an
`OrderState`.  The explicit observed-envelope tuple supplies the independent
ledger-side evidence without expanding 06B into a ledger reader.

The alternative of directly comparing the two order states would incorrectly
flag legitimate delayed, disconnected, lost-response, and duplicate-delivery
scenarios.  The alternative of reading/replaying a repository inside 06B would
create the persistence/recovery boundary reserved for 06C.  Both are rejected.

### 3.2 Result is a fail-closed fact, not an exception for a mismatch

`MISMATCH` is a strict, immutable result with ordered per-order findings.  It
is not a successful reconciliation and grants no authority.  A later packet
may consume that fact to choose a durable halt or operator workflow.

`SandboxReconciliationError` is reserved for malformed, forged, or
non-canonical input that cannot be evaluated safely.  It must not turn a real,
well-formed state mismatch into an exception or vice versa.  Unrelated
programming errors are not caught broadly.

## 4. Public Models

The public package remains `packages.execution_sandbox`; 06B adds only strict,
frozen Pydantic values and a pure reconcile entry point.

### 4.1 Request

`SandboxReconciliationRequest` contains:

- `snapshot: SandboxSnapshot`; and
- `observed_reports: tuple[EventEnvelope[object], ...]`.

Every nested model is rebuilt as its exact concrete type.  Each envelope is
round-tripped through the existing event-ledger canonical codec.  Only exact
`OrderEvent` and `FillEvent` payloads are accepted.  The input tuple may be in
any order; its order is never treated as authority.

`SandboxKnownReport` is the strict immutable snapshot record for one generated
delivery identity: its `report_id` is unique and its envelope is an exact
canonical `OrderEvent` or `FillEvent`.  `SandboxSnapshot.known_reports` is in
generation order, retains originals and duplicates separately, and uses the
same report IDs as `queued_reports`.  Its validator requires unique known IDs,
requires every queued ID to occur exactly once in the known inventory, and
requires every known report to refer to a snapshot order.  A delivered report
is therefore a known report whose ID is absent from the queue; no separate
mutable delivery-status field is introduced.

For an observed `event_id`, repeated canonical bytes are idempotent input;
different canonical bytes for the same ID are invalid input and raise
`SandboxReconciliationError`.  A well-formed report for an unknown sandbox
order is not malformed: it produces a `MISMATCH` finding.

### 4.2 Outcome and findings

`SandboxReconciliationStatus` is closed:

- `RECONCILED` — all orders and reports settle exactly, with no report delivery
  remaining;
- `DELIVERY_PENDING` — all known evidence is valid and the only gap is one or
  more report plans retained in the snapshot queue; and
- `MISMATCH` — at least one valid fact cannot be explained by the snapshot and
  canonical evidence.

`SandboxReconciliationResult` contains the status, the snapshot logical time,
an ordered tuple of `SandboxOrderReconciliation` records, an ordered tuple of
pending report IDs, and a canonical digest.  Result order is by UUID integer
order, not caller input order.  Its digest is derived from existing canonical
model serialization, never from a JSON library default or ambient Decimal
context.

Each per-order record carries the order ID, the final observed and expected
`OrderState`, the observed and pending report IDs, and a canonical tuple of
closed reason codes.  The initial closed reason vocabulary is:

- `UNKNOWN_ORDER_REPORT`;
- `OBSERVED_ORDER_REPLAY_FAILED`;
- `OBSERVED_STATE_MISMATCH`;
- `PENDING_ORDER_REPLAY_FAILED`;
- `VENUE_STATE_MISMATCH`;
- `FILL_EVIDENCE_MISMATCH`; and
- `UNEXPECTED_OBSERVED_REPORT`.

No reason code communicates a repair instruction or makes a halt decision.

## 5. Reconciliation Algorithm

The algorithm is finite and has no ambient input.

1. Canonicalize the request, snapshot, every order snapshot, every queued
   report plan, and every observed envelope.  Reject malformed values before a
   result is created.
2. Build a canonical observed-event map keyed by `event_id`.  Same-byte repeats
   collapse idempotently; a conflicting byte representation is an invalid
   request.
3. Build the known-report map keyed by report ID.  Treat the tuple position of
   a queued report as its retained insertion ordinal, resolve each queued plan
   through that known-report map, and sort the queue by `(deliver_at, retained
   ordinal)`, exactly matching 06A delivery ordering.  A queue ID absent from
   known inventory is malformed snapshot input; 06B never invents report
   bytes.
4. Derive delivered report identities as `known_reports - queued_reports`.
   Their unique canonical event IDs are the only observed report IDs permitted
   in the evidence tuple.  A delivered duplicate therefore requires the same
   canonical event as its original but does not mint a second observed event.
5. Group canonical reports by order.  Replay unique observed `OrderEvent`
   values from a fresh existing `OrderState`, ordered by their existing order
   sequence, and require the result to equal the snapshot `observed_state`.
   The existing `reduce_order` table remains the only lifecycle authority.
6. Starting with that observed state, replay queued `OrderEvent` values in
   queue delivery order and require the result to equal the snapshot
   `venue_state`.  This explicitly models delayed or disconnected delivery.
7. Validate `FillEvent` identities, report sequences, execution identities,
   and canonical bytes against the known original/duplicate report inventory.
   A queued fill is pending evidence; a foreign, absent, conflicting, or
   otherwise unexplained observed fill is a mismatch.  06B does not derive
   order state or money from fills.
8. Aggregate canonical per-order findings.  Any finding yields `MISMATCH`.
   Otherwise, at least one queued report yields `DELIVERY_PENDING`; only an
   empty pending set yields `RECONCILED`.

The reconciliation function neither mutates input objects nor calls a client
method.  Repeating it with the same input produces byte-identical result
serialization and digest.

## 6. Authority and Isolation Boundaries

06B consumes but does not replace:

- the existing `OrderState` and `reduce_order` lifecycle reducer;
- existing `OrderEvent`, `FillEvent`, `EventEnvelope`, and canonical
  event-ledger codec contracts;
- 06A's logical clock, queue ordering, snapshot, and scenario semantics; and
- Phase 5 global-halt and submit-permit authority.

It never creates a competing lifecycle reducer, report serializer, execution
identity, financial calculation, or safety authority.  A caller must treat a
`MISMATCH` as a denial until separately authorized logic handles it; the
reconciler itself does not rotate or persist a halt.

## 7. TDD and Drill Matrix

Tests begin with the absent public reconciliation API and are written before
production code.  The required matrix covers:

- exact ACK, partial/full fill, fill-before-ACK, cancel, and modify paths;
- delayed reports, disconnect/reconnect, lost responses, and queued duplicates
  as `DELIVERY_PENDING` rather than false mismatches;
- snapshot known-report retention for delivered originals and duplicates, plus
  queue IDs that must be an exact subset of that immutable inventory;
- report absence/excess, unknown orders, changed canonical bytes under one
  `event_id`, invalid lifecycle replay, forged state, a queue ID absent from
  known inventory, and invalid fill identity/sequence as fail-closed
  mismatches or invalid requests as appropriate;
- input-order independence, canonical result bytes/digest, strict model
  reconstruction, and no mutation of snapshot/evidence values; and
- regression coverage for the existing 06A lifecycle and event-ledger
  contracts.

Tests must not use a database, service, provider, socket, process, thread,
wall clock, exchange, broker, account, private endpoint, paper adapter, or
live authority.  No test weakens skips, markers, or existing authority gates.

## 8. Acceptance Boundary

06B is complete only when the pure reconciler, its strict contracts, and its
scenario drills are source-tested, independently reviewed, and pass the
repository's applicable offline checks.  Completion does not authorize any
paper or live execution, provider startup, external account query, database
mutation, recovery, alert delivery, halt mutation, or legacy execution change.
Each later Phase 6 packet requires its own approved design, implementation
plan, review, and gate.
