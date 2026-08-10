# Phase 6 06A — Deterministic Execution Sandbox

**Status:** Approved design, pending written-spec review

**Date:** 2026-08-10

**Base:** `main` at `732c824bd5e27e253609a1990a71a1d63a45a24e`

## 1. Purpose

06A is the first WS-06 packet.  It provides a deterministic, in-memory
execution-client boundary that can exercise canonical order and fill lifecycle
behaviour without an exchange, broker, network connection, process boundary,
database, paper account, or live execution path.

It must model exactly these finite conditions:

- submit, accept, reject, partial fill, full fill, cancel, and modify;
- disconnect and reconnect;
- delayed and duplicate reports; and
- a lost command response after a scripted sandbox action.

The packet is paper-only source work.  It must not invoke a provider, a
Nautilus runtime, a paper adapter, a private endpoint, PostgreSQL, or a
service.  Both live-execution approvals remain false.

## 2. Scope Decision

06A owns only a local, deterministic venue simulation.  It deliberately does
not implement:

- 06B execution-state reconciliation;
- 06C crash recovery, database recovery, or worker leases;
- 06D paper/test-environment adapters; or
- 06E legacy execution quarantine or deletion.

The existing `OrderState`, `OrderEvent`, `FillEvent`, `EventEnvelope`,
`InMemoryEventLedger`, and Phase 5 submit-authority contracts remain the
canonical building blocks.  06A adds no alternate order reducer, no mutable
venue cache authority, and no transport abstraction that could reach a real
endpoint.

## 3. Design Choice

The accepted approach is a strict `execution_sandbox` package whose client
owns the final Phase 5 one-shot permit consumption immediately before a local
submit effect.

The rejected alternatives are:

1. **Caller consumes before sandbox entry.**  This leaves a caller-controlled
   gap between authority consumption and the submit effect.
2. **A pure reducer fixture with no client boundary.**  This cannot exercise
   command/result ambiguity, connection state, or report delivery semantics.
3. **Reusing the Nautilus runtime as the sandbox.**  That would couple this
   narrow, network-free packet to Phase 4 runtime custody and prematurely
   introduce the later paper-adapter boundary.

## 4. Authority and Isolation Boundaries

### 4.1 06A owns

- deterministic sandbox command validation and immutable local state;
- exact scripted report scheduling and delivery order;
- conversion of scripted lifecycle observations into the existing canonical
  `OrderEvent` and `FillEvent` envelopes;
- local command-result ambiguity (`lost response`) without an automatic
  resend; and
- the immediate call to `consume_submit_permit` on a valid prepared permit.

### 4.2 06A consumes but does not replace

- Phase 5 durable approval, prepared-permit, consumed-permit, halt, safety,
  runtime-policy, and runtime-observation authority;
- the append-only event-ledger interface for hermetic event capture; and
- the canonical domain order and fill models and their reducers.

### 4.3 06A must not own

- a real transport, socket, HTTP client, subprocess, thread, service, or
  provider;
- reconciliation queries, venue discovery, position or balance repair;
- retry/re-send policy for an ambiguous submission;
- durable crash recovery; or
- paper/live account identity, credentials, endpoints, or feature flags.

The implementation and tests must demonstrate that the package imports none
of the network, process, database, paper-adapter, or live-provider paths.

## 5. Public Boundary

The public package is `packages.execution_sandbox`.  It is a pure Python,
in-memory package with strict, frozen Pydantic request/result/scenario models
and an explicit injected logical clock.

### 5.1 Client operations

`SandboxExecutionClient` exposes only:

- `submit(...)`;
- `modify(...)`;
- `cancel(...)`;
- `disconnect(...)`;
- `reconnect(...)`;
- `advance_time(...)`; and
- `drain_reports(...)`.

`snapshot()` returns a strict immutable inspection value for tests and later
reconciliation integration.  It is not a venue query API.

`submit(...)` accepts the Phase 5 prepared permit, current observation,
policy, safety observation, and trusted safety verifier required by
`consume_submit_permit`.  It validates all local command and scenario material
first, consumes the exact permit, and only then applies the sandbox submit.
The resulting consumed authority is retained in the submission result.

If the permit consumption fails, no sandbox order, report, or response is
created.  A consumed permit is never accepted again.

### 5.2 Commands and outcomes

All client operations use concrete command models.  A closed
`SandboxScenario` declares each permitted deterministic outcome in advance;
the client never derives price, liquidity, timing, or failure behaviour from
ambient state.

The finite scenario vocabulary includes:

- accept and reject;
- partial fill and full fill;
- cancel acknowledgment and modify acknowledgment;
- disconnect and reconnect;
- delay one report until a supplied logical timestamp;
- deliver one exact previously generated report a second time; and
- lose the response to the current command after its declared local action.

Reject, cancel, and other reason-bearing observations use existing canonical
reason-code rules.  Fill reports carry existing instrument, quantity, price,
commission, reconciliation-source, execution identity, and report-sequence
requirements.  Scenario data contains exact decimal domain objects, never
binary floats.

### 5.3 Results and errors

Results distinguish an observed acknowledgment/rejection from a
`SandboxLostResponse`.  A lost response is not a failed permit consumption and
does not authorize the client to resend the command.  Any later report can be
obtained only through the declared report queue after reconnect/time advance.

Malformed scenario material, invalid lifecycle edges, invalid IDs, stale or
failed authority, unexpected report content, or commands while disconnected
raise one bounded sandbox-domain error before a new effect.  The package does
not translate unrelated programming errors into a successful or ambiguous
result.

## 6. State Machine and Delivery Semantics

The sandbox snapshot contains only immutable, replayable data:

- connection state (`CONNECTED` or `DISCONNECTED`);
- the canonical existing `OrderState` for both each order's scripted venue
  state and its separately observed/report-delivery state, plus the current
  accepted `OrderIntent`;
- exact known execution-report identities;
- a report queue ordered by `(deliver_at, insertion_ordinal)`; and
- monotonically increasing per-order lifecycle and fill-report sequences.

The clock is supplied by the caller.  It is UTC and monotonic: advancing to an
earlier logical time is rejected.  No operation reads wall-clock time.

The existing `reduce_order` table remains the lifecycle authority.  In
particular it permits a fill observed before an ACK and preserves explicit
cancel/fill and modify/fill ordering.  A cancel or modify race is represented
only by the scenario's ordered actions; 06A creates no thread or concurrent
worker.

Reports are first created as canonical `EventEnvelope[OrderEvent]` or
`EventEnvelope[FillEvent]` values.  A scripted action reduces only the venue
state and queues the corresponding report.  `drain_reports(...)` reduces the
observed state, writes to the supplied `EventLedgerRepository` in hermetic
tests, and requires its exact canonical idempotency behaviour.  A duplicate
report re-delivers the exact original envelope/event ID; it does not mint a
new sequence or execution ID.  An already-recorded identical delivery is
therefore idempotent, while altered content with an existing ID fails closed.

`disconnect` prevents new submit, modify, and cancel commands.  It neither
changes an order nor invents a report.  `reconnect` restores only report
delivery.  Delayed reports remain queued until both their logical timestamp
has arrived and delivery is connected.

## 7. Submit and Lost-Response Rule

The ordered submit path is deliberately narrow:

1. canonically revalidate the command, scenario, order intent, and Phase 5
   authority inputs;
2. ensure the client is connected and the client-order identity is unused;
3. call Phase 5 `consume_submit_permit` with the supplied trusted safety
   verifier and current facts;
4. apply the declared action to venue state, enqueue its canonical reports,
   and retain the returned `ConsumedSubmitAuthority`; and
5. either return the declared response or raise `SandboxLostResponse` after
   the declared action.

There is no implicit retry at any point.  A caller that receives a lost
response must retain the original client order ID and observe the sandbox's
scheduled reports; 06B and 06C will later define cross-process reconciliation
and crash recovery rather than extending 06A's local behaviour.

## 8. Invariants

- A submit cannot create any sandbox effect without one successful Phase 5
  consumption event.
- The same prepared permit cannot be used twice, including after a lost
  response.
- Every emitted report is a concrete canonical existing domain payload in a
  correctly typed event envelope.
- A delayed, disconnected, or lost-response report may advance venue state but
  cannot advance observed state until the exact queued report is delivered.
- Order-event and fill-report identities, sequences, quantities, and lifecycle
  transitions are exact; duplicate delivery preserves bytes and identity.
- State, scenarios, results, and snapshots are immutable and replayable from
  the same command sequence and logical timestamps.
- The scenario completely determines acceptance, rejection, fills, response
  loss, and delivery timing.  There is no random source, ambient Decimal
  context dependence, socket, process, database, or real clock.
- A disconnected client cannot be used as a backdoor to submit, modify, or
  cancel; reconnect never repairs or reinterprets state.

## 9. Test and Drill Matrix

Tests are written before production code and cover at minimum:

- submit success, accept, reject, partial fill, and full fill;
- exact one-shot permit consumption and failure before any local effect;
- fill before ACK where the existing lifecycle table permits it;
- cancel/fill and modify/fill race orderings;
- disconnected command rejection and reconnect delivery;
- delayed reports, byte-identical duplicate reports, and lost response with no
  automatic resend;
- invalid scenarios, ID/sequence conflicts, altered duplicates, and forbidden
  lifecycle transitions;
- stable snapshots and canonical report bytes across repeated equivalent
  scenario runs; and
- static and runtime boundary checks proving no network, subprocess, database,
  paper-adapter, or live-provider use.

The focused suite is supplemented by existing domain-order, event-envelope,
event-ledger, Phase 5 submit-authority, contract-generation, secret-hygiene,
and diff checks.  Generated contracts, if public strict models require them,
are produced only through the repository generator and verified with
`make check-contracts`.

## 10. Acceptance Boundary

06A is complete only when the deterministic client and its scenario drills are
fully source-tested and independently reviewed.  It does not authorize paper
execution, provider startup, any network call, reconciliation, recovery, or
legacy execution changes.  Each later WS-06 packet requires its own approved
design, implementation plan, review, and gate.
